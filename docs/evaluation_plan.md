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
H3. Caller says "I have a complaint about my invoice" → BILLING_DISPUTE
H4. Low STT confidence simulated (mock language_probability=0.3 for 2 turns) → LOW_STT_CONFIDENCE
H5. Caller repeats same sentence 3 times → FRUSTRATION
H6. "Ambiguous patient" (two Müllers, DOB not resolving) → AMBIGUOUS_PATIENT
H7. "Ich möchte mit jemandem sprechen" (German human request) → CALLER_REQUESTED

### Reschedule / cancel (self-serve — must NOT hand off)
R1. "I want to reschedule my appointment" → set_intent("reschedule") → manage_appointment flow → patient verified → appointment chosen → new slot proposed → confirmed → closing
R2. "Ich möchte meinen Termin absagen" → set_intent("cancel") → manage_appointment flow → patient verified → appointment chosen → cancellation confirmed → closing
R3. Reschedule a patient with no upcoming appointments → find_patient_appointments returns empty → transfer_to_human (edge case, not a handoff regression)
R4. Reschedule "Müller" without DOB (ambiguous) → caller asked for DOB → if DOB disambiguates, flow continues; if still ambiguous → transfer_to_human
R5. Cancel for a caller whose name is not in PMS → search_patient returns not_found after one spelling retry → transfer_to_human ("we cannot cancel an appointment that's not on file")

---

## Instrumentation

### Latency processor

Implemented. `LatencyStartMark` sits after STT, `LatencyEndMark` sits after TTS
(see [services/receptionist/processors.py](../services/receptionist/processors.py)
and the `pipeline = Pipeline([...])` block in
[services/receptionist/main.py](../services/receptionist/main.py)).

Each caller turn appends a record to `logs/latency.jsonl`:

```json
{"session_id": "...", "turn_id": 3, "stt_text_preview": "I need an appointment",
 "turn_latency_ms": 847.2, "timestamp": "2026-04-20T17:22:31.442Z"}
```

After running the evaluation scenarios, compute P50/P95:

```bash
python -m scripts.summarize_latency logs/latency.jsonl
```

Paste the P50/P95 numbers into [docs/phase0_baseline.json](phase0_baseline.json).

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

After completing all Phase 0 evaluation runs, fill in [docs/phase0_baseline.json](phase0_baseline.json)
(a template with null placeholders is already committed):

1. P50/P95 latency: `python -m scripts.summarize_latency logs/latency.jsonl`
2. STT WER per language: run the 15 EN + 15 DE gold-standard utterances through Whisper with `jiwer`
3. Booking completion rate: 10 happy-path scenarios, count successful bookings
4. Handoff accuracy: 8 handoff scenarios (H1–H8), count triggers that fired as expected

Commit the filled-in JSON. For any Phase 1 candidate provider: run the same test
suite and compare against this baseline. Reject if any metric regresses by more than 20%.
