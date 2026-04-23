# Phase 1 Build Plan — German Variant

**Status:** Phase 0 POC complete (pushed to GitHub, 2026-04-22). This document captures the agreed scope, decisions, and milestone plan for Phase 1.

**Scope boundary:** Phase 1 targets the **German market only**. The US deployment path in [dental-receptionist-agent-prompt.md](dental-receptionist-agent-prompt.md) remains documented but is out of scope for the current work cycle.

---

## 1. Confirmed decisions

| # | Decision | Value |
|---|---|---|
| 1 | Telephony entry point | Home FritzBox 7362 SL, using one dedicated Telekom MSN |
| 2 | SIP ↔ Pipecat bridge | Asterisk + AudioSocket (lighter than LiveKit, acceptable tradeoff for LAN deployment) |
| 3 | LLM | Stays local — Ollama `qwen2.5:14b` on the dev/prod host |
| 4 | Language | German primary; EN/DE code-switching retained from Phase 0 |
| 5 | Compliance regime | DSGVO / BDSG; no HIPAA work in this phase |
| 6 | Stack openness | Fully open-source; paid APIs only as fallback with explicit justification |

---

## 2. Target architecture

```
PSTN caller
    │
    ▼
FritzBox 7362 SL                          (LAN: 192.168.178.1)
    ├── Telekom trunk (All-IP, G.711 a-law 8 kHz)
    └── Internal SIP extension "receptionist"
           │  registers as IP phone on FritzBox
           ▼
Asterisk (on the same LAN host as Pipecat)
    ├── PJSIP endpoint → FritzBox
    └── Dialplan → AudioSocket (TCP) to Pipecat
           │
           ▼
Pipecat pipeline  (existing services/receptionist/)
    ├── AudioSocketTransport   (new — replaces SmallWebRTCTransport for SIP path)
    ├── VAD (Silero)
    ├── STT (faster-whisper — re-tuned for 8 kHz a-law)
    ├── LLM (Ollama qwen2.5:14b, localhost:11434)
    ├── TTS (Piper, de_DE-thorsten-high)
    └── Flow manager (pipecat-flows, unchanged from Phase 0)
           │
           ▼
PMS adapter  (mock in M1-M2; real or "human review" queue in M3)
```

The browser/WebRTC path (`SmallWebRTCTransport`) is retained in parallel as a dev loop. The SIP path is additive, not a replacement.

---

## 3. Milestones

### M1 — SIP bridge on dev machine (1–2 weeks)

**Goal:** real phone call into the FritzBox reaches the existing Pipecat flow end-to-end.

Tasks:
- [ ] Install Asterisk 20 (LTS) on the dev host (Debian/Ubuntu or macOS via Homebrew)
- [ ] Configure FritzBox 7362 SL: create an internal IP phone ("Telefoniegerät"), assign credentials, route chosen Telekom MSN (`6190556` or `38534`) inbound to this extension
- [ ] Configure Asterisk PJSIP: register to FritzBox as that extension, codec `alaw` only
- [ ] Implement `AsteriskAudioSocketTransport` for Pipecat (or adopt community implementation if maintained):
  - TCP listener on 127.0.0.1:8089
  - Parses AudioSocket framing (16-bit PCM 8 kHz in, 16-bit PCM 8 kHz out)
  - Upsamples to 16 kHz for Whisper; downsamples TTS output to 8 kHz
- [ ] Asterisk dialplan: `AudioSocket(<uuid>,127.0.0.1:8089)` on the receptionist context
- [ ] Wire a second entry point in `services/receptionist/main.py` that starts the SIP-side pipeline instead of WebRTC, selected by env var `TRANSPORT=sip|webrtc`
- [ ] Call the FritzBox number from a mobile; verify greeting, language detection, booking flow completes

Acceptance:
- Call completes end-to-end without manual intervention
- End-to-end latency (user stops speaking → agent starts speaking) ≤ 1.5 s P95 on phone audio
- Booking reaches `book_appointment` tool call with valid arguments

