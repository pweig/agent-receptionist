# Evaluation Plan

Phase 0 establishes baseline metrics that become minimum acceptable thresholds for
Phase 1 provider selection. Any telephony/STT/TTS provider that regresses these numbers
by more than 20% is rejected.

---

## Quantitative Metrics

| Metric | Phase 0 Target | How Measured |
|---|---|---|
| End-to-end turn latency P95 | < 1000ms | Latency processor (see Instrumentation below) |
| End-to-end turn latency P50 | < 600ms | Same |
| STT WER — English | < 10% | 15-utterance EN gold-standard test set |
| STT WER — German | < 10% | 15-utterance DE gold-standard test set |
| Language detection accuracy | > 95% | 20 EN + 20 DE test utterances |
| Booking completion rate | > 80% | 10 scripted happy-path scenarios |
| Handoff trigger rate (scripted) | 100% | 8 scripted handoff scenarios |
| Hallucinated tool calls | 0 | Turn-by-turn log review |
| Barge-in recovery rate | > 90% | 10 interruption test cases |

---

## Test Scenarios (Manual — browser tab to Daily room)

### Happy path
1. **EN new patient checkup** — greets in EN, collects all 6 fields, proposes 3 slots, caller accepts first, confirms, closes
2. **DE existing patient Kontrolle** — greets in DE, finds Müller (P001) by name+DOB, proposes slots, caller prefers Mittwoch, confirms, closes
3. **EN/DE code-switching** — caller opens in German, switches to "I need a Bleaching appointment" mid-call; agent stays in DE but uses "Bleaching"

### Edge cases
4. **Ambiguous patient (two Müllers)** — caller says "Müller, Thomas" without DOB; search returns two candidates; after 2 clarification turns, agent triggers AMBIGUOUS_PATIENT handoff
5. **All slots full → alternatives** — mock `get_available_slots` to return empty list; agent offers different date range and then waitlist
6. **After-hours call** — mock `get_office_hours` to return closed; agent gives after-hours message and emergency number, offers callback logging
7. **Barge-in mid-TTS** — caller interrupts agent's slot proposal; agent acknowledges correction and re-proposes
8. **Emergency/pain urgency** — caller says "I have bad tooth pain"; agent recognizes emergency, calls `get_available_slots` with `urgency="emergency"`, proposes same-day/next-day options
9. **Caller on behalf of child** — "I'm calling to book an appointment for my daughter Emma Wilson"; agent collects Emma's DOB, finds P008, continues normally
10. **Poor connection / silence** — agent doesn't receive a transcript for 2 turns; asks to repeat; if still nothing, offers to transfer or call back

### Handoff triggers (must all fire correctly)
H1. Caller says "Can I speak to a human?" → CALLER_REQUESTED
H2. Caller says "What medication should I take?" → MEDICAL_QUESTION
H3. Caller says "I want to reschedule my appointment" → RESCHEDULE
H4. Caller says "I have a complaint about my invoice" → BILLING_DISPUTE
H5. Low STT confidence simulated (mock language_probability=0.3 for 2 turns) → LOW_STT_CONFIDENCE
H6. Caller repeats same sentence 3 times → FRUSTRATION
H7. "Ambiguous patient" (two Müllers, DOB not resolving) → AMBIGUOUS_PATIENT
H8. "Ich möchte mit jemandem sprechen" (German human request) → CALLER_REQUESTED

---

## Instrumentation

### Latency processor

Insert two thin `FrameProcessor` subclasses in the pipeline to timestamp each turn:

```python
# After WhisperSTTService — record when transcription is complete
class STTTimestampProcessor(FrameProcessor):
    async def process_frame(self, frame, direction):
        if isinstance(frame, TranscriptionFrame):
            frame.metadata = {"stt_end_ts": time.monotonic_ns()}
        await self.push_frame(frame, direction)

# Before transport.output() — record when first audio chunk is ready
class TTSTimestampProcessor(FrameProcessor):
    async def process_frame(self, frame, direction):
        if isinstance(frame, AudioRawFrame) and not self._logged:
            stt_ts = getattr(frame, "metadata", {}).get("stt_end_ts", 0)
            delta_ms = (time.monotonic_ns() - stt_ts) / 1_000_000
            _log_latency(session_id, turn_id, delta_ms)
            self._logged = True
        await self.push_frame(frame, direction)
```

Logs `{session_id, turn_id, stt_end_ms, tts_first_chunk_ms, delta_ms}` to `logs/latency.jsonl`.

### WER measurement

1. Prepare a 30-utterance gold-standard set: 15 EN + 15 DE
   - Cover: patient names, phone numbers, dates, visit reasons, insurance types
   - Include at least 2 utterances with background noise (recorded in noisy environment)
2. Run each utterance through `WhisperSTTService` in isolation (no pipeline)
3. Compute WER: `(S + D + I) / N` where S=substitutions, D=deletions, I=insertions, N=reference words
4. Use `jiwer` library: `wer(reference, hypothesis)`

### Language detection accuracy

Run 40 test utterances (20 EN, 20 DE) through the language_detection node.
Record detected language vs. ground truth.
Target: ≥ 38/40 (95%).

---

## Phase 0 → Phase 1 Regression Baseline

After completing all Phase 0 evaluation runs:

1. Export P50/P95 latency from `logs/latency.jsonl`
2. Record STT WER per language
3. Record booking completion rate and handoff accuracy
4. Save as `docs/phase0_baseline.json`:

```json
{
  "date": "2026-04-19",
  "latency_p50_ms": ...,
  "latency_p95_ms": ...,
  "wer_en": ...,
  "wer_de": ...,
  "booking_completion_rate": ...,
  "handoff_accuracy": ...,
  "language_detection_accuracy": ...
}
```

For any Phase 1 candidate provider: run the same test suite and compare against this baseline.
Reject if any metric regresses by more than 20%.
