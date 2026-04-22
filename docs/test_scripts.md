# Manual Test Scripts — Lena Voice Receptionist

**Application:** Muster Dental Practice — AI Voice Receptionist "Lena"
**URL:** http://localhost:7860 → click "Start Call"
**STT:** faster-whisper (EN/DE auto-detect)
**TTS:** Piper — `en_US-ryan-high` (English) / `de_DE-thorsten-high` (German)
**LLM:** Groq llama-3.3-70b-versatile (or local qwen2.5:14b)

---

## How to use these scripts

1. Start the app: `make dev`
2. Open http://localhost:7860 and click **Start Call**
3. Follow the Caller lines exactly to exercise the intended path
4. After each Lena turn, compare the actual utterance against the **Verify** column
5. At the end of each test, check every **Pass criterion**

**Notation used in System Action column:**

| Notation | Meaning |
|---|---|
| `node: greeting` | FlowManager is now in this node |
| `tool: foo("bar")` | LLM emits a tool call with these arguments |
| `→ node_name` | Flow transitions to this node after the tool returns |
| `TTS: voice_id` | Active Piper voice |
| `state.key = value` | Value written to FlowManager state |

---

## TC-01: Happy Path — New Patient (English)

**Scenario:** A new English-speaking patient calls to book a routine checkup, is not in the system, and accepts the first proposed slot.
**Language:** EN
**Expected outcome:** Appointment booked and SMS confirmation sent; call ends at closing node.
**Preconditions:** Office is open (use a weekday between 08:00–18:00, e.g. Monday 2026-04-20 10:00).

| # | Speaker | Utterance | System Action | Verify |
|---|---------|-----------|---------------|--------|
| 1 | Lena | "Muster Dental Practice, this is Lena speaking, how can I help you?" | `node: greeting`, `TTS: en_US-ryan-high` | Greeting in English; exactly one sentence |
| 2 | Caller | "Hi, I'd like to make an appointment." | STT transcription | - |
| 3 | Lena | "Of course, let me check our availability." | `tool: set_language("en")` → `node: hours_check` | `state.language = "en"`, TTS voice unchanged (already EN) |
| 4 | Lena | "Great, how can I help you today?" | `tool: get_office_hours()` returns `{"open":"08:00","close":"18:00"}` → `node: collect_info` | No closed message; flow enters collect_info |
| 5 | Lena | "Are you a new or existing patient with us?" | — | First collect_info question; no name asked yet |
| 6 | Caller | "I'm new, actually." | STT transcription | - |
| 7 | Lena | "No problem. What is your full name?" | — | One question only |
| 8 | Caller | "My name is Jennifer Blake." | STT transcription | - |
| 9 | Lena | "And your date of birth, please?" | — | Name recorded; DOB question follows |
| 10 | Caller | "September 12th, 1994." | STT transcription | - |
| 11 | Lena | "What phone number can we reach you on?" | — | DOB noted; phone question |
| 12 | Caller | "It's 312-555-0167." | STT transcription | - |
| 13 | Lena | "3-1-2, 5-5-5, 0-1-6-7 — is that correct?" | — | Phone read back digit-by-digit per persona rules |
| 14 | Caller | "Yes, that's right." | STT transcription | - |
| 15 | Lena | "And what is the reason for your visit?" | — | Confirmation acknowledged; visit-reason question |
| 16 | Caller | "Just a routine checkup." | STT transcription | - |
| 17 | Lena | "And are you privately insured, covered by a health plan, or self-pay?" | — | Insurance type question |
| 18 | Caller | "I have a PPO plan with Blue Cross." | STT transcription | - |
| 19 | Lena | — | `tool: search_patient("Jennifer Blake", dob=null)` → returns `{"status":"not_found"}` | search_patient called silently after all 6 fields collected; not-found path |
| 20 | Lena | — | `tool: request_slots(visit_type="checkup", urgency="routine")` → `node: slot_proposal`, `state.proposed_slots = [...]` | 6 slots generated; flow moves to slot_proposal |
| 21 | Lena | "I have Monday, April 20th at 9:00 AM with Dr. Fischer, or Tuesday, April 21st at 10:00 AM with Dr. Braun. Which would you prefer?" | — | Two concrete options presented; no open-ended question |
| 22 | Caller | "The first one works for me." | STT transcription | - |
| 23 | Lena | — | `tool: confirm_slot(slot_id="2026-04-20-0900-checkup")` → `node: confirmation`, `state.chosen_slot = "2026-04-20-0900-checkup"` | Flow moves to confirmation |
| 24 | Lena | "Let me read back the details: Jennifer Blake, date of birth September 12th, 1994, phone 3-1-2-5-5-5-0-1-6-7, checkup on Monday, April 20th at 9:00 AM. Does everything look correct?" | — | All four confirmation fields read back |
| 25 | Caller | "Yes, that's all correct." | STT transcription | - |
| 26 | Lena | — | `tool: book_appointment(patient_id="new", slot_id="2026-04-20-0900-checkup", visit_type="checkup", notes="New patient; PPO/Blue Cross")` → returns `{"status":"confirmed","confirmation_id":"APT-XXXXXXXX"}` | `state.confirmation_id` and `state.booked_appointment` set |
| 27 | Lena | — | `tool: send_confirmation(patient_id="new", channel="sms")` → returns `{"status":"sent","channel":"sms"}` → `node: closing` | SMS sent; flow moves to closing |
| 28 | Lena | "Your appointment is confirmed for Monday, April 20th at 9:00 AM. Have a great day!" | `node: closing` → `post_action: end_conversation` | Closing in English; appointment date/time mentioned; call ends |

**Pass criteria:**
- Greeting is in English with the exact phrase "Muster Dental Practice, this is Lena speaking"
- `set_language("en")` tool call fires after caller's first utterance
- `get_office_hours()` fires immediately on entering hours_check; no "I'm checking" preamble
- Collect_info proceeds one question per turn in the specified order (status → name → DOB → phone → reason → insurance)
- Phone number is read back digit-by-digit before confirmation
- `search_patient` is called silently after all 6 fields are collected
- `request_slots` is called with `visit_type="checkup"` and `urgency="routine"`
- Two concrete slots are proposed; LLM does not ask "when works for you?"
- `book_appointment` fires after verbal confirmation; `send_confirmation` fires immediately after
- Closing mentions the appointment date and time; call terminates

