# Voice Configuration — STT / TTS Recommendations by Phase

## Phase 0 — Self-hosted POC (current)

### STT: WhisperSTTService (faster-whisper)

- **Model:** `large-v3-turbo` (multilingual; better accuracy/latency ratio than `large-v3` on CPU)
- **Compute:** `int8` quantization on CPU; switch to `float16` on CUDA GPU for ~4× speedup
- **Language detection:** `language=None` (auto-detect); `TranscriptionFrame` carries `.language` and `.language_probability`
- **EN accuracy:** excellent on standard US/UK English
- **DE accuracy:** excellent on standard High German (Hochdeutsch); degrades with heavy dialect (Bavarian, Saxon), background noise, or children's voices
- **P95 latency (CPU, 3s utterance):** ~400–700ms; on GPU: ~60–100ms

### TTS: PiperTTSService

| Voice                 | Language | Quality     | Notes                                                                      |
| --------------------- | -------- | ----------- | -------------------------------------------------------------------------- |
| `de_DE-thorsten-high` | German   | Medium      | Male, clear, slightly robotic on long sentences. Good for booking phrases. |
| `en_US-ryan-high`     | English  | Medium      | Male, clear.                                                               |
| `de_DE-kerstin-low`   | German   | Low         | Female fallback; faster but lower quality.                                 |
| `en_US-lessac-high`   | English  | Medium-high | Female alternative.                                                        |

- **Synthesis latency:** < 100ms for utterances under 10 words; first-run includes ONNX model load (~1s)
- **Voice switching:** `TTSUpdateSettingsFrame(settings={"voice": "de_DE-thorsten-high"})` at runtime
- **Mitigation for robotic quality:** tune system prompt to enforce short sentences (≤ 2 sentences/turn); robotic artefacts are more noticeable in longer utterances

### VAD: SileroVADAnalyzer

- `stop_secs=0.5` — slightly longer than default (0.3s) to handle hesitation pauses in phone-style speech
- `confidence=0.65` — lower than default to reduce false positives on ambient noise

---

## Phase 1 — US Production

### STT: Deepgram nova-3

- Pipecat extra: `pipecat-ai[deepgram]`
- **Why:** streaming transcription (<50ms latency), superior English accuracy on telephone audio
- **Setup:** replace `WhisperSTTService` with `DeepgramSTTService(api_key=..., model="nova-3")`
- **Language detection:** Deepgram returns detected language in metadata; map to `set_language` call
- **HIPAA:** Deepgram offers HIPAA BAA — confirm before enabling

### TTS: Cartesia Sonic Turbo

- Pipecat extra: `pipecat-ai[cartesia]`
- **Why:** sub-200ms streaming TTS, high English naturalness, suitable for phone
- **Setup:** replace `PiperTTSService` with `CartesiaTTSService(api_key=..., voice_id="...")`
- **Voice ID:** use a professional US English male voice (test 2–3 options for caller preference)

### Telephony: SIP via Twilio (or Retell AI)

- Pipecat supports Twilio SIP via `TwilioTransport` or the generic `WebsocketServerTransport`
- Retell AI: managed SIP + HIPAA BAA; lowest integration friction for Phase 1
- Keep `allow_interruptions=True` — barge-in behavior over SIP requires VAD calibration

---

## Phase 1 — DE Production

### STT: Azure Cognitive Services (German)

- Pipecat extra: `pipecat-ai[azure]`
- **Why:** German-tuned acoustic models; strong on formal Hochdeutsch; good with dental vocabulary
- **Setup:** `AzureSTTService(subscription_key=..., region="germanywestcentral", language="de-DE")`
- **Alternative:** Google STT v2 (`de-DE` model); similar quality, different pricing
- **Data residency:** use `germanywestcentral` or `germanywestcentral` Azure region

### TTS: Azure Neural TTS

