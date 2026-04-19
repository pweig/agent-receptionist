# Compliance Checklist

## US — HIPAA

### Agreements
- [ ] Signed Business Associate Agreement (BAA) with telephony provider (Twilio, Vapi, Retell AI)
- [ ] Signed BAA with Anthropic, or replace with a self-hosted LLM to prevent PHI egress
- [ ] Signed BAA with Daily.co (requires Daily.co Business plan — upgrade from free tier before Phase 1)

### Data in transit
- [ ] All WebRTC audio encrypted with DTLS-SRTP (Daily.co default — verify in network audit)
- [ ] All API calls (Anthropic, Daily REST) use TLS 1.2+

### Data at rest
- [ ] Audio recordings stored encrypted at rest (AES-256) with access logs
- [ ] Transcripts stored encrypted at rest; access limited to authorized staff roles

### Minimum Necessary / PII handling
- [ ] Whisper transcription output: PII/PHI redacted before writing to logs
  - Mask: patient name, date of birth, phone number, insurance ID
  - Use regex or a NER model to scrub logs before persistence
- [ ] No SSN, insurance card numbers, or payment data collected via voice
- [ ] Only the data fields required for booking are collected (name, DOB, phone, visit type, insurance type)

### Consent and call recording
- [ ] Verbal consent prompt plays before first utterance is captured:
  - *"This call may be recorded for quality and service improvement purposes."*
  - Log: session_id, timestamp, caller_id (if available), consent_given=true
- [ ] Callers who refuse recording are transferred to a human receptionist

### Data residency
- [ ] Anthropic API endpoint confirmed as US-hosted (or BAA in place for cross-region)
- [ ] Daily.co room region set to us-west-2 or us-east-1
- [ ] Hosting infrastructure (if self-hosted) confirmed as US-based

### Retention and breach
- [ ] Retention policy defined and documented: audio + transcripts purged after [X] days per practice HIPAA policy
- [ ] Automated retention enforcement (e.g., S3 lifecycle policy)
- [ ] Breach notification procedure documented: < 60 days per HHS requirement (45 CFR § 164.404)

### Workforce
- [ ] Front-desk staff trained: what the AI handles, what triggers a handoff, how to receive transferred calls
- [ ] Incident response runbook covers AI-related events (misidentified patient, incorrect booking)

---

## DE — DSGVO / GDPR

### Agreements
- [ ] Auftragsverarbeitungsvertrag (AVV / DPA) signed with:
  - Anthropic (or use EU-hosted LLM to avoid SCCs for cross-border transfer)
  - Telephony provider (Twilio EU region, or EU-native Parloa/Synthflow)
  - Hosting provider (Hetzner, Scaleway, OVH, or Azure Germany North)
  - Daily.co (if used in Phase 1 DE deployment — verify EU datacenter option)
- [ ] Standard Contractual Clauses (SCCs) in place for any US-based processor without EU presence

### Data residency
- [ ] All audio, transcripts, and appointment data stored within EU
- [ ] No cross-border transfer to non-EU countries without SCCs or adequacy decision
- [ ] LLM API calls: either (a) EU endpoint with AVV or (b) self-hosted on EU infrastructure

### Consent (Einwilligung)
- [ ] German consent prompt plays before any audio is captured:
  - *"Dieses Gespräch kann aufgezeichnet werden. Mit der weiteren Nutzung stimmen Sie zu."*
  - Alternative: two-step consent with keypress confirmation
  - Log: session_id, timestamp, consent_given=true
- [ ] Callers who decline are transferred to a human immediately

### Data subject rights
- [ ] Right to erasure (Art. 17 DSGVO): documented process to delete caller audio/transcript on request
- [ ] Right to access (Art. 15): process to provide transcript copy on request
- [ ] Right to rectification (Art. 16): process to correct appointment records

### Documentation
- [ ] Datenschutzerklärung (Privacy Notice) updated to include AI voice processing
- [ ] Verzeichnis der Verarbeitungstätigkeiten (Records of Processing Activities) updated
  - Processing purpose: appointment scheduling
  - Data categories: voice, name, DOB, phone, insurance type
  - Retention: [X] days
  - Recipients: internal PMS system; no third-party marketing
- [ ] DPIA (Datenschutz-Folgenabschätzung) conducted if practice has > 250 employees
  or processes sensitive health data at scale (Art. 35 DSGVO + Art. 9)
- [ ] Art. 22 DSGVO (automated decision-making): appointment booking by AI is not a
  "significant decision" in the Art. 22 sense — document this assessment

### TTS German language validation
- [ ] Validated Piper `de_DE-thorsten-high` pronunciation for the following:
  - 20 common German surnames (Müller, Schneider, Fischer, Weber, Meyer, Wagner,
    Becker, Schulz, Hoffmann, Schäfer, Koch, Bauer, Richter, Klein, Wolf,
    Schröder, Neumann, Schwarz, Zimmermann, Braun)
  - 5 dental terms (Wurzelbehandlung, Bleaching, Karies, Zahnersatz, Implantate)
  - Insurance providers (AOK, TK, Barmer, DAK, BARMER GEK)
  - Dates: "am fünfzehnten März", "am zweiundzwanzigsten Juli"
  - Phone digit groups: "null-eins-sieben-sechs"
- [ ] Any mispronounced terms: use SSML phoneme tags or substitute synonyms in prompt

---

## Phase 0 POC Specific

- [ ] No real patient data in mock PMS — all records use fictional names and phone numbers
- [ ] `.env` is in `.gitignore` — API keys never committed to version control
- [ ] `logs/` directory is gitignored — transcripts not committed
- [ ] Piper model files (`*.onnx`) are gitignored — downloaded locally via `make setup`
- [ ] No cloud logging service configured — logs write to local files only

---

## Phase 1 Pre-Launch Gate

Before any live call is handled, all checkboxes in the relevant section (US or DE) must be ticked.
A compliance sign-off from the practice owner or their legal representative is required.
