"""
Pipecat transport for Asterisk's AudioSocket protocol.

AudioSocket is a simple TCP protocol documented in the Asterisk source
(apps/app_audiosocket.c). Each message has the framing:

    [TYPE (1 byte)] [LENGTH (2 bytes, big-endian)] [PAYLOAD (LENGTH bytes)]

Message types actually seen on the wire:

    0x00  Hangup   — length 0, closes the stream
    0x01  ID       — length 16, first message from Asterisk: UUID of the call
    0x03  DTMF     — length 1, a DTMF digit as ASCII
    0x10  Audio    — length 320 bytes (20 ms of 8 kHz 16-bit mono SLIN)
    0xFF  Error    — length 1, error code

Pipecat pipelines run at 16 kHz internally (Whisper, Silero VAD). This transport
upsamples 8 → 16 kHz on the way in using a streaming soxr resampler and accepts
8 kHz bytes on the way out (via `audio_out_sample_rate=8000` — the pipeline's
built-in resampler handles the TTS → 8 kHz step).

One TCP connection == one call. The `services/receptionist/main.py` entry point
wraps an `asyncio.start_server` around this transport: each incoming connection
spawns a new Pipecat pipeline.
"""

from __future__ import annotations

import asyncio
import struct
import time
import uuid
from typing import Optional

from loguru import logger

from pipecat.audio.resamplers.soxr_stream_resampler import SOXRStreamAudioResampler
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    OutputAudioRawFrame,
    StartFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.transports.base_input import BaseInputTransport
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import BaseTransport, TransportParams


# --- AudioSocket protocol constants ---------------------------------------

KIND_HANGUP = 0x00
KIND_ID = 0x01
KIND_DTMF = 0x03
KIND_AUDIO = 0x10
KIND_ERROR = 0xFF

# Asterisk speaks SLIN at 8 kHz: 16-bit signed mono, 320 bytes per 20 ms frame.
ASTERISK_SAMPLE_RATE = 8000


class AudioSocketParams(TransportParams):
    """TransportParams tuned for AudioSocket.

    audio_in_sample_rate defaults to 16000 — the pipeline rate. The transport
    upsamples the raw 8 kHz coming from Asterisk before handing frames to VAD
    and Whisper.

    audio_out_sample_rate defaults to 8000 so Pipecat resamples TTS output
    directly to Asterisk's rate and we can write bytes straight to the socket.
    """

    audio_in_sample_rate: Optional[int] = 16000
    audio_out_sample_rate: Optional[int] = ASTERISK_SAMPLE_RATE


class _FrameStream:
    """Wraps an asyncio StreamReader/Writer with AudioSocket framing.

    Kept intentionally small: one sender and one receiver coroutine share this
    object via the input/output transport classes. No buffering on the read
    side — we hand each parsed frame straight to Pipecat.
    """

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self._reader = reader
        self._writer = writer
        self._write_lock = asyncio.Lock()

    async def read_message(self) -> tuple[int, bytes] | None:
        """Read one (kind, payload) tuple. Returns None on EOF / closed socket."""
        try:
            header = await self._reader.readexactly(3)
        except asyncio.IncompleteReadError:
            return None
        kind = header[0]
        (length,) = struct.unpack(">H", header[1:3])
        payload = await self._reader.readexactly(length) if length else b""
        return kind, payload

    async def write_message(self, kind: int, payload: bytes = b"") -> None:
        async with self._write_lock:
            self._writer.write(struct.pack(">BH", kind, len(payload)) + payload)
            try:
                await self._writer.drain()
            except ConnectionError:
                # Peer went away mid-write; downstream will see EOF and stop.
                pass

    def close(self) -> None:
        try:
            self._writer.close()
        except Exception:
            pass


# --- Input side ----------------------------------------------------------