- **Voice options to evaluate:**
  | Voice | Gender | Notes |
  |---|---|---|
  | `de-DE-ConradNeural` | Male | Clear, professional, good for medical contexts |
  | `de-DE-KatjaNeural` | Female | Warm, natural; standard German |
  | `de-DE-AmalaNeural` | Female | Slightly younger tone |
- **Test corpus (mandatory before go-live):**
  - 20 German patient surnames: Müller, Schneider, Fischer, Weber, Meyer, Wagner, Becker,
    Schulz, Hoffmann, Schäfer, Koch, Bauer, Richter, Klein, Wolf, Schröder, Neumann,
    Schwarz, Zimmermann, Braun
  - 5 dental terms: Wurzelbehandlung, Bleaching, Karies, Zahnersatz, Implantate
  - Insurance names: AOK Bayern, Techniker Krankenkasse, Barmer, DAK-Gesundheit
  - Date expressions: "am fünfzehnten März", "nächsten Mittwoch"
  - Phone groups: "null-eins-sieben-sechs"
- **SSML workaround:** for mispronounced proper nouns, wrap in `<phoneme>` tags

### Telephony: EU SIP

- Option A: Twilio EU region (`edge=frankfurt`) with EU DPA in place
- Option B: Parloa (Berlin-based, enterprise budget, Azure-hosted in EU)
- Option C: Synthflow (SMB budget, EU hosting available)
- Keep self-hosted Pipecat on Hetzner/Scaleway/OVH for full data-residency control

---

## Phase 1 — M2 DE tuning for 8 kHz phone audio

The Phase 0 VAD and STT thresholds were tuned on 16 kHz WebRTC audio from the
browser demo. Narrowband SIP audio (8 kHz, codec compression, phone-line
noise) has a different VAD signature and systematically lower Whisper
confidence. M2 re-tunes the two thresholds against a 10-call sample of real
German phone calls.

**Methodology:** see [m2-tuning-runbook.md](m2-tuning-runbook.md) for the full
capture → offline-eval → adjust loop. The short version: `CAPTURE_CALLS=true`
writes raw SLIN16 to `logs/captures/`, 10 calls across booking / reschedule /
cancel / edge cases are placed, Whisper is re-run offline to get per-utterance
`no_speech_prob`, and `make metrics` aggregates the distribution.

**Acceptance:** STT confidence (`1 - no_speech_prob`) ≥ 0.70 on ≥ 90% of
utterances in the sample, and no mid-sentence cut-offs on slow German speech.

### Results

Fill in after the 10-call tuning run. Keep one row per tuning round so the
history is visible.

| Date | `vad.stop_secs` | `stt.no_speech_prob` | N utterances | Mean conf | % ≥ 0.70 | Notes |
|---|---|---|---|---|---|---|
| _(Phase 0)_ | 0.5 | 0.5 | — | — | — | Browser/WebRTC baseline; not re-measured on 8 kHz |
| _(pending)_ | ? | ? | — | — | — | M2 run — to be populated after capture |

Once the acceptance threshold is met, copy the final values into
[services/receptionist/config/settings.yaml](../services/receptionist/config/settings.yaml)
and tick T4 in the M2 build plan.

---

## Latency Budget (voice-first target: < 1s P95 end-to-end)

| Component                    | Phase 0 (CPU)     | Phase 1 target                  |
| ---------------------------- | ----------------- | ------------------------------- |
| VAD (end-of-turn detection)  | ~30ms             | ~20ms                           |
| STT (Whisper large-v3-turbo) | 400–700ms         | 50ms (Deepgram/Azure streaming) |
| LLM (Claude Sonnet, cached)  | 200–400ms         | 150–250ms                       |
| TTS first-chunk              | 80–100ms          | 50–80ms (streaming)             |
| **Total P95**                | **~800ms–1200ms** | **< 500ms**                     |

Phase 0 may exceed 1s on CPU without GPU. Acceptable for POC; resolve in Phase 1 via streaming STT.
