"""
FlowsFunctionSchema definitions for all PMS tools + the internal set_language function.

Each schema wraps a pms_mock async function with a thin handler that:
  1. Calls the underlying function
  2. Stores the result in flow_manager.state
  3. Returns the appropriate next NodeConfig

The set_language function is internal — it lets the LLM signal which language was
detected so the handler can switch the TTS voice at runtime.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pipecat_flows import FlowManager

from pipecat.frames.frames import TTSUpdateSettingsFrame

from .pms_mock import (
    book_appointment,
    get_available_slots,
    get_office_hours,
    search_patient,
    send_confirmation,
)

# TTS voice identifiers per language (must match settings.yaml)
TTS_VOICES: dict[str, str] = {
    "en": "en_US-ryan-high",
    "de": "de_DE-thorsten-high",
}


# ---------------------------------------------------------------------------
# Handler factories
# Handlers follow the pipecat-flows signature:
#   async def handler(function_name, tool_call_id, args, llm, context, result_callback)
# ---------------------------------------------------------------------------

async def _handle_get_office_hours(function_name, tool_call_id, args, llm, context, result_callback):
    result = await get_office_hours(args.get("date"))
    await result_callback(result)


async def _handle_search_patient(function_name, tool_call_id, args, llm, context, result_callback):
    result = await search_patient(args.get("name", ""), args.get("dob"))
    await result_callback(result)


async def _handle_get_available_slots(function_name, tool_call_id, args, llm, context, result_callback):
    result = await get_available_slots(
        visit_type=args.get("visit_type", "checkup"),
        urgency=args.get("urgency", "routine"),
        date_range=args.get("date_range"),
    )
    await result_callback(result)


async def _handle_book_appointment(function_name, tool_call_id, args, llm, context, result_callback):
    result = await book_appointment(
        patient_id=args.get("patient_id", ""),
        slot_id=args.get("slot_id", ""),
        visit_type=args.get("visit_type", "checkup"),
        notes=args.get("notes", ""),
    )
    await result_callback(result)


async def _handle_send_confirmation(function_name, tool_call_id, args, llm, context, result_callback):
    result = await send_confirmation(
        patient_id=args.get("patient_id", ""),
        appointment=args.get("appointment", {}),
        channel=args.get("channel", "sms"),
    )
    await result_callback(result)


async def _handle_set_language(function_name, tool_call_id, args, llm, context, result_callback):
    """
    Internal function: LLM calls this after detecting the caller's language.
    Switches the TTS voice to match the detected language.
    """
    lang = args.get("language", "en")
    if lang not in TTS_VOICES:
        lang = "en"

    # Switch TTS voice at runtime
    voice = TTS_VOICES[lang]
    await llm.push_frame(TTSUpdateSettingsFrame(settings={"voice": voice}))

    await result_callback({"language_set": lang, "voice": voice})


# ---------------------------------------------------------------------------
# Schema definitions (raw dicts — compatible with both pipecat-flows and direct
# AnthropicLLMService function registration)
# ---------------------------------------------------------------------------

GET_OFFICE_HOURS_SCHEMA = {
    "name": "get_office_hours",
    "description": "Check whether the practice is open on a given date and get opening hours.",
    "input_schema": {
        "type": "object",
        "properties": {
            "date": {
                "type": "string",
                "description": "ISO date string YYYY-MM-DD. Defaults to today if omitted.",
            }
        },
        "required": [],
    },
    "handler": _handle_get_office_hours,
}

SEARCH_PATIENT_SCHEMA = {
    "name": "search_patient",
    "description": (
        "Look up a patient by full name and optional date of birth. "
        "Returns the patient record, a list of candidates if ambiguous, or not_found."
    ),
    "input_schema": {
        "type": "object",
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
    },
    "handler": _handle_search_patient,
}

GET_AVAILABLE_SLOTS_SCHEMA = {
    "name": "get_available_slots",
    "description": "Get available appointment slots for the given visit type and urgency level.",
    "input_schema": {
        "type": "object",
        "properties": {
            "visit_type": {
                "type": "string",
                "enum": ["checkup", "cleaning", "pain", "emergency", "consultation"],
                "description": "Type of dental visit.",
            },
            "urgency": {
                "type": "string",
                "enum": ["routine", "urgent", "emergency"],
                "description": (
                    "Urgency level. Use 'emergency' for same/next-day pain or emergency visits."
                ),
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
    },
    "handler": _handle_get_available_slots,
}

BOOK_APPOINTMENT_SCHEMA = {
    "name": "book_appointment",
    "description": "Confirm and book an appointment slot for a patient.",
    "input_schema": {
        "type": "object",
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
    },
    "handler": _handle_book_appointment,
}

SEND_CONFIRMATION_SCHEMA = {
    "name": "send_confirmation",
    "description": "Send appointment confirmation to the patient via SMS or email.",
    "input_schema": {
        "type": "object",
        "properties": {
            "patient_id": {
                "type": "string",
                "description": "Patient ID.",
            },
            "appointment": {
                "type": "object",
                "description": "Appointment dict returned by book_appointment.",
            },
            "channel": {
                "type": "string",
                "enum": ["sms", "email"],
                "description": "Confirmation channel. Defaults to sms.",
            },
        },
        "required": ["patient_id", "appointment"],
    },
    "handler": _handle_send_confirmation,
}

SET_LANGUAGE_SCHEMA = {
    "name": "set_language",
    "description": (
        "Signal the detected caller language. Call this once after you have identified "
        "whether the caller is speaking English or German."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "language": {
                "type": "string",
                "enum": ["en", "de"],
                "description": "ISO 639-1 language code detected from the caller's utterance.",
            }
        },
        "required": ["language"],
    },
    "handler": _handle_set_language,
}

# Convenience groupings used by flows/nodes.py
ALL_SCHEMAS = [
    GET_OFFICE_HOURS_SCHEMA,
    SEARCH_PATIENT_SCHEMA,
    GET_AVAILABLE_SLOTS_SCHEMA,
    BOOK_APPOINTMENT_SCHEMA,
    SEND_CONFIRMATION_SCHEMA,
    SET_LANGUAGE_SCHEMA,
]