---

## TC-02: Happy Path — Existing Patient (German)

**Scenario:** An existing German-speaking patient, Anna Schmidt (P003), calls to book a Zahnreinigung (cleaning) and accepts the second of the proposed slots.
**Language:** DE
**Expected outcome:** Appointment booked for the second slot; SMS confirmation sent; call ends in German.
**Preconditions:** Office is open (e.g. Thursday 2026-04-23 09:30).

| # | Speaker | Utterance | System Action | Verify |
|---|---------|-----------|---------------|--------|
| 1 | Lena | "Zahnarztpraxis Muster, hier ist Lena, was kann ich für Sie tun?" | `node: greeting`, `TTS: de_DE-thorsten-high` | Greeting in German with formal "Sie" |
| 2 | Caller | "Guten Morgen, ich würde gerne einen Termin machen." | STT transcription | - |
| 3 | Lena | "Sehr gerne, einen Moment bitte." | `tool: set_language("de")` → `node: hours_check`, `state.language = "de"`, `TTSUpdateSettingsFrame(voice="de_DE-thorsten-high")` | Voice confirmed German; state.language = "de" |
| 4 | Lena | "Gerne, wie kann ich Ihnen helfen?" | `tool: get_office_hours()` returns `{"open":"08:00","close":"18:00"}` → `node: collect_info` | No closed message; German friendly acknowledgement |
| 5 | Lena | "Sind Sie bereits Patient bei uns, oder sind Sie neu bei uns?" | — | First collect_info question in German with formal "Sie" |
| 6 | Caller | "Ich bin schon Patientin bei Ihnen." | STT transcription | - |
| 7 | Lena | "Wie ist Ihr vollständiger Name, bitte?" | — | Existing patient path; name question |
| 8 | Caller | "Anna Schmidt." | STT transcription | - |
| 9 | Lena | "Und Ihr Geburtsdatum?" | — | DOB question; no spelling check for common surname |
| 10 | Caller | "Am zweiundzwanzigsten Juli 1992." | STT transcription | - |
| 11 | Lena | — | `tool: search_patient("Anna Schmidt", dob="1992-07-22")` → returns `{"status":"found","patient":{"id":"P003","full_name":"Anna Schmidt","dob":"1992-07-22","insurance":"DKV","insurance_type":"PKV",...}}` | Called silently after name + DOB; found immediately |
| 12 | Lena | "Ich sehe, Sie sind bereits bei uns, Frau Schmidt. Welche Telefonnummer können wir für Sie hinterlegen?" | — | Patient confirmed with formal address; phone question next |
| 13 | Caller | "Das ist null-acht-neun, neun-acht-sieben-sechs-fünf-vier-drei." | STT transcription | - |
| 14 | Lena | "null-acht-neun, neun-acht-sieben-sechs-fünf-vier-drei — ist das korrekt?" | — | Phone read back in digit groups per persona rules |
| 15 | Caller | "Ja, das stimmt." | STT transcription | - |
| 16 | Lena | "Was ist der Grund Ihres Besuchs?" | — | Visit-reason question |
| 17 | Caller | "Ich möchte eine Zahnreinigung." | STT transcription | - |
| 18 | Lena | "Sind Sie bei einer gesetzlichen Krankenkasse, privat versichert, oder zahlen Sie selbst?" | — | Insurance question |
| 19 | Caller | "Ich bin privat versichert, bei der DKV." | STT transcription | - |
| 20 | Lena | — | `tool: request_slots(visit_type="cleaning", urgency="routine")` → `node: slot_proposal`, `state.proposed_slots = [6 slots]` | All 6 fields collected; request_slots called; 45-min duration used |
| 21 | Lena | "Ich hätte Montag, den 20. April um 9:00 Uhr bei Dr. Fischer, oder Dienstag, den 21. April um 10:00 Uhr bei Dr. Braun. Welcher Termin passt Ihnen besser?" | — | Two slots presented in German; spoken dates ("am zwanzigsten April") |
| 22 | Caller | "Der zweite Termin wäre besser für mich." | STT transcription | - |
| 23 | Lena | — | `tool: confirm_slot(slot_id="2026-04-21-1000-cleaning")` → `node: confirmation`, `state.chosen_slot = "2026-04-21-1000-cleaning"` | Second slot selected; flow moves to confirmation |
| 24 | Lena | "Ich lese Ihnen die Termindetails vor: Anna Schmidt, Geburtsdatum am zweiundzwanzigsten Juli 1992, Telefon null-acht-neun-neun-acht-sieben-sechs-fünf-vier-drei, Zahnreinigung am Dienstag, dem 21. April um 10:00 Uhr. Ist alles korrekt?" | — | All four confirmation fields; German spoken date format |
| 25 | Caller | "Ja, alles stimmt." | STT transcription | - |
| 26 | Lena | — | `tool: book_appointment(patient_id="P003", slot_id="2026-04-21-1000-cleaning", visit_type="cleaning", notes="")` → returns `{"status":"confirmed"}` | Booking uses correct patient_id "P003" |
| 27 | Lena | — | `tool: send_confirmation(patient_id="P003", channel="sms")` → returns `{"status":"sent"}` → `node: closing` | Confirmation SMS sent; flow moves to closing |
| 28 | Lena | "Ihr Termin ist bestätigt für Dienstag, den 21. April um 10:00 Uhr. Auf Wiederhören!" | `node: closing` → `post_action: end_conversation` | Closing in German; formal "Auf Wiederhören"; call ends |