### M2 — Hardening and DSGVO compliance (2–3 weeks)

**Goal:** safe to point a real friend / pilot dentist at the number.

Tasks:
- [ ] Structured logging with PII redaction (name, DOB, phone → hashed or `[redacted]` in logs; keep full values only in the encrypted transcript store)
- [ ] Metrics: per-call duration, language detected, handoff rate, tool-call success rate, STT confidence histogram, LLM latency P50/P95
- [ ] Call-recording consent flow in TTS ("Dieses Gespräch wird zur Qualitätssicherung aufgezeichnet. Sind Sie einverstanden?") — default **off**, opt-in only
- [ ] Transcript retention policy: 30 days default, configurable; nightly cron to purge
- [ ] Recording retention: 7 days if opt-in taken, otherwise never stored
- [ ] Crash recovery: if the pipeline raises, Asterisk plays a prerecorded "Bitte rufen Sie in einem Moment zurück" before hanging up
- [ ] Re-tune VAD / STT thresholds on 8 kHz a-law real-call samples (Phase 0 values in [voice_config.md](voice_config.md) were for 16 kHz WebRTC)
- [ ] Office-hours end-to-end test with real calls outside hours → after-hours voicemail flow
- [ ] Write DSGVO Verarbeitungsverzeichnis (Art. 30 record of processing activities) entry — append to [compliance_checklist.md](compliance_checklist.md)

Acceptance:
- 20 consecutive test calls without a pipeline crash
- Logs contain no raw PII (manual grep audit of a 24 h log sample)
- Consent flow demonstrably skips recording when user says no

### M3 — PMS integration decision (scope-dependent)

**Goal:** bookings go somewhere useful.

Two paths — choose based on whether a pilot clinic is lined up:

**M3a — "Human review queue" (1 week, no partner needed)**
- [ ] On `book_appointment` tool call: write a booking request to a local SQLite DB + email/Slack notification to the front desk
- [ ] Simple web UI (reuse FastAPI) showing pending requests, accept/reject/reschedule buttons
- [ ] Keeps mock PMS interface, no real calendar integration

**M3b — Real PMS integration (2–6 weeks, needs partner)**
- [ ] Identify pilot practice's PMS (likely one of: Dampsoft DS-Win, CGM Z1, Evident, Charly)
- [ ] Obtain API docs / export interface — typically CSV/XML exchange, BDT, or vendor-specific REST
- [ ] Implement adapter conforming to existing tool signatures in [services/receptionist/tools/pms_mock.py](../services/receptionist/tools/pms_mock.py)
- [ ] Conflict handling: what if the slot was taken between proposal and booking?
- [ ] AVV (Auftragsverarbeitungsvertrag) with the clinic if we process their patient data

Default: **start with M3a**, move to M3b only when a concrete pilot clinic commits.

### M4 — Deploy (1 week)

**Goal:** agent runs unattended, 24/7 within configured office hours.

Tasks:
- [ ] Decide host: on-prem mini-PC at home (simplest, FritzBox is LAN-local) vs. EU VPS with VPN back to the FritzBox (more flexible, more ops)
  - Recommendation: **on-prem mini-PC** (e.g. Beelink / NUC with RTX 4060 or similar, ~€900 one-off) — keeps LLM local, no VPN complexity, keeps DSGVO story trivial
- [ ] systemd units for Asterisk + Pipecat service
- [ ] Docker Compose alternative for easier version bumps
- [ ] Auto-restart + health check ("call the number every 5 min, expect TTS greeting")
- [ ] Backups: weekly snapshot of config + transcript DB to encrypted external disk
- [ ] Runbook in `docs/`:
  - How to silence the agent (disable FritzBox MSN routing → falls back to voicemail)
  - How to forward a live call to a human
  - How to roll back a bad prompt change (git revert + service restart)
  - Who to call when the box dies

Acceptance:
- 72 h unattended uptime with > 50 test calls, zero manual restarts
- Rollback procedure validated by intentionally breaking the prompt and recovering

