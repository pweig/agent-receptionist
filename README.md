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
# if not activated
source /Users/D026233/dev/agent-receptionist/.venv/bin/activate

make dev
```

Opens http://localhost:7860 — click **Start Call** to connect your browser microphone directly to the agent. No phone number or external account needed.

> **Ollama users:** `ollama serve` must be running in a separate terminal before `make dev`.

---

## Run — SIP / Phone Mode (Phase 1)

To take real phone calls through the FritzBox instead of the browser, three things must be running:

1. **Asterisk** (Docker) — registers with the FritzBox, terminates the SIP call, bridges audio to AudioSocket
2. **Ollama** (or Groq) — the LLM backend
3. **The agent in SIP mode** — listens on TCP `:8089` for Asterisk's AudioSocket stream

### One-time setup

Configure the FritzBox + Asterisk per [docs/phase1-m1-fritzbox-setup.md](docs/phase1-m1-fritzbox-setup.md):

```bash
cp services/telephony/.env.example services/telephony/.env
# fill in FRITZBOX_USER / FRITZBOX_PASS / EXTERNAL_IP
docker compose -f services/telephony/docker-compose.yml build
```

### Day-to-day startup (3 terminals)

**Terminal 1 — Asterisk:**

```bash
cd services/telephony
docker compose up
# wait for "Outbound registration successful"
```

**Terminal 2 — Ollama** (skip if you're on Groq):

```bash
ollama serve
```

**Terminal 3 — agent in SIP mode:**

```bash
make dev-sip
# AudioSocket listener on :8089
```

Call your FritzBox number — Asterisk answers, dials `host.docker.internal:8089`, and the call runs through the same Pipecat pipeline as the WebRTC path.

`Ctrl+C` each terminal to stop. If the agent crashed and left `:8089` bound, free it with:

```bash
kill $(lsof -ti:8089)
```

See [docs/sip-debugging-runbook.md](docs/sip-debugging-runbook.md) when something doesn't connect.

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
├── telemetry.py        — Session event-log helper (append_event → logs/events.jsonl)
├── processors.py       — HandoffEvaluator + WhisperSTTWithConfidence pipeline processors
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
├── phase0_baseline.json      — Regression baseline template (fill in during evaluation)
└── voice_config.md           — STT/TTS recommendations per phase

scripts/
└── summarize_session.py      — P50/P95 + completion/handoff rates over logs/events.jsonl
```

---

## Conversation States

```
                          ┌──► COLLECT_INFO ──► SLOT_PROPOSAL ──► CONFIRMATION ──► CLOSING
GREETING → HOURS_CHECK → INTENT ──► MANAGE_APPOINTMENT ──► cancel ──────────────► CLOSING
                          │                       └──► RESCHEDULE_SLOT_PROPOSAL ──► CLOSING
                          └──► HANDOFF ("other") ──► CLOSING

Any eligible node → HANDOFF (on trigger) → CLOSING
```

Intent routing from the `INTENT` node:

- `booking` → `COLLECT_INFO` → booking flow
- `reschedule` / `cancel` → `MANAGE_APPOINTMENT` (self-serve — no handoff)
- `other` → `HANDOFF` (medical, billing, insurance disputes)

Handoff triggers fire two ways:

1. **LLM-driven** — the LLM calls `transfer_to_human` when the caller's request is out of scope.
2. **Auto-triggered** — `HandoffEvaluator` (in `processors.py`) runs `evaluate_handoff()` on every caller transcription and forces a transition to the handoff node on: caller requests human, medical question, billing dispute, low STT confidence (2+ turns), caller frustration (repeated utterances).

Reschedule and cancel requests are **not** handoff triggers — the `MANAGE_APPOINTMENT` flow handles them end-to-end.

---

## Mock PMS Data

Eight fictional patient records are pre-loaded, including:

- An intentional ambiguous pair (Thomas Müller / Tobias Müller) to test the ambiguous-patient handoff path
- DE and US patients with GKV, PKV, Selbstzahler, and US insurance types
- A pediatric patient to test the "booking on behalf of child" scenario

Three upcoming appointments are pre-seeded for reschedule/cancel testing — Thomas Müller (P001), Anna Schmidt (P003), and Sarah Johnson (P004). Seeded in `_seed_demo_appointments()` in [services/receptionist/tools/pms_mock.py](services/receptionist/tools/pms_mock.py).

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
kill $(lsof -ti:7860)   # WebRTC mode
kill $(lsof -ti:8089)   # SIP mode (AudioSocket listener)
```

**SIP: `address already in use` on :8089 when running `make dev-sip`**
A previous SIP-mode agent is still bound to the AudioSocket port (often after a crash where the parent process died but the child kept the socket). Find and kill it:

```bash
lsof -nP -iTCP:8089 -sTCP:LISTEN   # confirm what's holding it
kill $(lsof -ti:8089)              # SIGTERM; add -9 if it ignores
```

**SIP: phone rings ~5 times then goes to voicemail; agent never picks up**
First, the most common cause: the host's LAN IP changed (DHCP renewal, switching networks) but `EXTERNAL_IP` in `services/telephony/.env` still points at the old one. Asterisk then registers a Contact header FritzBox can't reach, so INVITEs never arrive and the FritzBox falls back to its own voicemail. `make dev-sip` now runs `services/telephony/preflight.sh` automatically to detect and fix this — invoke it directly if you're starting the container by hand:

```bash
bash services/telephony/preflight.sh
```

If that reports `ok` but the symptom persists, the call really isn't reaching Asterisk. To distinguish "FritzBox dropped the call" from "Asterisk dropped the call", confirm whether Asterisk ever sees an `INVITE`:

```bash
docker exec agent-asterisk asterisk -rx "pjsip set logger on"
docker logs -f --since 1s agent-asterisk | grep -aE 'INVITE|From:|No matching'
# now place the test call
```

- **No `INVITE` line appears** → FritzBox is not delivering the call. Check `fritz.box → Telefonie → Telefoniegeräte → receptionist → Rufnummern (eingehend)` — the dialed number must be ticked there. Then check `Telefonie → Anrufbeantworter` for the same number; the AB must not pick up before (or instead of) the IP phone.
- **`INVITE` arrives but `No matching endpoint` follows** → identify-match in `pjsip.conf` doesn't cover the source IP. The Docker-on-Mac fix is gotcha #4 in [docs/phase1-m1-fritzbox-setup.md](docs/phase1-m1-fritzbox-setup.md): include both `${FRITZBOX_HOST}` and `192.168.65.0/24` in `[fritzbox-identify]`.
- **`INVITE` arrives, dialplan executes, but caller hears nothing** → AudioSocket can't reach `host.docker.internal:8089`. Confirm `make dev-sip` is running and `lsof -nP -iTCP:8089 -sTCP:LISTEN` shows a `Python` process.

**SIP: registration check**
If unsure whether Asterisk is registered with the FritzBox at all:

```bash
docker exec agent-asterisk asterisk -rx "pjsip show registrations"
# Status should be "Registered" with a positive expiry. "Rejected" = bad password.
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