**Pass criteria:**
- Entire conversation is in German with formal "Sie" throughout
- TTS voice is `de_DE-thorsten-high` from turn 1
- `search_patient` fires after name + DOB are collected (not after all 6 fields)
- Patient is confirmed with "Ich sehe, Sie sind bereits bei uns" or equivalent
- `request_slots` uses `visit_type="cleaning"` (not "checkup")
- Second slot is correctly identified and its slot_id passed to `confirm_slot`
- `book_appointment` uses `patient_id="P003"`
- Closing uses "Auf Wiederhören" and mentions the confirmed date/time

---

## TC-03: After-Hours — Office Closed (German)

**Scenario:** A German caller rings on a Saturday when the practice is closed.
**Language:** DE
**Expected outcome:** Caller is informed the practice is closed, receives the German emergency number, and the call ends at closing.
**Preconditions:** System date is Saturday (e.g. 2026-04-18 14:00); `OFFICE_LOCALE=de`.

| # | Speaker | Utterance | System Action | Verify |
|---|---------|-----------|---------------|--------|
| 1 | Lena | "Zahnarztpraxis Muster, hier ist Lena, was kann ich für Sie tun?" | `node: greeting`, `TTS: de_DE-thorsten-high` | German greeting |
| 2 | Caller | "Hallo, ich brauche einen Termin." | STT transcription | - |
| 3 | Lena | "Natürlich, einen Moment." | `tool: set_language("de")` → `node: hours_check`, `state.language = "de"` | Language set to German |
| 4 | Lena | "Unsere Praxis ist derzeit nicht erreichbar. Für zahnärztliche Notfälle rufen Sie bitte +49-800-111-2222 an. Wir rufen Sie gerne am nächsten Werktag zurück." | `tool: get_office_hours()` returns `{"closed":true,"emergency_number":"+49-800-111-2222","message":"Unsere Praxis..."}` → `node: closing` | Closed message with German emergency number; no collect_info entered |
| 5 | Lena | "Ich wünsche Ihnen noch einen schönen Tag. Auf Wiederhören!" | `node: closing` → `post_action: end_conversation` | Warm closing; call terminates immediately |

**Pass criteria:**
- `get_office_hours()` returns `closed: true` for Saturday
- Flow goes directly `hours_check → closing` without entering `collect_info`
- Emergency number `+49-800-111-2222` is spoken aloud in Lena's turn 4
- No appointment collection steps occur
- Call terminates after closing

---

## TC-04: After-Hours — Office Closed (English)

**Scenario:** An English caller rings on a Sunday evening.
**Language:** EN
**Expected outcome:** Caller receives the English emergency number; call ends.
**Preconditions:** System date is Sunday (e.g. 2026-04-19 20:00); `OFFICE_LOCALE=en` or US-locale emergency number in response.

| # | Speaker | Utterance | System Action | Verify |
|---|---------|-----------|---------------|--------|
| 1 | Lena | "Muster Dental Practice, this is Lena speaking, how can I help you?" | `node: greeting`, `TTS: en_US-ryan-high` | English greeting |
| 2 | Caller | "Hi, I need to schedule an appointment." | STT transcription | - |
| 3 | Lena | "Of course, one moment." | `tool: set_language("en")` → `node: hours_check`, `state.language = "en"` | Language set |
| 4 | Lena | "Our practice is currently closed. For dental emergencies, please call +1-800-555-0123. We will call you back on the next business day." | `tool: get_office_hours()` returns `{"closed":true,"emergency_number":"+1-800-555-0123","message":"Our practice..."}` → `node: closing` | English emergency number spoken; closed message in English |
| 5 | Lena | "Thank you for calling. Have a good evening!" | `node: closing` → `post_action: end_conversation` | English closing; call ends |

**Pass criteria:**
- `get_office_hours()` returns `closed: true` for Sunday
- English emergency number `+1-800-555-0123` is included in Lena's message
- No collect_info entered
- Call terminates after closing

---

## TC-05: Ambiguous Patient Disambiguation

**Scenario:** A German caller says the surname "Müller". The system returns two candidates (Thomas Müller P001 and Tobias Müller P002). The agent asks for the DOB, the caller provides 1985-03-15, resolving to Thomas Müller (P001), and continues to booking.
**Language:** DE
**Expected outcome:** Thomas Müller (P001) correctly identified; appointment booking proceeds.
**Preconditions:** Office is open (e.g. Tuesday 2026-04-21 11:00).

| # | Speaker | Utterance | System Action | Verify |
|---|---------|-----------|---------------|--------|
| 1 | Lena | "Zahnarztpraxis Muster, hier ist Lena, was kann ich für Sie tun?" | `node: greeting`, `TTS: de_DE-thorsten-high` | German greeting |
| 2 | Caller | "Hallo, ich möchte einen Termin vereinbaren." | STT transcription | - |
| 3 | Lena | "Sehr gerne." | `tool: set_language("de")` → `node: hours_check` | - |
| 4 | Lena | "Gerne, wie kann ich Ihnen helfen?" | `tool: get_office_hours()` → open → `node: collect_info` | Office open; collect_info entered |
| 5 | Lena | "Sind Sie bereits Patient bei uns, oder neu?" | — | First question |
| 6 | Caller | "Ich bin schon Patient." | STT transcription | - |
| 7 | Lena | "Wie ist Ihr vollständiger Name, bitte?" | — | - |
| 8 | Caller | "Müller." | STT transcription | - |
| 9 | Lena | "Und Ihr Geburtsdatum, damit ich Sie im System finden kann?" | `tool: search_patient("Müller", dob=null)` → returns `{"status":"multiple","candidates":[{"id":"P001","full_name":"Thomas Müller","dob":"1985-03-15"},{"id":"P002","full_name":"Tobias Müller","dob":"1990-11-02"}]}` | Multiple match returned; agent asks for DOB to disambiguate; does NOT reveal the other patient's name |
| 10 | Caller | "Am fünfzehnten März 1985." | STT transcription | - |
| 11 | Lena | — | `tool: search_patient("Müller", dob="1985-03-15")` → returns `{"status":"found","patient":{"id":"P001","full_name":"Thomas Müller",...}}` | Second search with DOB resolves to P001 |
| 12 | Lena | "Ich sehe, Sie sind bereits bei uns, Herr Müller. Welche Telefonnummer sollen wir verwenden?" | — | Patient confirmed as Thomas Müller; collect_info continues |
| 13 | Caller | "null-eins-sieben-sechs, eins-zwei-drei-vier-fünf-sechs-sieben." | STT transcription | - |
| 14 | Lena | "null-eins-sieben-sechs-eins-zwei-drei-vier-fünf-sechs-sieben — ist das korrekt?" | — | Phone read back |
| 15 | Caller | "Ja." | STT transcription | - |
| 16 | Lena | "Was ist der Grund Ihres Besuchs?" | — | Visit reason |
| 17 | Caller | "Ich hätte gerne einen Kontrolltermin." | STT transcription | - |
| 18 | Lena | "Und Ihre Krankenkasse?" | — | Insurance |
| 19 | Caller | "Ich bin bei der AOK." | STT transcription | - |
| 20 | Lena | — | `tool: request_slots(visit_type="checkup", urgency="routine")` → `node: slot_proposal` | All fields collected; proceed to slot proposal |
| 21 | Lena | "Ich hätte Dienstag, den 21. April um 9:00 Uhr bei Dr. Fischer, oder Donnerstag, den 23. April um 10:00 Uhr bei Dr. Braun. Welcher Termin passt?" | — | Slots proposed |
| 22 | Caller | "Dienstag passt mir gut." | STT transcription | - |
| 23 | Lena | — | `tool: confirm_slot(slot_id="2026-04-21-0900-checkup")` → `node: confirmation` | - |

