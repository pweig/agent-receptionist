"""
NodeConfig factories and FlowsFunctionSchema handlers for the conversation flow.

Every state transition in pipecat_flows must be triggered by a function call return.
Nodes without functions cannot advance the flow. To handle this, nodes that would
naturally have no tools use lightweight "transition" functions that the LLM calls
to signal intent (e.g., set_language, set_intent, select_slot, complete_handoff).

Flow:
  (pipeline start) ──► consent  [two pre-roll tts_say clips: greeting, consent notice]
  consent ──record_consent(true)──► hours_check ──get_office_hours──► intent / closing
  consent ──record_consent(false)──► handoff
  intent ──set_intent("booking")──► collect_info
  intent ──set_intent("reschedule"|"cancel")──► manage_appointment
  intent ──set_intent("other")──► handoff
  collect_info ──search_patient / request_slots──► slot_proposal / handoff
  slot_proposal ──confirm_slot / get_more_slots──► confirmation / slot_proposal / handoff
  confirmation ──book_appointment / send_confirmation──► closing
  manage_appointment ──search_patient + find_patient_appointments──► self (verify + list)
  manage_appointment ──cancel_appointment──► closing
  manage_appointment ──request_slots──► reschedule_slot_proposal
  reschedule_slot_proposal ──confirm_slot──► reschedule_appointment ──► closing
  handoff ──────────────────────────────────────────────────► closing
  closing ─── end_conversation (post_action)
"""

from __future__ import annotations

from pipecat.frames.frames import TTSUpdateSettingsFrame
from pipecat_flows import FlowManager, FlowsFunctionSchema, NodeConfig

