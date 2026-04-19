# Dental Office Voice Receptionist — Phase 0 POC

Voice-based dental appointment booking agent. Handles greeting → language selection (EN/DE) → office hours check → info collection → slot proposal → booking confirmation, end-to-end via browser audio.

**Stack:** Pipecat · SmallWebRTC (browser, no account needed) · Whisper STT · Groq `llama-3.3-70b-versatile` or Ollama `qwen2.5:14b` · Piper TTS · Silero VAD

---

## Prerequisites

- Python 3.10+
- LLM backend (choose one):
  - **Groq API key** — free tier at [console.groq.com](https://console.groq.com) (100k tokens/day)
  - **Ollama** (local, no limits) — see [Local LLM Setup](#local-llm-setup-ollama) below

---

## Setup

```bash
# 1. Clone and enter the repo
git clone git@github.com:pweig/agent-receptionist.git
cd agent-receptionist

# 2. Install dependencies + download Piper TTS models (~125 MB)
make setup

# 3. Configure environment
cp .env.example .env
# Edit .env: fill in GROQ_API_KEY (skip if using Ollama)
```

### Local LLM Setup (Ollama)

If you prefer to run without a Groq API key:

```bash
# Install Ollama
brew install ollama

# Pull the recommended model for M-series Mac (14.8B, ~9 GB)
ollama pull qwen2.5:14b

# Start the Ollama server (keep this terminal open)
ollama serve
```

Then in `services/receptionist/config/settings.yaml`, comment out the Groq block and uncomment the Ollama block (or vice versa):

```yaml
llm:
  # --- Groq (cloud, free tier 100k TPD) ---
  # model: llama-3.3-70b-versatile
  # base_url: https://api.groq.com/openai/v1

  # --- Ollama (local, no limits) ---
  model: qwen2.5:14b
  base_url: http://localhost:11434/v1
```

The app auto-detects which backend to use from the `base_url` field — no code changes needed.

---

## Run

```bash
make dev
```

Opens http://localhost:7860 — click **Start Call** to connect your browser microphone directly to the agent. No phone number or external account needed.

> **Ollama users:** `ollama serve` must be running in a separate terminal before `make dev`.

---

## Configuration

**Office hours:** `services/receptionist/config/office_hours.yaml`
Defines weekday hours, public holidays (DE + US), date exceptions, and after-hours routing (emergency number, voicemail, callback logging).

**Runtime settings:** `services/receptionist/config/settings.yaml`
Model selection (Groq or Ollama), TTS voice names, VAD params, handoff thresholds.

### Environment Variables

| Variable        | Default | Description                                                                                       |
| --------------- | ------- | ------------------------------------------------------------------------------------------------- |
| `OFFICE_LOCALE` | `de`    | Sets greeting language and after-hours routing. `de` = German (Sie-form), anything else = English |
| `WHISPER_MODEL` | `small` | Whisper model size. Options: `tiny`, `small`, `medium`, `large-v3-turbo`                          |
| `PORT`          | `7860`  | Local server port                                                                                 |
| `GROQ_API_KEY`  | —       | Required only when using Groq backend                                                             |

Override Whisper model at runtime without editing yaml:

```bash
WHISPER_MODEL=tiny make dev
```

---

## Project Structure

```
services/receptionist/
├── main.py             — Pipecat pipeline + FastAPI app (entry point)
├── prompt.py           — Persona + per-state system messages
├── state.py            — Conversation state enums and data model
├── handoff.py          — Handoff trigger evaluation (regex + heuristics)
├── static/
│   └── index.html      — Browser WebRTC client (Start Call UI)
├── tools/
│   ├── pms_mock.py     — Mock PMS: search_patient, get_available_slots, book_appointment, ...
│   └── schemas.py      — Raw tool property dicts + TTS_VOICES constant
├── flows/
│   └── nodes.py        — 9 conversation state NodeConfigs + tool handlers (pipecat-flows)
├── models/
│   └── piper/          — Downloaded Piper TTS .onnx voice models (created by make setup)
└── config/
    ├── office_hours.yaml
    └── settings.yaml

docs/
├── architecture.adoc         — Full architecture reference with PlantUML diagrams
├── compliance_checklist.md   — HIPAA (US) + DSGVO/GDPR (DE)
├── evaluation_plan.md        — Metrics, test scenarios, latency benchmarks
└── voice_config.md           — STT/TTS recommendations per phase
```

---

## Conversation States

```
GREETING → HOURS_CHECK → COLLECT_INFO → SLOT_PROPOSAL → CONFIRMATION → CLOSING

Any state → HANDOFF (on trigger) → CLOSING
```

Handoff triggers (defined in `handoff.py`, wired in Phase 1): caller requests human, medical question, billing dispute, rescheduling, low STT confidence (2+ turns), caller frustration (repeated utterances).

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

## Architecture

Full architecture documentation — including pipeline frame flow, state machine, tool inventory, and deployment diagrams — is in [`docs/architecture.adoc`](docs/architecture.adoc).

---

## Phasing

| Phase   | Stack                                                                  | Status        |
| ------- | ---------------------------------------------------------------------- | ------------- |
| 0 — POC | SmallWebRTC (aiortc) + Whisper + Piper + Groq or Ollama                | **This repo** |
| 1 US    | Add Twilio/Retell SIP; Deepgram STT; Cartesia TTS; Dentrix PMS         | Planned       |
| 1 DE    | Add EU SIP; Azure STT/TTS; DSGVO compliance; Dampsoft PMS              | Planned       |
| 2       | Rescheduling flow; sentiment-based handoff; waitlist; context trimming | Future        |

See `docs/voice_config.md` for Phase 1 provider recommendations.
See `docs/compliance_checklist.md` before any production deployment.

---

## Troubleshooting

**Rate limit (429 from Groq)**
You've hit the free-tier daily limit (100k tokens/day). Options: switch to Ollama (see [Local LLM Setup](#local-llm-setup-ollama)) or wait for the daily limit to reset at midnight UTC.

**No audio in browser**
Check that you granted microphone permission when prompted. Use Chrome or Firefox — Safari has known WebRTC compatibility issues and is not recommended.

**Ctrl+C not stopping the server**
The FastAPI lifespan handler cancels all active bot tasks on shutdown. If it hangs, force-kill the port:

```bash
kill $(lsof -ti:7860)
```

**Piper model missing / TTS silent**
The `.onnx` model files were not downloaded. Re-run setup:

```bash
make setup
```

Models are saved to `services/receptionist/models/piper/`.

**"Connection refused" or LLM errors with Ollama**
`ollama serve` must be running before `make dev`. Start it in a separate terminal:

```bash
ollama serve
```

Then verify the model is pulled: `ollama list` should show `qwen2.5:14b`.