**Pass criteria:**
- First `search_patient("Müller")` without DOB returns `status: "multiple"`
- Agent asks for DOB without revealing the other candidate's name
- Second `search_patient("Müller", dob="1985-03-15")` returns `status: "found"` for Thomas Müller (P001)
- `book_appointment` (in subsequent turns) uses `patient_id="P001"`
- At no point does the agent say "Tobias" or reveal the second candidate's details

---

## TC-06: Emergency / Pain

**Scenario:** An English caller reports severe toothache and describes it as urgent. The agent sets `visit_type="pain"` and `urgency="emergency"`, retrieving slots within the next 2 days.
**Language:** EN
**Expected outcome:** Emergency slots (within 48 hours) presented; caller accepts first slot; appointment booked.
**Preconditions:** Office is open (e.g. Monday 2026-04-20 08:30).

| # | Speaker | Utterance | System Action | Verify |
|---|---------|-----------|---------------|--------|
| 1 | Lena | "Muster Dental Practice, this is Lena speaking, how can I help you?" | `node: greeting`, `TTS: en_US-ryan-high` | - |
| 2 | Caller | "Hello, I'm in a lot of pain — my tooth has been throbbing all night." | STT transcription | - |
| 3 | Lena | "I'm sorry to hear that, let me check our availability right away." | `tool: set_language("en")` → `node: hours_check` | Empathetic acknowledgement; no "I'm checking" preamble |
| 4 | Lena | "I'd like to help. Are you a patient with us?" | `tool: get_office_hours()` → open → `node: collect_info` | Office open; collect_info |
| 5 | Caller | "No, I'm new here." | STT transcription | - |
| 6 | Lena | "What is your full name?" | — | - |
| 7 | Caller | "David Park." | STT transcription | - |
| 8 | Lena | "And your date of birth?" | — | - |
| 9 | Caller | "July 4th, 1987." | STT transcription | - |
| 10 | Lena | "What phone number should we use?" | — | - |
| 11 | Caller | "415-555-0189." | STT transcription | - |
| 12 | Lena | "4-1-5, 5-5-5, 0-1-8-9 — is that right?" | — | Phone read back |
| 13 | Caller | "Yes." | STT transcription | - |
| 14 | Lena | "You mentioned tooth pain — how would you describe the urgency?" | — | Agent probes for urgency given pain reason already stated |
| 15 | Caller | "It's very severe, I can barely sleep. I need to be seen as soon as possible." | STT transcription | - |
| 16 | Lena | "Understood. Do you have dental insurance, or are you self-pay?" | — | Insurance question |
| 17 | Caller | "I have an HMO plan." | STT transcription | - |
| 18 | Lena | — | `tool: search_patient("David Park")` → returns `{"status":"not_found"}` | New patient; not found |
| 19 | Lena | — | `tool: request_slots(visit_type="pain", urgency="emergency", date_range={"start":"2026-04-20","end":"2026-04-22"})` → returns slots within 48 h → `node: slot_proposal` | `urgency="emergency"` triggers 2-day window; `visit_type="pain"` |
| 20 | Lena | "I have today, April 20th at 2:00 PM with Dr. Fischer, or tomorrow, April 21st at 9:00 AM with Dr. Braun. Which would you prefer?" | — | Only near-term slots offered |
| 21 | Caller | "Today at 2 PM, please." | STT transcription | - |
| 22 | Lena | — | `tool: confirm_slot(slot_id="2026-04-20-1400-pain")` → `node: confirmation` | - |
| 23 | Lena | "Let me confirm: David Park, July 4th 1987, phone 4-1-5-5-5-5-0-1-8-9, tooth pain appointment today April 20th at 2:00 PM. Does that look right?" | — | Full confirmation read-back |
| 24 | Caller | "Yes, perfect." | STT transcription | - |
| 25 | Lena | — | `tool: book_appointment(patient_id="new", slot_id="2026-04-20-1400-pain", visit_type="pain", notes="New patient; severe tooth pain; HMO")` → confirmed | - |
| 26 | Lena | — | `tool: send_confirmation(patient_id="new", channel="sms")` → sent → `node: closing` | - |
| 27 | Lena | "Your appointment is confirmed for today at 2:00 PM. We look forward to seeing you — feel better soon!" | `post_action: end_conversation` | Empathetic closing |