---

## 4. FritzBox 7362 SL — specific notes

- **Year / firmware:** released 2014, last major Fritz!OS ~7.29. EOL for features, still receives occasional security patches. Check current FW before starting M1 and update if newer available.
- **SIP registrar:** built-in, accepts up to 10 internal IP phones. We need 1. No license required.
- **Codecs:** G.711 a-law for external PSTN trunk; G.722 wideband available for internal IP phones but we'll force a-law to match the trunk and avoid transcoding.
- **Numbers currently configured:**
  - `6190556` (Telekom, All-IP) — **candidate for receptionist**
  - `999999999` (sip.poivy.com, discount VoIP) — leave alone
  - `38534` (Telekom, All-IP) — alternative candidate
- **Configuration path:** _Telefonie → Telefoniegeräte → Neues Gerät einrichten → Telefon (mit und ohne Anrufbeantworter) → LAN/WLAN (IP-Telefon) → assign MSN_
- **Known quirk:** FritzBox sometimes requires a reboot after creating a new IP phone for Asterisk registration to succeed. Build this into the M1 runbook.

---

## 5. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| STT accuracy drops on 8 kHz a-law vs. 16 kHz WebRTC | High | High | Re-tune thresholds in M2; evaluate `large-v3-turbo` vs. `small` on real recordings; fallback is handoff on low confidence (already implemented) |
| FritzBox SIP registration flakiness / dropped calls | Medium | Medium | Asterisk auto-reconnect; monitor registration state; keep WebRTC path as dev fallback |
| Ollama latency spikes on `qwen2.5:14b` without GPU | Medium | High | Benchmark early in M1; if P95 > 1.5 s on target hardware, either add GPU (one-off cost) or move LLM to EU-hosted API (Mistral, Scaleway) — config change only |
| DSGVO: first real patient call captured before consent flow is in place | Medium | High | Do not point a production number at the agent until M2 consent flow is merged and tested |
| Pilot clinic's PMS has no usable API | High | Medium | Default to M3a human-review queue; don't block M4 deploy on M3b |
| User asks for English mid-call with poor a-law quality | Low | Medium | Already handled — language-detect path falls through to handoff if STT confidence drops |

---

## 6. Open questions / deferred

These don't block M1 but should be decided before the corresponding milestone:

- **M2:** call-recording legal text — draft with a German lawyer or use a template (DSGVO-Konferenz example texts)? Cost implication: lawyer ~€300–600 one-off.
- **M3:** which pilot clinic? Needed before M3b scoping begins.
- **M4:** host hardware spec — needs a GPU benchmark result from M1 to pick the right mini-PC.
- **Post-M4:** monitoring / alerting — Prometheus + Grafana on same box, or something lighter (Netdata, Uptime Kuma)?

---

## 7. Cost snapshot

| Item | One-off | Monthly |
|---|---|---|
| FritzBox 7362 SL | Already owned | €0 |
| Telekom MSN for agent | Already paid (flatrate) | €0 |
| Asterisk software | €0 | €0 |
| Pipecat / Whisper / Piper / Ollama | €0 | €0 |
| Dev host electricity (ongoing during dev) | — | ~€5–15 |
| Mini-PC with GPU (M4 target) | €800–1,200 | — |
| Electricity for prod mini-PC (~80 W avg) | — | ~€15–25 |
| Optional: lawyer review of DSGVO flow | €300–600 | — |
| Optional: EU-hosted LLM API (if GPU path fails) | — | €10–30 |
| **Total during dev (M1–M3)** | **~€0** | **< €20** |
| **Total during prod (post-M4)** | **~€1,000 one-off** | **~€20–40** |

---

## 8. Next action

Start **M1, Task 1**: install Asterisk locally and confirm it can register to the FritzBox as an IP phone on the chosen MSN. This is pure plumbing, no Pipecat code changes yet — smallest step that proves the telephony path is reachable.
