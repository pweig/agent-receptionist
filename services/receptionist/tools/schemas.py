"""
Raw schema property dicts for all PMS tools and the internal set_language function.
FlowsFunctionSchema instances (with handlers attached) live in flows/nodes.py to
avoid circular imports between schemas ↔ node factories.
"""

# TTS voice identifiers per language (must match settings.yaml)
TTS_VOICES: dict[str, str] = {
    "en": "en_US-ryan-high",
    "de": "de_DE-thorsten-high",
}

# ---------------------------------------------------------------------------
# Raw schema property dicts — imported by flows/nodes.py
# ---------------------------------------------------------------------------

SEARCH_PATIENT_PROPS = {
    "name": "search_patient",
    "description": (
        "Look up a patient by full name and optional date of birth. "
        "Returns the patient record, a list of candidates if ambiguous, or not_found."
    ),
    "properties": {
        "name": {
            "type": "string",
            "description": "Patient full name as provided by the caller.",
        },
        "dob": {
            "type": "string",
            "description": "Date of birth in YYYY-MM-DD format. Omit if not yet collected.",
        },
    },
    "required": ["name"],
}

GET_AVAILABLE_SLOTS_PROPS = {
    "name": "get_available_slots",
    "description": "Get available appointment slots for the given visit type and urgency level.",
    "properties": {
        "visit_type": {
            "type": "string",
            "enum": ["checkup", "cleaning", "pain", "emergency", "consultation"],
            "description": "Type of dental visit.",
        },
        "urgency": {
            "type": "string",
            "enum": ["routine", "urgent", "emergency"],
            "description": "Urgency level. Use 'emergency' for same/next-day pain or emergency visits.",
        },
        "date_range": {
            "type": "object",
            "description": "Optional date range to search within.",
            "properties": {
                "start": {"type": "string", "description": "ISO date, defaults to today."},
                "end": {"type": "string", "description": "ISO date, defaults to today+14d."},
            },
        },
    },
    "required": ["visit_type", "urgency"],
}

BOOK_APPOINTMENT_PROPS = {
    "name": "book_appointment",
    "description": "Confirm and book an appointment slot for a patient.",
    "properties": {
        "patient_id": {
            "type": "string",
            "description": "Patient ID from search_patient result.",
        },
        "slot_id": {
            "type": "string",
            "description": "Slot ID from get_available_slots result.",
        },
        "visit_type": {
            "type": "string",
            "enum": ["checkup", "cleaning", "pain", "emergency", "consultation"],
        },
        "notes": {
            "type": "string",
            "description": "Optional notes from the caller (allergies, special requests).",
        },
    },
    "required": ["patient_id", "slot_id", "visit_type"],
}

SEND_CONFIRMATION_PROPS = {
    "name": "send_confirmation",
    "description": "Send appointment confirmation to the patient via SMS or email.",
    "properties": {
        "patient_id": {
            "type": "string",
            "description": "Patient ID.",
        },
        "channel": {
            "type": "string",
            "enum": ["sms", "email"],
            "description": "Confirmation channel. Defaults to sms.",
        },
    },
    "required": ["patient_id"],
}

FIND_PATIENT_APPOINTMENTS_PROPS = {
    "name": "find_patient_appointments",
    "description": (
        "List the upcoming appointments for a verified patient. "
        "Call this once search_patient has returned a confirmed patient record."
    ),
    "properties": {
        "patient_id": {
            "type": "string",
            "description": "Patient ID from the search_patient result.",
        },
    },
    "required": ["patient_id"],
}

CANCEL_APPOINTMENT_PROPS = {
    "name": "cancel_appointment",
    "description": (
        "Cancel an existing appointment by its confirmation_id. "
        "Only call this after reading the appointment details back to the caller "
        "and receiving explicit confirmation."
    ),
    "properties": {
        "confirmation_id": {
            "type": "string",
            "description": "Confirmation ID from find_patient_appointments.",
        },
    },
    "required": ["confirmation_id"],
}

RESCHEDULE_APPOINTMENT_PROPS = {
    "name": "reschedule_appointment",
    "description": (
        "Move an existing appointment to a new slot. "
        "Call this after the caller has chosen a new slot and confirmed it."
    ),
    "properties": {
        "confirmation_id": {
            "type": "string",
            "description": "Existing appointment's confirmation_id.",
        },
        "new_slot_id": {
            "type": "string",
            "description": "Slot ID from get_available_slots for the new time.",
        },
    },
    "required": ["confirmation_id", "new_slot_id"],
}

SET_LANGUAGE_PROPS = {
    "name": "set_language",
    "description": (
        "Signal the detected caller language. Call this once after you have identified "
        "whether the caller is speaking English or German."
    ),
    "properties": {
        "language": {
            "type": "string",
            "enum": ["en", "de"],
            "description": "ISO 639-1 language code detected from the caller's utterance.",
        }
    },
    "required": ["language"],
}
