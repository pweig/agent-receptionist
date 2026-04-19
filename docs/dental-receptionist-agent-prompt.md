# Dental Office Voice Receptionist Agent — Build Brief

**Role:** You are an expert voice-AI agent architect.

**Task:** Design and build a voice-based receptionist agent for a dental office. The agent handles incoming appointment calls end-to-end — greeting, information collection, slot negotiation, and confirmed booking — with a pragmatic, phased approach to maturity.

---

## Channel and availability

- **Channel:** Voice (telephony). Voice-first constraints apply: sub-1s response latency target, barge-in support, natural turn-taking, disfluency tolerance, graceful handling of background noise and partial transcriptions.
- **Availability:** Not 24/7. The agent is active during configurable office hours. Outside of hours, calls route to a defined fallback (voicemail with callback logging, after-hours emergency number, or silent decline — decide during design).

---

## Deployment strategy (phased)

### Phase 0 — Open-source POC (self-hosted, no telephony provider)

Build and validate the full conversation pipeline on an open-source stack before committing to any commercial telephony vendor. Goals of the POC:

- Prove end-to-end conversation logic works (greeting → info collection → slot proposal → booking confirmation)
- Validate bilingual EN/DE behavior including code-switching
- Stress-test tool calling against mock practice management system (PMS) interfaces
- Measure end-to-end latency, turn-taking quality, and barge-in behavior
- Iterate on the system prompt cheaply, without per-minute telephony charges

**Recommended open-source stack (all components have mature 2026 support):**