class AudioSocketInputTransport(BaseInputTransport):
    """Reads AudioSocket frames, upsamples 8 → 16 kHz, pushes to pipeline."""

    def __init__(
        self,
        transport: "AudioSocketTransport",
        stream: _FrameStream,
        params: AudioSocketParams,
        **kwargs,
    ):
        super().__init__(params, **kwargs)
        self._transport = transport
        self._stream = stream
        self._params = params
        self._resampler = SOXRStreamAudioResampler()
        self._reader_task: Optional[asyncio.Task] = None
        self._initialized = False

    async def start(self, frame: StartFrame):
        await super().start(frame)
        if self._initialized:
            return
        self._initialized = True
        self._reader_task = self.create_task(self._reader_loop())
        await self.set_transport_ready(frame)
        self._transport._mark_pipeline_started()

    async def stop(self, frame: EndFrame):
        await super().stop(frame)
        await self._cancel_reader()

    async def cancel(self, frame: CancelFrame):
        await super().cancel(frame)
        await self._cancel_reader()

    async def cleanup(self):
        await super().cleanup()
        await self._transport.cleanup()

    async def _cancel_reader(self):
        if self._reader_task:
            await self.cancel_task(self._reader_task)
            self._reader_task = None

    async def _reader_loop(self):
        logger.info("AudioSocket reader loop started")
        try:
            while True:
                msg = await self._stream.read_message()
                if msg is None:
                    logger.info("AudioSocket peer closed the connection")
                    break

                kind, payload = msg

                if kind == KIND_AUDIO:
                    # SLIN 8 kHz mono 16-bit → 16 kHz for the pipeline.
                    pcm16k = await self._resampler.resample(
                        payload, ASTERISK_SAMPLE_RATE, self.sample_rate
                    )
                    await self.push_audio_frame(
                        InputAudioRawFrame(
                            audio=pcm16k,
                            sample_rate=self.sample_rate,
                            num_channels=1,
                        )
                    )
                elif kind == KIND_ID:
                    call_uuid = uuid.UUID(bytes=payload) if len(payload) == 16 else None
                    logger.info(f"AudioSocket call UUID: {call_uuid}")
                elif kind == KIND_HANGUP:
                    logger.info("AudioSocket HANGUP received")
                    break
                elif kind == KIND_ERROR:
                    logger.warning(f"AudioSocket ERROR: {payload!r}")
                    break
                elif kind == KIND_DTMF:
                    # DTMF passthrough isn't wired up yet; log and move on.
                    digit = payload.decode("ascii", errors="replace") if payload else ""
                    logger.debug(f"AudioSocket DTMF: {digit}")
                else:
                    logger.debug(f"AudioSocket unknown kind 0x{kind:02x} len={len(payload)}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(f"AudioSocket reader loop error: {exc!r}")
        finally:
            # Signal the output side to stop pacing and shut the pipeline down.
            await self._transport.on_peer_disconnected()


# --- Output side ---------------------------------------------------------

class AudioSocketOutputTransport(BaseOutputTransport):
    """Writes pipeline audio as AudioSocket 0x10 frames.

    `audio_out_sample_rate=8000` in the params, so frames arrive already at
    Asterisk's rate — no extra resampling needed here.
    """

    def __init__(
        self,
        transport: "AudioSocketTransport",
        stream: _FrameStream,
        params: AudioSocketParams,
        **kwargs,
    ):
        super().__init__(params, **kwargs)
        self._transport = transport
        self._stream = stream
        self._params = params
        self._initialized = False

        # Real-time pacing. Pipecat's MediaSender calls write_audio_frame() as
        # fast as it can dequeue; Asterisk plays audio at 8 kHz real time and
        # has only a small jitter buffer, so if we push the whole TTS utterance
        # in a few ms the output is garbled. We deliberately block inside
        # write_audio_frame to emit audio at playback speed.
        self._next_send_time = 0.0

    async def start(self, frame: StartFrame):
        await super().start(frame)
        if self._initialized:
            return
        self._initialized = True
        await self.set_transport_ready(frame)

    async def stop(self, frame: EndFrame):
        await super().stop(frame)
        await self._hangup()

    async def cancel(self, frame: CancelFrame):
        await super().cancel(frame)
        await self._hangup()

    async def cleanup(self):
        await super().cleanup()
        await self._transport.cleanup()

    async def write_audio_frame(self, frame: OutputAudioRawFrame) -> bool:
        # Split into 20 ms AudioSocket frames (160 samples = 320 bytes of SLIN8).
        # Matches Asterisk's native frame size; larger packets cause playback jitter.
        audio = frame.audio
        FRAME_BYTES = int(self.sample_rate * 0.02) * 2  # 320 bytes at 8 kHz
        n_frames = 0
        for i in range(0, len(audio), FRAME_BYTES):
            await self._stream.write_message(KIND_AUDIO, audio[i : i + FRAME_BYTES])
            n_frames += 1
        logger.debug(f"AudioSocket wrote {n_frames} frames ({len(audio)} bytes) @ {self.sample_rate}Hz")

        # Block until the audio we just wrote would have finished playing at
        # real time. This keeps Asterisk's buffer steady instead of receiving a
        # whole utterance in one asyncio tick.
        duration_secs = (len(audio) / 2) / self.sample_rate
        now = time.monotonic()
        if self._next_send_time < now:
            # First chunk, or we fell behind; rebase the clock.
            self._next_send_time = now + duration_secs
        else:
            sleep_for = max(0.0, self._next_send_time - now)
            self._next_send_time += duration_secs
            if sleep_for:
                await asyncio.sleep(sleep_for)
        return True

    async def _hangup(self):
        try:
            await self._stream.write_message(KIND_HANGUP)
        except Exception:
            pass
        self._stream.close()


# --- Top-level transport -------------------------------------------------

class AudioSocketTransport(BaseTransport):
    """One transport instance per Asterisk AudioSocket TCP connection.

    Usage (from a plain asyncio server handler)::

        async def handle(reader, writer):
            transport = AudioSocketTransport(reader, writer)
            # build pipeline with transport.input() / transport.output()
            # and run it; when the pipeline ends, the connection is closed.
    """

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        params: Optional[AudioSocketParams] = None,
        *,
        input_name: Optional[str] = None,
        output_name: Optional[str] = None,
    ):
        super().__init__(input_name=input_name, output_name=output_name)
        self._stream = _FrameStream(reader, writer)
        self._params = params or AudioSocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
        )
        self._input: Optional[AudioSocketInputTransport] = None
        self._output: Optional[AudioSocketOutputTransport] = None
        self._peer_disconnected = asyncio.Event()
        self._pipeline_started = asyncio.Event()

    def input(self) -> AudioSocketInputTransport:
        if not self._input:
            self._input = AudioSocketInputTransport(
                self, self._stream, self._params, name=self._input_name
            )
        return self._input

    def output(self) -> AudioSocketOutputTransport:
        if not self._output:
            self._output = AudioSocketOutputTransport(
                self, self._stream, self._params, name=self._output_name
            )
        return self._output

    async def on_peer_disconnected(self):
        """Called by the reader when Asterisk hangs up or the socket dies.

        Caller code (main.py) can await `wait_until_disconnected()` to drive
        pipeline shutdown from outside.
        """
        self._peer_disconnected.set()

    async def wait_until_disconnected(self) -> None:
        await self._peer_disconnected.wait()

    def _mark_pipeline_started(self) -> None:
        self._pipeline_started.set()

    async def wait_until_pipeline_started(self) -> None:
        """Lets callers wait until the pipeline has processed its StartFrame
        (i.e. transport is ready) before pushing the initial flow frame."""
        await self._pipeline_started.wait()