**Pass criteria:**
- `request_slots` is called with `visit_type="pain"` and `urgency="emergency"`
- Returned slots are all within the next 48 hours (not a 14-day window)
- Agent does not suggest home remedies or give any medical advice
- Slot at 2:00 PM on the same day is offered and booked

---

## TC-07: Human Transfer — Explicit Request

**Scenario:** An English caller immediately asks to speak to a real person.
**Language:** EN
**Expected outcome:** Agent triggers transfer_to_human without resistance; warm handoff message; call ends.
**Preconditions:** Office is open.

| # | Speaker | Utterance | System Action | Verify |
|---|---------|-----------|---------------|--------|
| 1 | Lena | "Muster Dental Practice, this is Lena speaking, how can I help you?" | `node: greeting`, `TTS: en_US-ryan-high` | - |
| 2 | Caller | "I want to speak to a real person, please." | STT transcription | - |
| 3 | Lena | "Of course." | `tool: set_language("en")` → `node: hours_check` | Language set; no resistance |
| 4 | Lena | — | `tool: get_office_hours()` → open → `node: collect_info` | Office open; enters collect_info |
| 5 | Lena | "I'll connect you with a colleague right away." | `tool: transfer_to_human(reason="caller_requested")` → `node: handoff`, `state.handoff_reason = "caller_requested"` | Immediate transfer; no appointment collection attempted |
| 6 | Lena | — | `tool: complete_handoff()` → `node: closing` | Handoff confirmed |
| 7 | Lena | "Thank you for calling Muster Dental Practice. Have a great day!" | `node: closing` → `post_action: end_conversation` | Brief closing; call ends |

**Pass criteria:**
- Agent does not attempt to collect any patient information before transferring
- `transfer_to_human(reason="caller_requested")` is called as soon as the request is detected
- Agent does not argue, explain, or ask why
- Handoff message is warm and concise (one sentence)
- `complete_handoff()` fires and call terminates at closing

---

## TC-08: Human Transfer — Medical Question

**Scenario:** A German caller describes symptoms (swelling and pain for three days) that constitute a medical question. The agent recognises this is outside its scope and transfers to a human.
**Language:** DE
**Expected outcome:** Agent declines to assess the symptoms, transfers to human colleague.
**Preconditions:** Office is open.

| # | Speaker | Utterance | System Action | Verify |
|---|---------|-----------|---------------|--------|
| 1 | Lena | "Zahnarztpraxis Muster, hier ist Lena, was kann ich für Sie tun?" | `node: greeting`, `TTS: de_DE-thorsten-high` | German greeting |
| 2 | Caller | "Ich habe seit drei Tagen Schwellungen und starke Schmerzen im Kiefer. Ist das etwas Ernstes?" | STT transcription | - |
| 3 | Lena | "Ich verstehe, das klingt unangenehm." | `tool: set_language("de")` → `node: hours_check` | Language set; empathetic but no diagnosis |
| 4 | Lena | — | `tool: get_office_hours()` → open → `node: collect_info` | Office open |
| 5 | Lena | "Ich kann keine medizinische Einschätzung geben, aber ich verbinde Sie sofort mit einer Kollegin, die Ihnen besser helfen kann." | `tool: transfer_to_human(reason="medical_question")` → `node: handoff`, `state.handoff_reason = "medical_question"` | No diagnosis given; transfer fired immediately |
| 6 | Lena | — | `tool: complete_handoff()` → `node: closing` | Handoff complete |
| 7 | Lena | "Vielen Dank für Ihren Anruf. Auf Wiederhören!" | `node: closing` → `post_action: end_conversation` | German closing; call ends |

**Pass criteria:**
- Agent gives zero medical information, diagnosis, or reassurance about severity
- `transfer_to_human(reason="medical_question")` is called before collecting any booking information
- Agent's message before transfer is in German with formal "Sie"
- Entire call is conducted in German
- Call ends after `complete_handoff` → closing

---

## TC-09: Human Transfer — Rescheduling Request

**Scenario:** An English caller wants to reschedule an existing appointment. Rescheduling is outside the agent's scope.
**Language:** EN
**Expected outcome:** Agent acknowledges it cannot handle rescheduling, offers to connect to a colleague, transfers to human.
**Preconditions:** Office is open.

| # | Speaker | Utterance | System Action | Verify |
|---|---------|-----------|---------------|--------|
| 1 | Lena | "Muster Dental Practice, this is Lena speaking, how can I help you?" | `node: greeting`, `TTS: en_US-ryan-high` | - |
| 2 | Caller | "Hi, I need to reschedule my appointment from last week." | STT transcription | - |
| 3 | Lena | "I can help with that." | `tool: set_language("en")` → `node: hours_check` | - |
| 4 | Lena | "Great, how can I help?" | `tool: get_office_hours()` → open → `node: collect_info` | Office open |
| 5 | Lena | "I can only help with booking new appointments — I'll connect you with a colleague who can reschedule your visit." | `tool: transfer_to_human(reason="reschedule")` → `node: handoff`, `state.handoff_reason = "reschedule"` | Explains limitation in one sentence; no argument; transfer called |
| 6 | Lena | "I'll connect you right away." | — | Warm handoff in handoff node |
| 7 | Lena | — | `tool: complete_handoff()` → `node: closing` | - |
| 8 | Lena | "Thank you for calling. Have a great day!" | `node: closing` → `post_action: end_conversation` | Brief closing; call ends |

**Pass criteria:**
- Agent correctly identifies rescheduling as outside scope
- `transfer_to_human(reason="reschedule")` is called; reason enum value matches exactly
- Agent does not attempt to collect any appointment information
- Explanation is one concise sentence — no lengthy apology
- Call terminates after closing

---

## TC-10: Booking on Behalf of a Child (Pediatric Patient)

**Scenario:** A parent calls to book an appointment for their daughter Emma Wilson (P008), who is a pediatric patient (DOB 2010-02-28, PPO/Cigna). The caller clarifies they are calling on the child's behalf.
**Language:** EN
**Expected outcome:** Emma Wilson (P008) correctly found; appointment booked with parent-accompanies note.
**Preconditions:** Office is open (e.g. Friday 2026-04-24 10:00).

