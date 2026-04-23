# M2 Build Plan — Hardening & DSGVO Compliance

**Branch:** `feature-m2`
**Goal:** safe to point a real friend / pilot dentist at the number.
**Prerequisite:** M1 complete — real phone calls reach the Pipecat pipeline end-to-end.

---

## Acceptance criteria (gate for merging to main)

- [ ] 20 consecutive test calls without a pipeline crash
- [ ] Logs contain no raw PII (manual `grep` audit of a 24 h log sample)
- [ ] Consent flow demonstrably skips any recording when the caller says "Nein"
- [ ] VAD/STT tuned: STT confidence ≥ 0.70 on ≥ 90% of utterances in a 10-call sample
- [ ] All DSGVO checklist items for DE marked done in `compliance_checklist.md`

---

## Task 1 — DSGVO consent flow

**Why first:** the spec risk table flags this as *High × High*. No real call should be processed before the consent flow is in place.

### What to build

1. New flow node `consent` inserted between `greeting` and `hours_check`.
2. Prompt: the agent reads the German consent text (see below) and then calls `record_consent(given: bool)`.
3. If `given=false` → go to `handoff` immediately with reason `"consent_declined"`.
4. If `given=true` → store `consent_given=true` + timestamp in `flow_manager.state` and continue to `hours_check`.
5. Log the consent event to `events.jsonl` (session_id, timestamp, consent_given, caller_id).

**Consent text (draft — review with a German lawyer before production use):**
> *"Dieses Gespräch wird zum Zweck der Terminvereinbarung automatisch verarbeitet.
> Sind Sie damit einverstanden? Antworten Sie bitte mit Ja oder Nein."*

### Files to change
- `services/receptionist/flows/nodes.py` — add `create_consent_node()`, update `_handle_set_language` to transition to consent instead of hours_check
- `services/receptionist/prompt.py` — add `STATE_TASK_MESSAGES["consent"]`
- `services/receptionist/state.py` — add `consent_given` field to initial state

### Acceptance
- Saying "Nein" / "No" → agent says goodbye and hangs up; no STT output written to log
- Saying "Ja" / "Yes" → call continues normally; `events.jsonl` has `consent_given: true`

---

## Task 2 — PII redaction in logs

### Problem
Loguru `[STT]` lines print raw transcriptions — these contain patient names, dates of birth, phone numbers. The `events.jsonl` telemetry file currently stores session IDs and latencies only (clean), but if extended to include transcripts they must be scrubbed first.

### What to build

1. `services/receptionist/privacy.py` — a `redact(text: str) -> str` function using regex rules:
   - Dates in `DD.MM.YYYY` / `DD/MM/YYYY` / spoken form → `[DOB]`
   - German mobile / landline patterns → `[PHONE]`
   - No attempt to redact free-form names (too error-prone); instead suppress STT log lines in production mode via a log level flag.
2. `LOG_PII=false` env var (default false in production). When false, the `[STT]` lines in `DebugFrameLogger` are printed at DEBUG level only, and any `events.jsonl` transcript field is redacted via `redact()` before writing.
3. Update `services/receptionist/main.py` `DebugFrameLogger` to respect `LOG_PII`.

### Files to change
- `services/receptionist/privacy.py` — new file
- `services/receptionist/main.py` — gate `[STT]` log lines on `LOG_PII`
- `services/receptionist/telemetry.py` — apply `redact()` to any transcript fields before writing to `events.jsonl`
- `.env.example` — document `LOG_PII=false`

### Acceptance
- `grep -i "müller\|meier\|01[567]\|[0-9]\{2\}\.[0-9]\{2\}\.[0-9]\{4\}" logs/events.jsonl` returns nothing in a 24 h sample

---

## Task 3 — Crash recovery

### Problem
If the Pipecat pipeline raises an unhandled exception (LLM timeout, model OOM, etc.) the TCP connection closes silently and the caller hears dead air, then a click. No graceful message is played.

### What to build

1. Wrap `runner.run(task)` in `run_bot_sip()` in a broad `try/except`.
2. On exception: log the traceback, then send a pre-recorded fallback clip via the AudioSocket before closing.
3. Pre-record (or synthesise once at build time) a German 8 kHz a-law WAV:
   > *"Es tut mir leid, es ist ein Fehler aufgetreten. Bitte rufen Sie in einem Moment zurück."*
4. The fallback clip is stored at `services/receptionist/audio/fallback_de.wav` and read into bytes at startup. On crash, `AudioSocketOutputTransport` writes it as a sequence of 320-byte `0x10` frames before sending `0x00` (hangup).
5. Add a monotonic restart counter per process — if > 3 crashes in 10 minutes, log a `CRITICAL` alert and stop accepting new connections (avoids a crash loop).

### Files to change
- `services/receptionist/main.py` — wrap `runner.run`, load fallback clip, implement crash counter
- `services/receptionist/audiosocket_transport.py` — add `play_raw_wav(path)` helper on the transport
- `services/receptionist/audio/` — new directory, add `fallback_de.wav`
- `Makefile` — `make gen-fallback` target that uses Piper to synthesise the clip at setup time

