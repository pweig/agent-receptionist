# Dental Office Voice Receptionist — Phase 0 POC

Voice-based dental appointment booking agent. Handles greeting → language detection (EN/DE) → info collection → slot proposal → booking confirmation, end-to-end via browser audio.

**Stack:** Pipecat · Daily.co WebRTC · Whisper STT · Claude claude-sonnet-4-6 · Piper TTS · Silero VAD

---

## Prerequisites

- Python 3.10+
- Anthropic API key ([console.anthropic.com](https://console.anthropic.com))
- Daily.co account — free tier ([daily.co](https://www.daily.co)) — for the browser test client

---

## Setup

```bash
# 1. Clone and enter the repo
git clone <repo-url>
cd agent-receptionist

# 2. Install dependencies + download Piper TTS models (~125 MB)
make setup

# 3. Configure environment
cp .env.example .env
# Edit .env: fill in ANTHROPIC_API_KEY and DAILY_API_KEY
```

---

## Run

```bash
make dev
```

The agent starts and prints a Daily room URL. Open that URL in a browser — your microphone connects directly to the agent. No phone number needed.

If you already have a specific Daily room URL, set it in `.env`:
```
DAILY_ROOM_URL=https://your-domain.daily.co/your-room
```

---

## Project Structure

```
services/receptionist/
├── main.py             — Pipecat pipeline (entry point)
├── prompt.py           — Persona + per-state system messages
├── state.py            — Conversation state enums and data model
├── handoff.py          — Handoff trigger evaluation (regex + heuristics)
├── tools/
│   ├── pms_mock.py     — Mock PMS: search_patient, get_available_slots, book_appointment, ...
│   └── schemas.py      — Tool definitions + LLM handler wrappers
├── flows/
│   └── nodes.py        — 9 conversation state NodeConfigs (pipecat-flows)
└── config/
    ├── office_hours.yaml
    └── settings.yaml

docs/
├── compliance_checklist.md   — HIPAA (US) + DSGVO/GDPR (DE)
├── evaluation_plan.md        — Metrics, test scenarios, latency benchmarks
└── voice_config.md           — STT/TTS recommendations per phase
```

---

## Conversation States

```
GREETING → LANGUAGE_DETECT → HOURS_CHECK → INTENT → INFO_COLLECTION
→ SLOT_PROPOSAL → CONFIRMATION → CLOSING

Any state → HANDOFF (on trigger) → CLOSING
```

Handoff triggers (Phase 1 eager policy): caller requests human, medical question, billing dispute, rescheduling, low STT confidence (2+ turns), caller frustration.

---

## Configuration

**Office hours:** `services/receptionist/config/office_hours.yaml`
Defines weekday hours, public holidays (DE + US), date exceptions, and after-hours routing (emergency number, voicemail, callback logging).

**Runtime settings:** `services/receptionist/config/settings.yaml`
Model selection, TTS voice names, VAD params, handoff thresholds.

---

## Mock PMS Data

Eight fictional patient records are pre-loaded, including:
- An intentional ambiguous pair (Thomas Müller / Tobias Müller) to test the ambiguous-patient handoff path
- DE and US patients with GKV, PKV, Selbstzahler, and US insurance types
- A pediatric patient to test the "booking on behalf of child" scenario

---

## Evaluation

Run the 10 test scenarios in `docs/evaluation_plan.md` after setup.
Target: P95 turn latency < 1000ms, > 80% booking completion, 100% handoff trigger accuracy.

---

## Phasing

| Phase | Stack | Status |
|---|---|---|
| 0 — POC | Pipecat + Daily + Whisper + Piper | **This repo** |
| 1 US | Add Twilio/Retell SIP; Deepgram STT; Cartesia TTS | Planned |
| 1 DE | Add EU SIP; Azure STT/TTS; DSGVO compliance | Planned |
| 2 | Rescheduling flow; sentiment-based handoff; waitlist | Future |

See `docs/voice_config.md` for Phase 1 provider recommendations.
See `docs/compliance_checklist.md` before any production deployment.
