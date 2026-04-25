# M2 — VAD / STT Tuning Runbook

Goal: close the T4 acceptance criterion from
[phase1-m2-build-plan.md](phase1-m2-build-plan.md):

> STT confidence ≥ 0.70 on ≥ 90 % of utterances across a 10-call sample,
> and the agent does not cut the caller off mid-sentence on slow German speech.

Everything below is a one-shot procedure. When you're done, you'll have
evidence-backed values for `vad.stop_secs` and `stt.no_speech_prob` in
[services/receptionist/config/settings.yaml](../services/receptionist/config/settings.yaml)
and a filled-in results table in
[docs/voice_config.md](voice_config.md).

---

## 1. Enable call capture

Edit `.env`:

```bash
CAPTURE_CALLS=true
```

This triggers the optional path at
[audiosocket_transport.py:74-81](../services/receptionist/audiosocket_transport.py#L74-L81)
and at [main.py:450-454](../services/receptionist/main.py#L450-L454). Each call
writes raw 8 kHz SLIN16 PCM to `logs/captures/session_<uuid>.raw`.

`LOG_PII=true` is also useful during tuning — you'll want to see the
transcription next to the audio to judge whether Whisper got it right.
**Remember to set both back to `false` before going live.**

Restart the SIP pipeline so the new env vars take effect:

```bash
make dev-sip
```

## 2. Place the 10 calls

Target mix (spread across ≥ 2 sessions to hit different ambient-noise conditions):

| # | Intent | Notes |
|---|---|---|
| 1–3 | New appointment booking | Standard names, straightforward slot |
| 4–5 | New booking with an ambiguous patient | Exercise Thomas / Tobias Müller path in [tools/pms_mock.py](../services/receptionist/tools/pms_mock.py) |
| 6–7 | Reschedule | Caller already has an appointment; they give an existing reference |
| 8 | Cancel | |
| 9 | After-hours / closed office | Should route to closing |
| 10 | "Nein" on consent | Verify capture is empty or absent (see §5) |

Speak naturally. Don't over-articulate — we're tuning for real callers, not
for clean studio German. Include at least two slow/hesitant speakers if you
can rope someone in.

## 3. Convert captures to WAV

```bash
make convert-captures
```

This shells out to `ffmpeg -f s16le -ar 8000 -ac 1` for every
`logs/captures/*.raw` and writes a sibling `.wav`. Spot-check a couple by
playing them back — if they sound clipped or choppy on listen-back, the
problem is upstream of VAD/STT and tuning won't fix it.

## 4. Re-run Whisper offline and dump confidence

Pipecat's live STT writes one `stt_utterance` event per utterance to
[events.jsonl](../logs/events.jsonl), so the easiest source of truth is
`make metrics` (see §6) — it already aggregates confidence across the run.

If you want per-utterance CSV for spreadsheet analysis, run this against the
`.wav` captures:

```python
# scripts/whisper_eval.py (not committed — run ad hoc during tuning)
import csv
import sys
from pathlib import Path

from faster_whisper import WhisperModel

model = WhisperModel("small", device="cpu", compute_type="int8")

out = csv.writer(sys.stdout)
out.writerow(["session", "segment_start", "segment_end", "text", "no_speech_prob", "avg_logprob"])

for wav in sorted(Path("logs/captures").glob("*.wav")):
    segments, info = model.transcribe(str(wav), language="de", vad_filter=False)
    for seg in segments:
        out.writerow([
            wav.stem, f"{seg.start:.2f}", f"{seg.end:.2f}",
            seg.text, f"{seg.no_speech_prob:.3f}", f"{seg.avg_logprob:.3f}",
        ])
```

`1 - no_speech_prob` is the rough "confidence" the acceptance criterion
refers to (Whisper does not emit a direct confidence score; this is the
standard proxy). Pipe to `results.csv`, open in your tool of choice, and
compute the % of rows with `no_speech_prob ≤ 0.30`.

## 5. Verify consent-decline captures (DSGVO)

For call #10 (caller says "Nein"):

```bash
ls -la logs/captures/session_<decline-id>.raw
```

The capture file **may contain audio up to and including the "Nein"**
— the current `CAPTURE_CALLS` path writes from socket-open until transport
shutdown, and the consent check happens mid-call. This is a known limitation
to note in the privacy audit; the retention policy (7 days for captures)
and the fact that `CAPTURE_CALLS` is off in production is what satisfies the
compliance requirement, not the capture mechanism itself.

## 6. Read the aggregate numbers

```bash
make metrics
```

Under **STT CONFIDENCE**, record:

- `Utterances:` (call this `N`)
- `Mean confidence:`
- `≥ 0.70:` (must be `≥ 90%` to pass)
- Histogram row counts

Under **TURN LATENCY**, note `P50` and `P95` — even though T4's acceptance
is confidence-only, a regression in P95 from Phase 0 (~800–1200 ms) would
mean tuning helped STT but hurt responsiveness.

## 7. Decide on knob adjustments

Use the evidence from §4 and §6.

| Symptom | Likely knob | Direction |
|---|---|---|
| Agent cuts caller off mid-sentence | `vad.stop_secs` | Raise (0.5 → 1.0 → 1.2) |
| Agent waits too long after caller clearly stopped | `vad.stop_secs` | Lower, but never below 0.3 |
| Lots of `<no speech>` / gibberish transcriptions in quiet moments | `stt.no_speech_prob` | Raise (0.5 → 0.6 → 0.7) |
| Whispered or quiet utterances get dropped entirely | `stt.no_speech_prob` | Lower, but never below 0.3 |
| Mean confidence < 0.70 across the sample | STT model | Consider upgrading to `large-v3-turbo` (800 MB) before more tuning |

Change **one knob at a time**, re-run one or two calls to sanity-check the
change, and only then commit. Changing both at once makes the next round
impossible to interpret.

## 8. Record results

Edit [docs/voice_config.md](voice_config.md) → "Phase 1 — M2 DE tuning for
8 kHz phone audio" section. Fill in:

- Final `vad.stop_secs` and `stt.no_speech_prob`
- Sample size `N`
- Mean confidence, % ≥ 0.70
- Date of the tuning run
- One-line note on any surprise you hit (this is where hindsight for the
  next tuning round lives)

Then update [services/receptionist/config/settings.yaml](../services/receptionist/config/settings.yaml):

```yaml
stt:
  no_speech_prob: <new>
vad:
  stop_secs: <new>
```

Tick T4 in the M2 build plan's acceptance checklist.

## 9. Disable capture before going live

Before any real patient call:

```bash
# .env
CAPTURE_CALLS=false
LOG_PII=false
```

Run `make purge-old-logs` once to clear `logs/captures/` (the retention
policy defaults to 7 days, but for pre-launch you want an empty directory).