### Acceptance
- Kill Ollama mid-call → caller hears the German apology before hangup
- Third crash within 10 min → new calls are rejected and a `CRITICAL` line appears in the log

---

## Task 4 — VAD / STT re-tuning for 8 kHz phone audio

### Problem
The current `settings.yaml` values (`stop_secs`, `no_speech_prob`) were tuned on 16 kHz WebRTC audio from the browser. Phone audio has narrowband codec compression, background noise, and echo — VAD triggers differ, and Whisper confidence scores are systematically lower.

### What to build

1. Add a **call recording capture mode**: env var `CAPTURE_CALLS=true` writes raw 8 kHz SLIN PCM frames from the AudioSocket to `logs/captures/session_<id>.raw`. Used only during tuning, never in production.
2. Convert captures to WAV for manual listening and offline Whisper evaluation:
   `make convert-captures` — uses `ffmpeg` to convert `.raw` → `.wav`.
3. Run at least 10 real test calls, capture, and evaluate Whisper confidence distribution. Adjust:
   - `vad.stop_secs` (currently 0.8 s — phone silence sounds different; may need 1.0–1.2 s)
   - `stt.no_speech_prob` threshold (currently 0.6 — phone noise raises baseline; tune up)
4. Document the final values and the test methodology in `docs/voice_config.md`.

### Files to change
- `services/receptionist/audiosocket_transport.py` — optional capture path (behind env var)
- `services/receptionist/config/settings.yaml` — updated thresholds after tuning
- `docs/voice_config.md` — tuning rationale and sample statistics
- `Makefile` — `make convert-captures` target
- `.env.example` — document `CAPTURE_CALLS=false`

### Acceptance
- STT confidence ≥ 0.70 on ≥ 90 % of utterances across the 10-call sample after tuning
- Agent does not cut the caller off mid-sentence on slow German speech

---

## Task 5 — Extended metrics

### Problem
`events.jsonl` currently captures latency and booking/handoff outcomes. M2 adds more callers, so we need richer per-call observability to detect regressions without listening to every call.

### What to build

Extend `LatencyTracker` / telemetry to emit additional events:

| Event | Fields |
|---|---|
| `call_start` | session_id, timestamp, caller_id_hash |
| `call_end` | session_id, duration_secs, intent, handoff, tool_errors |
| `stt_utterance` | session_id, confidence, language, duration_ms |
| `llm_turn` | session_id, latency_ms, tokens_in, tokens_out |
| `consent` | session_id, consent_given |
| `crash` | session_id, exc_type, traceback_hash |

Add a `make metrics` target that reads `logs/events.jsonl` and prints a 1-page summary: call count, mean/P95 latency, handoff rate, STT confidence histogram, crash count.

### Files to change
- `services/receptionist/telemetry.py` — new event types + helper functions
- `services/receptionist/main.py` — emit `call_start` / `call_end` events
- `services/receptionist/processors.py` — emit `stt_utterance` and `llm_turn` events
- `Makefile` — `make metrics` target (Python one-liner reading `logs/events.jsonl`)

---

## Task 6 — Retention policy & DSGVO documentation

### Retention

1. Add `scripts/purge_logs.py` — deletes `events.jsonl` lines older than `LOG_RETENTION_DAYS` (default 30) and any `logs/captures/*.raw` older than `CAPTURE_RETENTION_DAYS` (default 7).
2. Add `Makefile` target `make purge-old-logs` (manual) and document a cron entry in the M4 runbook.
3. `events.jsonl` rotates daily (`loguru` rotation config) so files are dated and purgeable individually.

### Documentation

1. Fill in the DE section of `docs/compliance_checklist.md`:
   - Mark the items covered by T1 (consent), T2 (PII redaction), T5 (retention) as done
   - Add the Art. 30 Verarbeitungsverzeichnis entry (processing purpose, data categories, retention, recipients)
   - Document the Art. 22 assessment (appointment booking is not an automated significant decision)
2. Update `docs/architecture.adoc` to show the consent node in the flow diagram.

### Files to change
- `scripts/purge_logs.py` — new file
- `docs/compliance_checklist.md` — tick DE items covered by M2
- `docs/architecture.adoc` — flow diagram update
- `Makefile` — `make purge-old-logs`

---

## Task order and dependencies

```
T1 (consent flow)       ← no dependencies, start here
T2 (PII redaction)      ← after T1 (consent event must be logged cleanly)
T3 (crash recovery)     ← independent, can run in parallel with T1/T2
T4 (VAD/STT tuning)     ← needs real call samples; start captures during T1–T3 testing
T5 (extended metrics)   ← after T1/T2 (new events depend on consent + PII logic)
T6 (docs + retention)   ← last; fills in results of T1–T5
```

---

## Out of scope for M2

- Real PMS integration (M3)
- Outbound calls / appointment reminders (post-M3)
- Multi-call concurrency / load testing (M4)
- HIPAA (US deployment, post-M3)