| # | Speaker | Utterance | System Action | Verify |
|---|---------|-----------|---------------|--------|
| 1 | Lena | "Muster Dental Practice, this is Lena speaking, how can I help you?" | `node: greeting`, `TTS: en_US-ryan-high` | - |
| 2 | Caller | "Hi, I'd like to book an appointment for my daughter." | STT transcription | - |
| 3 | Lena | "Of course, I can help with that." | `tool: set_language("en")` → `node: hours_check` | - |
| 4 | Lena | "Of course, how can I help?" | `tool: get_office_hours()` → open → `node: collect_info` | Office open |
| 5 | Lena | "Is your daughter already a patient with us, or is this her first visit?" | — | Agent adapts question to context (calling for daughter) |
| 6 | Caller | "Yes, she's been here before. Her name is Emma Wilson." | STT transcription | - |
| 7 | Lena | "And what is her date of birth?" | — | DOB for the patient (the child) |
| 8 | Caller | "February 28th, 2010." | STT transcription | - |
| 9 | Lena | — | `tool: search_patient("Emma Wilson", dob="2010-02-28")` → returns `{"status":"found","patient":{"id":"P008","full_name":"Emma Wilson","dob":"2010-02-28","notes":"Pediatric patient — parent accompanies.",...}}` | Patient found; pediatric note visible in record |
| 10 | Lena | "I can see Emma is a patient with us. What phone number should we use for the confirmation?" | — | Confirmed; phone question; note that parent accompanies is acknowledged implicitly |
| 11 | Caller | "2-0-6, 5-5-5, 0-1-3-3." | STT transcription | - |
| 12 | Lena | "2-0-6, 5-5-5, 0-1-3-3 — is that correct?" | — | Phone read back |
| 13 | Caller | "Yes." | STT transcription | - |
| 14 | Lena | "And what is the reason for Emma's visit?" | — | Visit reason for the child |
| 15 | Caller | "She's due for a checkup." | STT transcription | - |
| 16 | Lena | "And what insurance does Emma have?" | — | Insurance |
| 17 | Caller | "She's on my Cigna PPO plan." | STT transcription | - |
| 18 | Lena | — | `tool: request_slots(visit_type="checkup", urgency="routine")` → `node: slot_proposal` | Slots for checkup routine |
| 19 | Lena | "I have Friday, April 24th at 9:00 AM with Dr. Fischer, or Friday, April 24th at 11:00 AM with Dr. Braun. Which works better?" | — | Two slots proposed |
| 20 | Caller | "9 AM on Friday, please." | STT transcription | - |
| 21 | Lena | — | `tool: confirm_slot(slot_id="2026-04-24-0900-checkup")` → `node: confirmation` | - |
| 22 | Lena | "Let me confirm: Emma Wilson, February 28th 2010, phone 2-0-6-5-5-5-0-1-3-3, checkup on Friday April 24th at 9:00 AM. Is everything correct?" | — | Child's details confirmed; parent is calling |
| 23 | Caller | "Yes, that's great, thank you." | STT transcription | - |
| 24 | Lena | — | `tool: book_appointment(patient_id="P008", slot_id="2026-04-24-0900-checkup", visit_type="checkup", notes="Parent calling on behalf of child; parent accompanies.")` → confirmed | `notes` field records parent context |
| 25 | Lena | — | `tool: send_confirmation(patient_id="P008", channel="sms")` → sent → `node: closing` | - |
| 26 | Lena | "Emma's appointment is confirmed for Friday, April 24th at 9:00 AM. Have a great day!" | `post_action: end_conversation` | Closing uses child's first name; call ends |

**Pass criteria:**
- Agent naturally adapts "new or existing patient" question to the child context
- `search_patient("Emma Wilson", dob="2010-02-28")` returns P008 with pediatric note
- `book_appointment` uses `patient_id="P008"` and includes a parent note in `notes`
- Confirmation read-back uses Emma's details (not the parent's)
- No confusion between parent and child identities throughout the call

---

## TC-11: Slot Alternatives — Caller Rejects First Options

**Scenario:** An English caller wants a cleaning appointment but rejects the first three proposed slots because they fall on the wrong days. The agent calls `get_more_slots` with a different date range. The caller accepts a slot from the second batch.
**Language:** EN
**Expected outcome:** `get_more_slots` called at least once; caller accepts slot from second batch; booking completed.
**Preconditions:** Office is open (e.g. Monday 2026-04-20 09:00). Assume first batch covers Mon–Tue, second batch covers Thu–Fri of the same week.