- **Orchestration framework:** Pipecat (Python, widest STT/LLM/TTS provider support, best for fast prototyping) or LiveKit Agents (WebRTC-first, better for eventual telephony via SIP, stronger latency profile). Both are fully open-source and production-used.
- **STT (speech-to-text):** Whisper (OpenAI open-weights, strong German + English) or faster-whisper for lower latency. Self-hosted.
- **LLM:** Start with a hosted API during POC (Claude, GPT-4o, or similar) for conversation quality; self-host via Ollama / vLLM only if data-residency rules require it. The prompt is portable either way.
- **TTS (text-to-speech):** Piper (fast, CPU-friendly, good German voices — e.g. `de_DE-thorsten`) for low-latency scenarios; Coqui XTTS-v2 for higher-quality multilingual output including voice cloning if a consistent brand voice is wanted. Both are self-hostable.
- **VAD and turn detection:** Silero VAD (standard in both Pipecat and LiveKit Agents).
- **Client for testing:** WebRTC browser client (LiveKit playground or Pipecat's Voice UI Kit) — no real phone number needed during POC.

**POC scope constraints:**

- Browser-to-agent audio only; no real telephony yet
- Mock PMS tool implementations (in-memory calendar, fake patient records)
- One test environment, local or cheap cloud VM with CPU-only inference where possible
- Measured success: < 1s end-to-end latency on test hardware, clean EN/DE switching, successful mock booking flow

**Exit criteria from POC:** conversation quality is good enough that the remaining risk is telephony integration (SIP, codecs, phone-network audio quality) rather than agent logic.

### Phase 1 — First production deployment with eager handoff

Integrate the validated agent with a commercial telephony provider per region. The orchestration framework chosen in Phase 0 (Pipecat or LiveKit) already supports SIP telephony, so the migration is primarily adding a telephony adapter — not rewriting logic.

### Phase 2 — Mature deployment

Tighten handoff criteria, expand scope to rescheduling and cancellations, optimize costs.

---

## Platform strategy (Phase 1+)

Build a **platform-abstracted design** that can deploy on different telephony stacks without rewriting conversation logic.

- **US deployment:** needs HIPAA compliance with a signed BAA, US-based data residency, and mature English support. Viable candidates: Retell AI (self-service BAA, lowest friction), Vapi (HIPAA as add-on), Bland AI (HIPAA on business tiers). Self-hosted option: continue with Pipecat or LiveKit + SIP provider (e.g. Twilio) with BAA in place.
- **German deployment:** needs GDPR/DSGVO compliance, EU data residency, strong German-language voice quality, and ideally a German or EU-based vendor to reduce cross-border data transfer complexity. Viable candidates: Parloa (Berlin-based, Azure-hosted, enterprise budget) or Synthflow (SMB budget, EU hosting available). Self-hosted option: same Pipecat/LiveKit stack deployed on EU infrastructure (Hetzner, OVH, Scaleway) with an EU SIP provider.

The prompt, state machine, and tool contracts must be portable across all of these with only configuration changes.

---

## Core requirements

### 1. Language handling

- Detect English vs. German from the caller's opening utterance
- Continue the full conversation in the detected language
- Handle EN/DE code-switching (common with German callers using English dental terms)
- Pronounce names, dates, and times naturally in the chosen language
- STT/TTS model choice must be validated separately per deployment — German voice quality varies significantly across providers and is a key selection criterion for the EU stack

### 2. Conversational behavior

- Warm, professional, concise — a good human receptionist, not a chatbot
- One question at a time; avoid multi-part questions
- Confirm critical details (name spelling, date of birth, phone number, appointment time) by reading them back
- Handle interruptions and corrections without losing state
- Keep turns short — voice callers tune out after ~2 sentences

### 3. Information to collect

- Full name (with spelling confirmation for non-obvious names)
- Date of birth
- Phone number (read back digit-by-digit)
- Reason for visit (checkup, cleaning, pain, emergency, consultation, etc.)
- New vs. existing patient
- Insurance type (gesetzlich / privat / Selbstzahler for DE; insurance provider for US)

### 4. Scheduling negotiation

- Query calendar for slots matching the visit type's required duration
- Propose 2–3 concrete options rather than open-ended "when works for you?"
- Offer alternatives when preferred times are full: next availability, different day of week, waitlist
- Prioritize urgency: pain/emergency → same-day or next-day; routine → normal availability
- Respect office hours configuration when proposing slots

### 5. System integration (stubbed, PMS-agnostic)

Clean tool interfaces that don't bind to a specific practice management system:

```
search_patient(name, dob) → patient_record | null
get_available_slots(visit_type, date_range, urgency) → slot[]
book_appointment(patient, slot, visit_type, notes) → confirmation_id
send_confirmation(patient, appointment, channel) → status
get_office_hours(date) → hours | closed
```

Mock implementations during Phase 0 (in-memory Python dicts are fine). Swapping in a real PMS later — Dampsoft, Evident, CGM Z1 for DE; Dentrix, Open Dental, Eaglesoft for US — should be an integration task, not a redesign.

### 6. Handoff policy (phased)

**Phase 1 — early deployment (eager handoff):** Transfer to a human whenever any of these occur:

- Caller asks for a human at any point
- Caller sounds frustrated, confused, or repeats themselves more than once
- Any medical question beyond booking (symptoms, treatment advice, medication)
- Billing, insurance disputes, or complaints
- Ambiguous patient record match
- Rescheduling or cancellation requests (booking only in Phase 1)
- STT confidence drops below threshold for 2+ turns
- Anything outside a clean booking flow

**Phase 2 — mature deployment (selective handoff):** Tighten criteria as confidence grows. Handle rescheduling, cancellations, insurance clarifications, multi-patient family bookings autonomously. Reserve handoff for medical advice, complaints, complex insurance, and explicit human requests.

**During office hours:** handoff = warm transfer to reception.
**Outside office hours:** handoff = capture callback number + reason, log for morning follow-up; route true emergencies to the on-call emergency number.

### 7. Edge cases to handle explicitly

- Caller on behalf of someone else (parent/child, spouse, elderly relative)
- Requested time fully booked
- Patient not found in system (new patient path vs. spelling issue)
- Emergency/severe pain → immediate same-day offer or handoff per urgency protocol
- After-hours callers → polite redirect to voicemail or emergency line
- Ambient noise, poor connection, silence → graceful re-prompt, then handoff or voicemail

---

## Deliverables

1. **Phase 0 POC implementation** — working self-hosted voice agent using the open-source stack above, runnable locally with mock PMS tools and a browser test client
2. **Agent system prompt** — full LLM prompt with persona, rules, and language handling (portable across platforms)
3. **Tool definitions** — PMS-agnostic function signatures with mock implementations
4. **Conversation state model** — states (greeting, language detection, hours check, intent, info collection, slot proposal, confirmation, handoff, closing), transitions, recovery paths
5. **Handoff triggers** — concrete, testable conditions for Phase 1, with Phase 2 loosening notes
6. **Voice configuration** — STT/TTS recommendations per phase:
   - Phase 0: Whisper + Piper/Coqui, self-hosted
   - Phase 1 US: evaluate Retell/Vapi with English-tuned voices, or keep self-hosted + SIP
   - Phase 1 DE: evaluate Synthflow/Parloa with German-tuned voices, or keep self-hosted + EU SIP; validate pronunciation of German names, street addresses, and insurance provider names
7. **Compliance checklist** — HIPAA (US) and DSGVO/GDPR (DE) requirements: BAA/AVV signed, data residency confirmed, call recording consent flow, PII redaction in logs, retention policy
8. **Office hours configuration** — schema for defining hours per weekday, holidays, and exceptions; after-hours routing behavior
9. **Evaluation plan** — booking completion rate, handoff rate (expected high in Phase 1), language detection accuracy, caller satisfaction proxies (interruptions, repeated questions, hang-ups), and latency measurements from Phase 0 carried forward as regression benchmarks

---

## Phasing principle

Build Phase 0 on open source to de-risk the conversation logic cheaply. Build Phase 1 with eager handoff as a safety net. Instrument everything so handoff clusters become the Phase 2 roadmap. Don't try to handle every case on day one.