from ..prompt import PERSONA_SYSTEM_PROMPT, PREROLL_CONSENT, PREROLL_GREETING, STATE_TASK_MESSAGES
from ..telemetry import append_event, log_from_flow_manager
from ..tools.pms_mock import (
    book_appointment,
    cancel_appointment,
    find_patient_appointments,
    get_available_slots,
    get_office_hours,
    reschedule_appointment,
    search_patient,
    send_confirmation,
)
from ..tools.schemas import (
    BOOK_APPOINTMENT_PROPS,
    CANCEL_APPOINTMENT_PROPS,
    FIND_PATIENT_APPOINTMENTS_PROPS,
    GET_AVAILABLE_SLOTS_PROPS,
    GET_OFFICE_HOURS_PROPS,
    RESCHEDULE_APPOINTMENT_PROPS,
    SEARCH_PATIENT_PROPS,
    SEND_CONFIRMATION_PROPS,
    TTS_VOICES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _role() -> list[dict]:
    return [{"role": "system", "content": PERSONA_SYSTEM_PROMPT}]


def _task(key: str) -> list[dict]:
    return [{"role": "system", "content": STATE_TASK_MESSAGES[key]}]


def _fn(name: str, description: str, properties: dict, required: list, handler) -> FlowsFunctionSchema:
    return FlowsFunctionSchema(
        name=name,
        description=description,
        properties=properties,
        required=required,
        handler=handler,
    )


# ---------------------------------------------------------------------------
# Handlers — consent
# ---------------------------------------------------------------------------

async def _handle_record_consent(args: dict, flow_manager: FlowManager):
    given = bool((args or {}).get("given", False))
    from pipecat.utils.time import time_now_iso8601
    ts = time_now_iso8601()
    flow_manager.state["consent_given"] = given
    flow_manager.state["consent_timestamp"] = ts
    append_event(
        log_path=flow_manager.state.get("event_log_path"),
        session_id=flow_manager.state.get("session_id", "unknown"),
        event="consent",
        consent_given=given,
        caller_id=flow_manager.state.get("caller_id"),
    )
    if not given:
        flow_manager.state["handoff_reason"] = "consent_declined"
        return {"consent": "declined"}, create_handoff_node()
    return {"consent": "given"}, create_hours_check_node()


# ---------------------------------------------------------------------------
# Handlers — PMS tools
# ---------------------------------------------------------------------------

async def _handle_get_office_hours(args: dict, flow_manager: FlowManager):
    result = await get_office_hours((args or {}).get("date"))
    flow_manager.state["office_hours"] = result
    # Only transition from the hours_check node; elsewhere return info without changing state.
    if flow_manager.current_node == "hours_check":
        if result.get("closed"):
            return result, create_closing_node()
        return result, create_intent_node()
    return result, None


async def _handle_search_patient(args: dict, flow_manager: FlowManager):
    a = args or {}
    result = await search_patient(a.get("name", ""), a.get("dob"))
    flow_manager.state["patient_record"] = result
    # Stay in collect_info; LLM handles ambiguous / not_found cases via task message
    return result, None


async def _handle_request_slots(args: dict, flow_manager: FlowManager):
    """LLM calls this when all info is collected; fetches slots and advances to slot_proposal."""
    a = args or {}
    result = await get_available_slots(
        visit_type=a.get("visit_type", "checkup"),
        urgency=a.get("urgency", "routine"),
        date_range=a.get("date_range"),
    )
    flow_manager.state["proposed_slots"] = result.get("slots", [])
    return result, create_slot_proposal_node()


async def _handle_get_more_slots(args: dict, flow_manager: FlowManager):
    """Fetch additional slots when the caller wants alternatives."""
    a = args or {}
    result = await get_available_slots(
        visit_type=a.get("visit_type", "checkup"),
        urgency=a.get("urgency", "routine"),
        date_range=a.get("date_range"),
    )
    flow_manager.state["proposed_slots"] = result.get("slots", [])
    # Stay in slot_proposal
    return result, None


async def _handle_book_appointment(args: dict, flow_manager: FlowManager):
    a = args or {}
    result = await book_appointment(
        patient_id=a.get("patient_id", ""),
        slot_id=a.get("slot_id", ""),
        visit_type=a.get("visit_type", "checkup"),
        notes=a.get("notes", ""),
    )
    if result.get("status") == "confirmed":
        flow_manager.state["confirmation_id"] = result.get("confirmation_id")
        flow_manager.state["booked_appointment"] = result.get("appointment")
    # Stay in confirmation; LLM calls send_confirmation next
    return result, None


async def _handle_find_patient_appointments(args: dict, flow_manager: FlowManager):
    """List upcoming appointments for a verified patient. Stays in manage_appointment."""
    a = args or {}
    result = await find_patient_appointments(patient_id=a.get("patient_id", ""))
    flow_manager.state["patient_appointments"] = result.get("appointments", [])
    return result, None


async def _handle_cancel_appointment(args: dict, flow_manager: FlowManager):
    """Cancel after the caller has confirmed. Transition to closing on success."""
    a = args or {}
    result = await cancel_appointment(confirmation_id=a.get("confirmation_id", ""))
    if result.get("status") == "cancelled":
        cancelled = result.get("appointment", {})
        flow_manager.state["cancelled_appointment"] = cancelled
        log_from_flow_manager(
            flow_manager,
            "cancel_done",
            confirmation_id=cancelled.get("confirmation_id"),
            patient_id=cancelled.get("patient_id"),
            slot_id=cancelled.get("slot_id"),
        )
        return result, create_closing_node()
    # not_found — stay in manage_appointment so LLM can ask for clarification.
    return result, None


async def _handle_request_reschedule_slots(args: dict, flow_manager: FlowManager):
    """Fetch new slots for reschedule; advances to reschedule_slot_proposal."""
    a = args or {}
    result = await get_available_slots(
        visit_type=a.get("visit_type", "checkup"),
        urgency=a.get("urgency", "routine"),
        date_range=a.get("date_range"),
    )
    flow_manager.state["proposed_slots"] = result.get("slots", [])
    return result, create_reschedule_slot_proposal_node()


async def _handle_confirm_reschedule_slot(args: dict, flow_manager: FlowManager):
    """Caller picked a new slot; perform the reschedule and go to closing."""
    a = args or {}
    confirmation_id = flow_manager.state.get("selected_confirmation_id", "")
    new_slot_id = a.get("slot_id", "")
    # Capture the pre-reschedule slot so the event record shows what moved.
    old_slot_id = None
    for appt in flow_manager.state.get("patient_appointments", []):
        if appt.get("confirmation_id") == confirmation_id:
            old_slot_id = appt.get("slot_id")
            break
    result = await reschedule_appointment(
        confirmation_id=confirmation_id,
        new_slot_id=new_slot_id,
    )
    if result.get("status") == "rescheduled":
        rescheduled = result.get("appointment", {})
        flow_manager.state["rescheduled_appointment"] = rescheduled
        log_from_flow_manager(
            flow_manager,
            "reschedule_done",
            confirmation_id=confirmation_id,
            patient_id=rescheduled.get("patient_id"),
            from_slot=old_slot_id,
            to_slot=new_slot_id,
        )
        return result, create_closing_node()
    # slot_taken or not_found — stay in reschedule_slot_proposal so LLM can offer alternatives.
    return result, None


async def _handle_select_appointment(args: dict, flow_manager: FlowManager):
    """Record which of the listed appointments the caller wants to act on."""
    a = args or {}
    flow_manager.state["selected_confirmation_id"] = a.get("confirmation_id", "")
    return {"selected_confirmation_id": a.get("confirmation_id", "")}, None


async def _handle_send_confirmation(args: dict, flow_manager: FlowManager):
    a = args or {}
    booked = flow_manager.state.get("booked_appointment", {})
    result = await send_confirmation(
        patient_id=a.get("patient_id", ""),
        appointment=booked,
        channel=a.get("channel", "sms"),
    )
    if result.get("status") == "sent":
        log_from_flow_manager(
            flow_manager,
            "booking_done",
            confirmation_id=booked.get("confirmation_id"),
            patient_id=a.get("patient_id", ""),
            slot_id=booked.get("slot_id"),
            visit_type=booked.get("visit_type"),
        )
    return result, create_closing_node()


# ---------------------------------------------------------------------------
# Handlers — transition functions (no PMS call; just signal intent to flow)
# ---------------------------------------------------------------------------

async def _handle_set_language(args: dict, flow_manager: FlowManager):
    """Update TTS voice to match detected/requested language.

    Registered as a global function so the LLM can switch voices mid-call if
    the caller code-switches. Never triggers a flow transition — the entry
    point is the consent node (set up directly by main.py after pre-roll TTS).
    """
    lang = (args or {}).get("language", "en")
    if lang not in TTS_VOICES:
        lang = "en"
    flow_manager.state["language"] = lang
    await flow_manager.task.queue_frame(
        TTSUpdateSettingsFrame(settings={"voice": TTS_VOICES[lang]})
    )
    return {"language_set": lang}, None


async def _handle_set_intent(args: dict, flow_manager: FlowManager):
    """LLM calls this after determining caller intent.

    booking               → collect_info (new/returning patient booking)
    reschedule | cancel   → manage_appointment (self-serve flow)
    other                 → handoff (medical, billing, anything else)
    """
    intent = (args or {}).get("intent", "booking")
    flow_manager.state["intent"] = intent
    if intent == "booking":
        return {"intent": intent}, create_collect_info_node()
    if intent in ("reschedule", "cancel"):
        return {"intent": intent}, create_manage_appointment_node()
    return {"intent": intent}, create_handoff_node()


async def _handle_confirm_slot(args: dict, flow_manager: FlowManager):
    """LLM calls this when the caller accepts a slot."""
    a = args or {}
    flow_manager.state["chosen_slot"] = a.get("slot_id")
    return {"slot_confirmed": a.get("slot_id")}, create_confirmation_node()


async def _handle_transfer_to_human(args: dict, flow_manager: FlowManager):
    """Any node can call this to immediately route to handoff."""
    reason = (args or {}).get("reason", "caller_requested")
    flow_manager.state["handoff_reason"] = reason
    log_from_flow_manager(
        flow_manager,
        "llm_handoff",
        reason=reason,
        from_node=flow_manager.current_node,
    )
    return {"handoff": True}, create_handoff_node()


async def _handle_complete_handoff(args: dict, flow_manager: FlowManager):
    return {"done": True}, create_closing_node()


# ---------------------------------------------------------------------------
# Shared transfer-to-human schema (available in every node that needs it)
# ---------------------------------------------------------------------------

def _transfer_fn() -> FlowsFunctionSchema:
    return _fn(
        name="transfer_to_human",
        description=(
            "Transfer the caller to a human receptionist. Call this when the caller "
            "requests a human, asks a medical question, has a billing dispute, or the "
            "conversation cannot proceed. Do NOT call this for reschedule or cancel "
            "requests — those are handled autonomously via the manage_appointment flow."
        ),
        properties={
            "reason": {
                "type": "string",
                "enum": [
                    "caller_requested", "medical_question", "billing_dispute",
                    "ambiguous_patient", "outside_scope", "frustration",
                ],
                "description": "Reason for the transfer.",
            }
        },
        required=["reason"],
        handler=_handle_transfer_to_human,
    )


# ---------------------------------------------------------------------------
# Node factories
# ---------------------------------------------------------------------------

def create_set_language_schema() -> FlowsFunctionSchema:
    """Global function — available in every node so the LLM can switch language at any time."""
    return _fn(
        name="set_language",
        description=(
            "Signal the detected caller language. Call this when the caller's language is "
            "identified or changes. From the greeting node this also advances the conversation; "
            "from any other node it only updates the TTS voice."
        ),
        properties={
            "language": {
                "type": "string",
                "enum": ["en", "de"],
                "description": "ISO 639-1 code: 'en' for English, 'de' for German.",
            }
        },
        required=["language"],
        handler=_handle_set_language,
    )


def create_get_office_hours_schema() -> FlowsFunctionSchema:
    """Global function — only transitions the flow when called from the hours_check node."""
    return _fn(
        name=GET_OFFICE_HOURS_PROPS["name"],
        description=GET_OFFICE_HOURS_PROPS["description"],
        properties=GET_OFFICE_HOURS_PROPS["properties"],
        required=GET_OFFICE_HOURS_PROPS["required"],
        handler=_handle_get_office_hours,
    )




def create_consent_node(lang: str = "de") -> NodeConfig:
    """Entry node for every call.

    Pre-roll tts_say actions play the greeting, then the DSGVO consent notice,
    before the LLM is ever invoked. `respond_immediately: False` keeps the LLM
    silent until the caller actually speaks — the first caller utterance is
    expected to be Ja/Nein, and the LLM's job here is just to call
    record_consent with that answer.
    """
    lang_desc = "German (always use formal 'Sie' address)" if lang == "de" else "English"
    lang_msg = {
        "role": "system",
        "content": (
            f"LANGUAGE LOCK: This call is in {lang_desc}. "
            f"Every single response you produce MUST be in {lang_desc}. "
            "Do not switch languages under any circumstances."
        ),
    }
    greeting_text = PREROLL_GREETING.get(lang, PREROLL_GREETING["en"])
    consent_text = PREROLL_CONSENT.get(lang, PREROLL_CONSENT["en"])
    return {
        "name": "consent",
        "role_messages": _role() + [lang_msg],
        "task_messages": _task("consent"),
        "functions": [
            _fn(
                name="record_consent",
                description=(
                    "Record whether the caller has given or declined consent to automated "
                    "processing. Call this immediately after the caller answers Ja/Yes or "
                    "Nein/No to the consent notice."
                ),
                properties={
                    "given": {
                        "type": "boolean",
                        "description": "true if the caller agreed, false if they declined.",
                    }
                },
                required=["given"],
                handler=_handle_record_consent,
            ),
        ],
        "pre_actions": [
            {"type": "tts_say", "text": greeting_text},
            {"type": "tts_say", "text": consent_text},
        ],
        "respond_immediately": False,
    }


def create_hours_check_node() -> NodeConfig:
    return {
        "name": "hours_check",
        "role_messages": _role(),
        "task_messages": _task("hours_check"),
        "functions": [],
    }


def create_intent_node() -> NodeConfig:
    return {
        "name": "intent",
        "role_messages": _role(),
        "task_messages": _task("intent"),
        "functions": [
            _fn(
                name="set_intent",
                description=(
                    "Signal the caller's intent after asking how you can help. "
                    "Use 'booking' for new appointments, 'reschedule' to change an existing "
                    "appointment, 'cancel' to cancel one, 'other' for everything else."
                ),
                properties={
                    "intent": {
                        "type": "string",
                        "enum": ["booking", "reschedule", "cancel", "other"],
                        "description": (
                            "'booking' for a new appointment, 'reschedule' to move an existing one, "
                            "'cancel' to cancel one, 'other' to transfer to a human."
                        ),
                    }
                },
                required=["intent"],
                handler=_handle_set_intent,
            ),
            _transfer_fn(),
        ],
    }


def create_collect_info_node() -> NodeConfig:
    return {
        "name": "collect_info",
        "role_messages": _role(),
        "task_messages": _task("collect_info"),
        "functions": [
            _fn(
                name=SEARCH_PATIENT_PROPS["name"],
                description=SEARCH_PATIENT_PROPS["description"],
                properties=SEARCH_PATIENT_PROPS["properties"],
                required=SEARCH_PATIENT_PROPS["required"],
                handler=_handle_search_patient,
            ),
            _fn(
                name="request_slots",
                description=(
                    "Call this when all required information has been collected "
                    "(name, DOB, phone, visit reason, insurance). "
                    "Fetches available slots and advances to slot proposal."
                ),
                properties=GET_AVAILABLE_SLOTS_PROPS["properties"],
                required=GET_AVAILABLE_SLOTS_PROPS["required"],
                handler=_handle_request_slots,
            ),
            _transfer_fn(),
        ],
    }


def create_slot_proposal_node() -> NodeConfig:
    return {
        "name": "slot_proposal",
        "role_messages": _role(),
        "task_messages": _task("slot_proposal"),
        "functions": [
            _fn(
                name="confirm_slot",
                description="Confirm the slot the caller has chosen.",
                properties={
                    "slot_id": {
                        "type": "string",
                        "description": "The slot ID from the proposed slots list.",
                    }
                },
                required=["slot_id"],
                handler=_handle_confirm_slot,
            ),
            _fn(
                name="get_more_slots",
                description="Fetch additional slots when the caller wants different options.",
                properties=GET_AVAILABLE_SLOTS_PROPS["properties"],
                required=GET_AVAILABLE_SLOTS_PROPS["required"],
                handler=_handle_get_more_slots,
            ),
            _transfer_fn(),
        ],
    }


def create_confirmation_node() -> NodeConfig:
    return {
        "name": "confirmation",
        "role_messages": _role(),
        "task_messages": _task("confirmation"),
        "functions": [
            _fn(
                name=BOOK_APPOINTMENT_PROPS["name"],
                description=BOOK_APPOINTMENT_PROPS["description"],
                properties=BOOK_APPOINTMENT_PROPS["properties"],
                required=BOOK_APPOINTMENT_PROPS["required"],
                handler=_handle_book_appointment,
            ),
            _fn(
                name=SEND_CONFIRMATION_PROPS["name"],
                description=SEND_CONFIRMATION_PROPS["description"],
                properties=SEND_CONFIRMATION_PROPS["properties"],
                required=SEND_CONFIRMATION_PROPS["required"],
                handler=_handle_send_confirmation,
            ),
            _transfer_fn(),
        ],
    }


def create_manage_appointment_node() -> NodeConfig:
    """Verify patient, list their upcoming appointments, then branch to cancel or
    reschedule. Reused for both intents — the `intent` state field decides the
    path inside the task message."""
    return {
        "name": "manage_appointment",
        "role_messages": _role(),
        "task_messages": _task("manage_appointment"),
        "functions": [
            _fn(
                name=SEARCH_PATIENT_PROPS["name"],
                description=SEARCH_PATIENT_PROPS["description"],
                properties=SEARCH_PATIENT_PROPS["properties"],
                required=SEARCH_PATIENT_PROPS["required"],
                handler=_handle_search_patient,
            ),
            _fn(
                name=FIND_PATIENT_APPOINTMENTS_PROPS["name"],
                description=FIND_PATIENT_APPOINTMENTS_PROPS["description"],
                properties=FIND_PATIENT_APPOINTMENTS_PROPS["properties"],
                required=FIND_PATIENT_APPOINTMENTS_PROPS["required"],
                handler=_handle_find_patient_appointments,
            ),
            _fn(
                name="select_appointment",
                description=(
                    "Record which appointment the caller has chosen from the list "
                    "returned by find_patient_appointments. Call this once the caller "
                    "has picked one — before cancel_appointment or request_slots."
                ),
                properties={
                    "confirmation_id": {
                        "type": "string",
                        "description": "The confirmation_id of the selected appointment.",
                    },
                },
                required=["confirmation_id"],
                handler=_handle_select_appointment,
            ),
            _fn(
                name=CANCEL_APPOINTMENT_PROPS["name"],
                description=CANCEL_APPOINTMENT_PROPS["description"],
                properties=CANCEL_APPOINTMENT_PROPS["properties"],
                required=CANCEL_APPOINTMENT_PROPS["required"],
                handler=_handle_cancel_appointment,
            ),
            _fn(
                name="request_slots",
                description=(
                    "Fetch available new slots for a reschedule. "
                    "Call this only when the caller's intent is 'reschedule' "
                    "and they have selected an existing appointment."
                ),
                properties=GET_AVAILABLE_SLOTS_PROPS["properties"],
                required=GET_AVAILABLE_SLOTS_PROPS["required"],
                handler=_handle_request_reschedule_slots,
            ),
            _transfer_fn(),
        ],
    }


def create_reschedule_slot_proposal_node() -> NodeConfig:
    return {
        "name": "reschedule_slot_proposal",
        "role_messages": _role(),
        "task_messages": _task("reschedule_slot_proposal"),
        "functions": [
            _fn(
                name="confirm_slot",
                description="Confirm the new slot the caller has chosen for the reschedule.",
                properties={
                    "slot_id": {
                        "type": "string",
                        "description": "The slot ID from the proposed slots list.",
                    }
                },
                required=["slot_id"],
                handler=_handle_confirm_reschedule_slot,
            ),
            _fn(
                name="get_more_slots",
                description="Fetch additional slots when the caller wants different options.",
                properties=GET_AVAILABLE_SLOTS_PROPS["properties"],
                required=GET_AVAILABLE_SLOTS_PROPS["required"],
                handler=_handle_get_more_slots,
            ),
            _transfer_fn(),
        ],
    }


def create_handoff_node() -> NodeConfig:
    return {
        "name": "handoff",
        "role_messages": _role(),
        "task_messages": _task("handoff"),
        "functions": [
            _fn(
                name="complete_handoff",
                description="Call this after informing the caller of the transfer or after-hours message.",
                properties={},
                required=[],
                handler=_handle_complete_handoff,
            ),
        ],
    }


def create_closing_node() -> NodeConfig:
    return {
        "name": "closing",
        "role_messages": _role(),
        "task_messages": _task("closing"),
        "functions": [],
        "post_actions": [{"type": "end_conversation"}],
    }