| # | Speaker | Utterance | System Action | Verify |
|---|---------|-----------|---------------|--------|
| 1 | Lena | "Muster Dental Practice, this is Lena speaking, how can I help you?" | `node: greeting`, `TTS: en_US-ryan-high` | - |
| 2 | Caller | "Hi, I'd like to book a cleaning appointment." | STT transcription | - |
| 3 | Lena | "Absolutely." | `tool: set_language("en")` → `node: hours_check` | - |
| 4 | Lena | "Great, how can I help you today?" | `tool: get_office_hours()` → open → `node: collect_info` | Office open |
| 5 | Lena | "Are you a new or existing patient?" | — | - |
| 6 | Caller | "Existing — Sarah Johnson." | STT transcription | - |
| 7 | Lena | "And your date of birth?" | — | - |
| 8 | Caller | "April 10th, 1988." | STT transcription | - |
| 9 | Lena | — | `tool: search_patient("Sarah Johnson", dob="1988-04-10")` → `{"status":"found","patient":{"id":"P004",...}}` | P004 found |
| 10 | Lena | "Welcome back, Sarah. What phone number should we use?" | — | Patient confirmed |
| 11 | Caller | "312-555-0198." | STT transcription | - |
| 12 | Lena | "3-1-2, 5-5-5, 0-1-9-8 — correct?" | — | Phone read back |
| 13 | Caller | "Yes." | STT transcription | - |
| 14 | Lena | "And what is the reason for your visit?" | — | - |
| 15 | Caller | "I'd like a teeth cleaning." | STT transcription | - |
| 16 | Lena | "Do you have dental insurance?" | — | - |
| 17 | Caller | "Blue Cross Blue Shield PPO." | STT transcription | - |
| 18 | Lena | — | `tool: request_slots(visit_type="cleaning", urgency="routine")` → `node: slot_proposal`, first batch: Mon/Tue slots | All fields collected |
| 19 | Lena | "I have Monday, April 20th at 9:00 AM with Dr. Fischer, or Tuesday, April 21st at 10:00 AM with Dr. Braun. Which works for you?" | — | First batch proposed |
| 20 | Caller | "Neither of those days works. Do you have anything later in the week?" | STT transcription | - |
| 21 | Lena | — | `tool: get_more_slots(visit_type="cleaning", urgency="routine", date_range={"start":"2026-04-22","end":"2026-04-28"})` → second batch: Thu/Fri slots → `state.proposed_slots` updated | `get_more_slots` called with a later date range; stays in slot_proposal |
| 22 | Lena | "I also have Thursday, April 23rd at 2:00 PM with Dr. Fischer, or Friday, April 24th at 9:00 AM with Dr. Braun. Would either of those work?" | — | Second batch presented |
| 23 | Caller | "Thursday at 2 PM works perfectly." | STT transcription | - |
| 24 | Lena | — | `tool: confirm_slot(slot_id="2026-04-23-1400-cleaning")` → `node: confirmation` | Slot from second batch confirmed |
| 25 | Lena | "To confirm: Sarah Johnson, April 10th 1988, phone 3-1-2-5-5-5-0-1-9-8, cleaning on Thursday April 23rd at 2:00 PM. Is that right?" | — | Full read-back |
| 26 | Caller | "Yes, perfect." | STT transcription | - |
| 27 | Lena | — | `tool: book_appointment(patient_id="P004", slot_id="2026-04-23-1400-cleaning", visit_type="cleaning", notes="")` → confirmed | - |
| 28 | Lena | — | `tool: send_confirmation(patient_id="P004", channel="sms")` → sent → `node: closing` | - |
| 29 | Lena | "Your appointment is confirmed for Thursday, April 23rd at 2:00 PM. Have a great day!" | `post_action: end_conversation` | - |

**Pass criteria:**
- `request_slots` is called with `visit_type="cleaning"` and `urgency="routine"`
- First batch is presented as concrete options (not a question of "when do you want?")
- `get_more_slots` is called when caller rejects the first batch, with a `date_range` that is later than the first batch
- `state.proposed_slots` is updated with the second batch
- `confirm_slot` uses a slot_id from the second batch (Thursday or Friday)
- `book_appointment` uses `patient_id="P004"`

---

## TC-12: Language Switch Mid-Call

**Scenario:** A caller starts in German. After Lena's first response, the caller switches entirely to English. Lena must follow — `set_language("en")` is called from a non-greeting node (just updates TTS voice, no flow transition).
**Language:** DE → EN
**Expected outcome:** TTS voice switches from German to English mid-call; rest of conversation proceeds in English; booking completed.
**Preconditions:** Office is open (e.g. Thursday 2026-04-23 14:00).

| # | Speaker | Utterance | System Action | Verify |
|---|---------|-----------|---------------|--------|
| 1 | Lena | "Zahnarztpraxis Muster, hier ist Lena, was kann ich für Sie tun?" | `node: greeting`, `TTS: de_DE-thorsten-high` | German greeting (agent defaulted to DE or previous session was DE) |
| 2 | Caller | "Guten Tag, ich brauche einen Termin." | STT transcription | - |
| 3 | Lena | "Gerne, einen Moment." | `tool: set_language("de")` → `node: hours_check`, `state.language = "de"`, `TTSUpdateSettingsFrame(voice="de_DE-thorsten-high")` | Language set to German; flow advances to hours_check |
| 4 | Lena | "Sehr gut, wie kann ich Ihnen helfen?" | `tool: get_office_hours()` → open → `node: collect_info` | Office open; collect_info entered |
| 5 | Lena | "Sind Sie bereits Patient bei uns, oder neu?" | — | First question in German |
| 6 | Caller | "Actually, I'd prefer to speak English if that's okay." | STT transcription | Caller switches to English mid-call |
| 7 | Lena | "Of course, no problem at all." | `tool: set_language("en")` called from `collect_info` node → returns `{"language_set":"en"}, None` (no flow transition) → `TTSUpdateSettingsFrame(voice="en_US-ryan-high")` queued | TTS switches to English; flow stays in collect_info |
| 8 | Lena | "Are you a new or existing patient?" | — | Repeats the unanswered question, now in English |
| 9 | Caller | "I'm new." | STT transcription | - |
| 10 | Lena | "What is your full name?" | — | - |
| 11 | Caller | "Fatima Al-Hassan." | STT transcription | - |
| 12 | Lena | "Could you spell your last name for me?" | — | Non-obvious name; agent asks for spelling per persona rules |
| 13 | Caller | "A-L, hyphen, H-A-S-S-A-N." | STT transcription | - |
| 14 | Lena | "Thank you, Fatima. And your date of birth?" | — | Spelling acknowledged; continues |
| 15 | Caller | "June 14th, 2001." | STT transcription | - |
| 16 | Lena | "What phone number can we reach you on?" | — | - |
| 17 | Caller | "030-555-1234." | STT transcription | - |
| 18 | Lena | "0-3-0, 5-5-5, 1-2-3-4 — is that correct?" | — | Phone read back digit-by-digit |
| 19 | Caller | "Yes." | STT transcription | - |
| 20 | Lena | "And what is the reason for your visit?" | — | - |
| 21 | Caller | "Just a routine checkup." | STT transcription | - |
| 22 | Lena | "Do you have dental insurance?" | — | - |
| 23 | Caller | "Yes, I'm with Barmer." | STT transcription | - |
| 24 | Lena | — | `tool: search_patient("Fatima Al-Hassan")` → `{"status":"found","patient":{"id":"P006",...}}` | Found in system even as "new" caller; collect_info task handles this |
| 25 | Lena | "I can see you're already a patient with us, Fatima." | — | Existing patient confirmed; no disruption |
| 26 | Lena | — | `tool: request_slots(visit_type="checkup", urgency="routine")` → `node: slot_proposal` | All 6 fields collected |
| 27 | Lena | "I have Thursday, April 23rd at 3:00 PM with Dr. Fischer, or Friday, April 24th at 9:00 AM with Dr. Braun. Which would you prefer?" | — | Slots in English |
| 28 | Caller | "Thursday at 3 PM." | STT transcription | - |
| 29 | Lena | — | `tool: confirm_slot(slot_id="2026-04-23-1500-checkup")` → `node: confirmation` | - |
| 30 | Lena | "To confirm: Fatima Al-Hassan, June 14th 2001, phone 0-3-0-5-5-5-1-2-3-4, checkup on Thursday April 23rd at 3:00 PM. Is everything correct?" | — | Full read-back in English |
| 31 | Caller | "Yes, that's right." | STT transcription | - |
| 32 | Lena | — | `tool: book_appointment(patient_id="P006", slot_id="2026-04-23-1500-checkup", visit_type="checkup", notes="")` → confirmed | `patient_id="P006"` |
| 33 | Lena | — | `tool: send_confirmation(patient_id="P006", channel="sms")` → sent → `node: closing` | - |
| 34 | Lena | "Your appointment is confirmed for Thursday, April 23rd at 3:00 PM. Have a great day!" | `post_action: end_conversation` | Closing in English; call ends |

