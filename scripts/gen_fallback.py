#!/usr/bin/env python3
"""
Synthesise the SIP crash-fallback audio clip.

Piper + soxr resample to 8 kHz mono SLIN16 (the format Asterisk's AudioSocket
expects). Run once after `make setup`. Output:
  services/receptionist/audio/fallback_de.wav
"""

import wave
from pathlib import Path

import numpy as np
import soxr
from piper.voice import PiperVoice

REPO = Path(__file__).resolve().parents[1]
MODEL = REPO / "services" / "receptionist" / "models" / "piper" / "de_DE-thorsten-high.onnx"
OUT = REPO / "services" / "receptionist" / "audio" / "fallback_de.wav"
TEXT = "Es tut mir leid, es ist ein Fehler aufgetreten. Bitte rufen Sie in einem Moment zurück."
TARGET_RATE = 8000


def main() -> None:
    if not MODEL.exists():
        raise SystemExit(
            f"Piper model missing: {MODEL}\n"
            "Run `make setup` first to download voice models."
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)

    voice = PiperVoice.load(str(MODEL))

    chunks = list(voice.synthesize(TEXT))
    if not chunks:
        raise SystemExit("Piper returned no audio chunks")

    native_rate = chunks[0].sample_rate
    pcm_native = np.concatenate([c.audio_int16_array for c in chunks])

    resampled = soxr.resample(pcm_native, native_rate, TARGET_RATE, quality="HQ")
    out_pcm = np.clip(resampled, -32768, 32767).astype(np.int16)

    with wave.open(str(OUT), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(TARGET_RATE)
        wf.writeframes(out_pcm.tobytes())

    secs = len(out_pcm) / TARGET_RATE
    print(f"Wrote {OUT} ({OUT.stat().st_size} bytes, {secs:.2f}s @ {TARGET_RATE} Hz)")


if __name__ == "__main__":
    main()