**Pass criteria:**
- Greeting and turns 1–5 are in German with formal "Sie"
- When caller switches to English, agent responds in English immediately (turn 7)
- `set_language("en")` called from `collect_info` node; handler returns `(result, None)` — flow does NOT transition, only TTS voice updates
- `state.language` updates to `"en"` and `TTSUpdateSettingsFrame(voice="en_US-ryan-high")` is queued on `flow_manager.task`
- From turn 8 onwards the entire conversation — including slot proposals, confirmation, and closing — is in English
- `book_appointment` uses `patient_id="P006"` (Fatima's record found despite caller saying "I'm new")

---

## Appendix A: Node Transition Reference

```
greeting
  └─ set_language(lang) ──────────────────────────────────────► hours_check
                                                                      │
                              ┌── get_office_hours() [closed] ────────►──────────────────────► closing
                              └── get_office_hours() [open]  ──────────────────────────────► collect_info
                                                                                                   │
                    ┌─ search_patient() [stay, None] ────────────────────────────────────────────┤
                    ├─ request_slots() ──────────────────────────────────────────────────────► slot_proposal
                    └─ transfer_to_human() ──────────────────────────────────────────────────► handoff
                                                                                                   │
                    ┌─ confirm_slot() ───────────────────────────────────────────────────────► confirmation
                    ├─ get_more_slots() [stay, None] ────────────────────────────────────────┤
                    └─ transfer_to_human() ──────────────────────────────────────────────────► handoff
                                                                                                   │
                    ├─ book_appointment() [stay, None] ──────────────────────────────────────┤
                    ├─ send_confirmation() ──────────────────────────────────────────────────► closing
                    └─ transfer_to_human() ──────────────────────────────────────────────────► handoff
                                                                                                   │
                              complete_handoff() ────────────────────────────────────────────► closing
                                                                                                   │
                              post_action: end_conversation ──────────────────────────────── call ends
```

`set_language(lang)` is available in **every node**. From `greeting` it also advances the flow; from all other nodes it only fires `TTSUpdateSettingsFrame` and returns `None` for the next node.

---

## Appendix B: Mock Patient Quick Reference

| ID | Full Name | DOB | Phone | Insurance | Type | Notes |
|----|-----------|-----|-------|-----------|------|-------|
| P001 | Thomas Müller | 1985-03-15 | +49-176-1234567 | AOK Bayern | GKV | Amalgam allergy; ambiguous pair |
| P002 | Tobias Müller | 1990-11-02 | +49-89-5554433 | TK | GKV | Ambiguous pair with P001 |
| P003 | Anna Schmidt | 1992-07-22 | +49-89-9876543 | DKV | PKV | — |
| P004 | Sarah Johnson | 1988-04-10 | +1-312-555-0198 | Blue Cross Blue Shield | PPO | Prefers mornings |
| P005 | Michael Chen | 1975-09-30 | +1-415-555-0174 | Delta Dental | HMO | — |
| P006 | Fatima Al-Hassan | 2001-06-14 | +49-30-5551234 | Barmer | GKV | — |
| P007 | Klaus Bergmann | 1965-12-03 | +49-711-9998877 | Selbstzahler | Selbstzahler | Prefers Dr. Fischer |
| P008 | Emma Wilson | 2010-02-28 | +1-206-555-0133 | Cigna | PPO | Pediatric; parent accompanies |

---

## Appendix C: Office Hours Reference

| Day | Hours |
|-----|-------|
| Monday | 08:00–18:00 |
| Tuesday | 08:00–18:00 |
| Wednesday | 08:00–13:00 |
| Thursday | 08:00–18:00 |
| Friday | 08:00–15:00 |
| Saturday | Closed |
| Sunday | Closed |

Emergency numbers: DE `+49-800-111-2222` · EN `+1-800-555-0123`

Notable closed dates in 2026: `2026-04-03` (Karfreitag), `2026-04-06` (Ostermontag), `2026-05-01` (Tag der Arbeit/Labour Day), `2026-05-14` (Christi Himmelfahrt), `2026-05-25` (Pfingstmontag/Memorial Day), `2026-07-04` (Independence Day), `2026-10-03` (Deutsche Einheit), `2026-12-25–26` (Weihnachten/Christmas).
