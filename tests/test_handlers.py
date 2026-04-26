"""Unit tests for flows/nodes.py handlers — state transitions and side effects."""
import pytest
from unittest.mock import AsyncMock, patch

from services.receptionist.flows.nodes import (
    _handle_record_consent,
    _handle_set_language,
    _handle_search_patient,
    _handle_request_slots,
    _handle_book_appointment,
    _handle_send_confirmation,
    _handle_set_intent,
    _handle_confirm_slot,
    _handle_transfer_to_human,
    _handle_complete_handoff,
    _handle_find_patient_appointments,
    _handle_cancel_appointment,
    _handle_request_reschedule_slots,
    _handle_confirm_reschedule_slot,
    _handle_select_appointment,
    create_collect_info_node,
    create_closing_node,
    create_consent_node,
    create_handoff_node,
    create_intent_node,
    create_slot_proposal_node,
    create_confirmation_node,
    create_manage_appointment_node,
    create_reschedule_slot_proposal_node,
)
from pipecat.frames.frames import TTSUpdateSettingsFrame


# ---------------------------------------------------------------------------
# _handle_set_language
# ---------------------------------------------------------------------------

async def test_set_language_from_non_greeting_no_transition(flow_manager_collect_info):
    result, next_node = await _handle_set_language({"language": "de"}, flow_manager_collect_info)
    assert next_node is None

async def test_set_language_stores_language_in_state(flow_manager):
    await _handle_set_language({"language": "de"}, flow_manager)
    assert flow_manager.state["language"] == "de"

async def test_set_language_queues_tts_update_frame(flow_manager):
    await _handle_set_language({"language": "de"}, flow_manager)
    frames = flow_manager.task.queued_frames
    assert len(frames) == 1
    assert isinstance(frames[0], TTSUpdateSettingsFrame)

async def test_set_language_tts_frame_has_correct_voice_de(flow_manager):
    await _handle_set_language({"language": "de"}, flow_manager)
    frame = flow_manager.task.queued_frames[0]
    assert frame.settings["voice"] == "de_DE-thorsten-high"

async def test_set_language_tts_frame_has_correct_voice_en(flow_manager):
    await _handle_set_language({"language": "en"}, flow_manager)
    frame = flow_manager.task.queued_frames[0]
    assert frame.settings["voice"] == "en_US-ryan-high"

async def test_set_language_unknown_code_falls_back_to_en(flow_manager):
    result, _ = await _handle_set_language({"language": "fr"}, flow_manager)
    assert result["language_set"] == "en"

async def test_set_language_handles_null_args(flow_manager):
    result, _ = await _handle_set_language(None, flow_manager)
    assert result["language_set"] == "en"  # default


# ---------------------------------------------------------------------------
# _handle_search_patient
# ---------------------------------------------------------------------------

_FOUND = {"status": "found", "patient": {"id": "P003", "full_name": "Anna Schmidt"}}
_MULTIPLE = {"status": "multiple", "candidates": [{"id": "P001"}, {"id": "P002"}]}
_NOT_FOUND = {"status": "not_found"}


async def test_search_patient_stores_result_in_state(flow_manager):
    with patch("services.receptionist.flows.nodes.search_patient",
               new=AsyncMock(return_value=_FOUND)):
        await _handle_search_patient({"name": "Anna Schmidt"}, flow_manager)
    assert flow_manager.state["patient_record"] == _FOUND

async def test_search_patient_always_stays_in_node(flow_manager):
    with patch("services.receptionist.flows.nodes.search_patient",
               new=AsyncMock(return_value=_NOT_FOUND)):
        _, next_node = await _handle_search_patient({"name": "Nobody"}, flow_manager)
    assert next_node is None

async def test_search_patient_passes_dob(flow_manager):
    mock_search = AsyncMock(return_value=_FOUND)
    with patch("services.receptionist.flows.nodes.search_patient", new=mock_search):
        await _handle_search_patient({"name": "Müller", "dob": "1985-03-15"}, flow_manager)
    mock_search.assert_called_once_with("Müller", "1985-03-15")


# ---------------------------------------------------------------------------
# _handle_request_slots
# ---------------------------------------------------------------------------

_SLOTS = {"slots": [{"id": "slot-1", "datetime_iso": "2026-04-21T10:00:00"}]}


async def test_request_slots_transitions_to_slot_proposal(flow_manager):
    with patch("services.receptionist.flows.nodes.get_available_slots",
               new=AsyncMock(return_value=_SLOTS)):
        _, next_node = await _handle_request_slots(
            {"visit_type": "checkup", "urgency": "routine"}, flow_manager)
    assert next_node is not None
    task_content = next_node["task_messages"][0]["content"]
    assert "slot" in task_content.lower()

async def test_request_slots_stores_proposed_slots(flow_manager):
    with patch("services.receptionist.flows.nodes.get_available_slots",
               new=AsyncMock(return_value=_SLOTS)):
        await _handle_request_slots({"visit_type": "checkup", "urgency": "routine"}, flow_manager)
    assert flow_manager.state["proposed_slots"] == _SLOTS["slots"]

async def test_request_slots_defaults_on_null_args(flow_manager):
    mock = AsyncMock(return_value=_SLOTS)
    with patch("services.receptionist.flows.nodes.get_available_slots", new=mock):
        await _handle_request_slots(None, flow_manager)
    mock.assert_called_once_with(visit_type="checkup", urgency="routine", date_range=None)


# ---------------------------------------------------------------------------
# _handle_book_appointment
# ---------------------------------------------------------------------------

_CONFIRMED = {
    "status": "confirmed",
    "confirmation_id": "APT-ABCD1234",
    "appointment": {"patient_id": "P001", "slot_id": "slot-1", "visit_type": "checkup"},
}
_SLOT_TAKEN = {"status": "slot_taken"}


async def test_book_appointment_confirmed_stores_ids(flow_manager):
    with patch("services.receptionist.flows.nodes.book_appointment",
               new=AsyncMock(return_value=_CONFIRMED)):
        await _handle_book_appointment(
            {"patient_id": "P001", "slot_id": "slot-1", "visit_type": "checkup"}, flow_manager)
    assert flow_manager.state["confirmation_id"] == "APT-ABCD1234"
    assert flow_manager.state["booked_appointment"]["visit_type"] == "checkup"

async def test_book_appointment_stays_in_confirmation_node(flow_manager):
    with patch("services.receptionist.flows.nodes.book_appointment",
               new=AsyncMock(return_value=_CONFIRMED)):
        _, next_node = await _handle_book_appointment(
            {"patient_id": "P001", "slot_id": "slot-1", "visit_type": "checkup"}, flow_manager)
    assert next_node is None

async def test_book_appointment_slot_taken_no_state_update(flow_manager):
    with patch("services.receptionist.flows.nodes.book_appointment",
               new=AsyncMock(return_value=_SLOT_TAKEN)):
        await _handle_book_appointment(
            {"patient_id": "P001", "slot_id": "slot-1", "visit_type": "checkup"}, flow_manager)
    assert "confirmation_id" not in flow_manager.state


# ---------------------------------------------------------------------------
# _handle_send_confirmation
# ---------------------------------------------------------------------------

_SENT = {"status": "sent", "channel": "sms"}


async def test_send_confirmation_transitions_to_closing(flow_manager):
    flow_manager.state["booked_appointment"] = {"confirmation_id": "APT-01"}
    with patch("services.receptionist.flows.nodes.send_confirmation",
               new=AsyncMock(return_value=_SENT)):
        _, next_node = await _handle_send_confirmation({"patient_id": "P001"}, flow_manager)
    assert next_node is not None
    task_content = next_node["task_messages"][0]["content"]
    assert "Thank" in task_content

async def test_send_confirmation_uses_state_appointment(flow_manager):
    stored_appt = {"confirmation_id": "APT-01", "visit_type": "checkup"}
    flow_manager.state["booked_appointment"] = stored_appt
    mock = AsyncMock(return_value=_SENT)
    with patch("services.receptionist.flows.nodes.send_confirmation", new=mock):
        await _handle_send_confirmation({"patient_id": "P001"}, flow_manager)
    # appointment arg should come from state, not from handler args
    call_kwargs = mock.call_args
    assert call_kwargs.kwargs.get("appointment") == stored_appt or \
           call_kwargs.args[1] == stored_appt


# ---------------------------------------------------------------------------
# _handle_set_intent
# ---------------------------------------------------------------------------

async def test_set_intent_booking_transitions_to_collect_info(flow_manager):
    _, next_node = await _handle_set_intent({"intent": "booking"}, flow_manager)
    assert next_node is not None
    assert next_node.get("name") == "collect_info"

async def test_set_intent_reschedule_transitions_to_manage_appointment(flow_manager):
    _, next_node = await _handle_set_intent({"intent": "reschedule"}, flow_manager)
    assert next_node is not None
    assert next_node.get("name") == "manage_appointment"

async def test_set_intent_cancel_transitions_to_manage_appointment(flow_manager):
    _, next_node = await _handle_set_intent({"intent": "cancel"}, flow_manager)
    assert next_node is not None
    assert next_node.get("name") == "manage_appointment"

async def test_set_intent_other_transitions_to_handoff(flow_manager):
    _, next_node = await _handle_set_intent({"intent": "other"}, flow_manager)
    assert next_node is not None
    assert next_node.get("name") == "handoff"

async def test_set_intent_stores_intent_in_state(flow_manager):
    await _handle_set_intent({"intent": "reschedule"}, flow_manager)
    assert flow_manager.state["intent"] == "reschedule"


# ---------------------------------------------------------------------------
# _handle_confirm_slot
# ---------------------------------------------------------------------------

async def test_confirm_slot_transitions_to_confirmation(flow_manager):
    _, next_node = await _handle_confirm_slot({"slot_id": "slot-1"}, flow_manager)
    assert next_node is not None

async def test_confirm_slot_stores_chosen_slot(flow_manager):
    await _handle_confirm_slot({"slot_id": "slot-1"}, flow_manager)
    assert flow_manager.state["chosen_slot"] == "slot-1"


# ---------------------------------------------------------------------------
# _handle_transfer_to_human
# ---------------------------------------------------------------------------

async def test_transfer_to_human_transitions_to_handoff(flow_manager):
    _, next_node = await _handle_transfer_to_human({"reason": "caller_requested"}, flow_manager)
    assert next_node is not None

async def test_transfer_to_human_stores_reason(flow_manager):
    await _handle_transfer_to_human({"reason": "medical_question"}, flow_manager)
    assert flow_manager.state["handoff_reason"] == "medical_question"

async def test_transfer_to_human_defaults_reason_on_null_args(flow_manager):
    await _handle_transfer_to_human(None, flow_manager)
    assert flow_manager.state["handoff_reason"] == "caller_requested"


# ---------------------------------------------------------------------------
# _handle_complete_handoff
# ---------------------------------------------------------------------------

async def test_complete_handoff_transitions_to_closing(flow_manager):
    _, next_node = await _handle_complete_handoff({}, flow_manager)
    assert next_node is not None
    assert next_node.get("name") == "closing"


# ---------------------------------------------------------------------------
# Reschedule / cancel flow handlers
# ---------------------------------------------------------------------------

_APPT_LIST = {
    "appointments": [
        {
            "confirmation_id": "APT-ABC",
            "slot_id": "2026-05-01-1000-checkup",
            "datetime_iso": "2026-05-01T10:00:00",
            "date_human": "Friday, May 01",
            "time_human": "10:00 AM",
            "visit_type": "checkup",
            "provider": "Dr. Fischer",
        }
    ]
}


async def test_find_patient_appointments_stores_list_in_state(flow_manager):
    with patch("services.receptionist.flows.nodes.find_patient_appointments",
               new=AsyncMock(return_value=_APPT_LIST)):
        _, next_node = await _handle_find_patient_appointments({"patient_id": "P001"}, flow_manager)
    assert next_node is None  # stays in manage_appointment
    assert flow_manager.state["patient_appointments"] == _APPT_LIST["appointments"]


async def test_select_appointment_stores_confirmation_id(flow_manager):
    await _handle_select_appointment({"confirmation_id": "APT-ABC"}, flow_manager)
    assert flow_manager.state["selected_confirmation_id"] == "APT-ABC"


async def test_cancel_appointment_success_transitions_to_closing(flow_manager):
    cancelled = {"status": "cancelled", "appointment": {"confirmation_id": "APT-ABC"}}
    with patch("services.receptionist.flows.nodes.cancel_appointment",
               new=AsyncMock(return_value=cancelled)):
        _, next_node = await _handle_cancel_appointment({"confirmation_id": "APT-ABC"}, flow_manager)
    assert next_node is not None
    assert next_node.get("name") == "closing"
    assert flow_manager.state["cancelled_appointment"]["confirmation_id"] == "APT-ABC"


async def test_cancel_appointment_not_found_stays_in_node(flow_manager):
    not_found = {"status": "not_found"}
    with patch("services.receptionist.flows.nodes.cancel_appointment",
               new=AsyncMock(return_value=not_found)):
        _, next_node = await _handle_cancel_appointment({"confirmation_id": "APT-XXX"}, flow_manager)
    assert next_node is None


async def test_request_reschedule_slots_transitions_to_reschedule_slot_proposal(flow_manager):
    slots = {"slots": [{"id": "s1", "datetime_iso": "2026-05-02T10:00:00"}]}
    with patch("services.receptionist.flows.nodes.get_available_slots",
               new=AsyncMock(return_value=slots)):
        _, next_node = await _handle_request_reschedule_slots(
            {"visit_type": "checkup", "urgency": "routine"}, flow_manager)
    assert next_node is not None
    assert next_node.get("name") == "reschedule_slot_proposal"
    assert flow_manager.state["proposed_slots"] == slots["slots"]


async def test_confirm_reschedule_slot_success_transitions_to_closing(flow_manager):
    flow_manager.state["selected_confirmation_id"] = "APT-ABC"
    rescheduled = {
        "status": "rescheduled",
        "appointment": {"confirmation_id": "APT-ABC", "slot_id": "new-slot"},
    }
    with patch("services.receptionist.flows.nodes.reschedule_appointment",
               new=AsyncMock(return_value=rescheduled)):
        _, next_node = await _handle_confirm_reschedule_slot({"slot_id": "new-slot"}, flow_manager)
    assert next_node is not None
    assert next_node.get("name") == "closing"
    assert flow_manager.state["rescheduled_appointment"]["slot_id"] == "new-slot"


async def test_confirm_reschedule_slot_taken_stays_in_node(flow_manager):
    flow_manager.state["selected_confirmation_id"] = "APT-ABC"
    taken = {"status": "slot_taken"}
    with patch("services.receptionist.flows.nodes.reschedule_appointment",
               new=AsyncMock(return_value=taken)):
        _, next_node = await _handle_confirm_reschedule_slot({"slot_id": "new-slot"}, flow_manager)
    assert next_node is None


async def test_manage_appointment_node_has_required_tools():
    node = create_manage_appointment_node()
    tool_names = {fn.name for fn in node["functions"]}
    assert {"search_patient", "find_patient_appointments", "select_appointment",
            "cancel_appointment", "request_slots", "transfer_to_human"} <= tool_names


async def test_reschedule_slot_proposal_node_has_required_tools():
    node = create_reschedule_slot_proposal_node()
    tool_names = {fn.name for fn in node["functions"]}
    assert {"confirm_slot", "get_more_slots", "transfer_to_human"} <= tool_names


# ---------------------------------------------------------------------------
# Completion events — confirm that handlers write to the event log when wired
# ---------------------------------------------------------------------------

import json

def _attach_event_log(flow_manager, tmp_path):
    log = tmp_path / "events.jsonl"
    flow_manager.state["session_id"] = "sess-test"
    flow_manager.state["event_log_path"] = log
    return log


async def test_send_confirmation_emits_booking_done(flow_manager, tmp_path):
    log = _attach_event_log(flow_manager, tmp_path)
    flow_manager.state["booked_appointment"] = {
        "confirmation_id": "APT-01", "slot_id": "slot-1", "visit_type": "checkup",
    }
    with patch("services.receptionist.flows.nodes.send_confirmation",
               new=AsyncMock(return_value={"status": "sent", "channel": "sms"})):
        await _handle_send_confirmation({"patient_id": "P001"}, flow_manager)
    record = json.loads(log.read_text().strip())
    assert record["event"] == "booking_done"
    assert record["confirmation_id"] == "APT-01"
    assert record["patient_id"] == "P001"


async def test_cancel_appointment_emits_cancel_done(flow_manager, tmp_path):
    log = _attach_event_log(flow_manager, tmp_path)
    cancelled = {
        "status": "cancelled",
        "appointment": {"confirmation_id": "APT-9", "patient_id": "P003", "slot_id": "s-9"},
    }
    with patch("services.receptionist.flows.nodes.cancel_appointment",
               new=AsyncMock(return_value=cancelled)):
        await _handle_cancel_appointment({"confirmation_id": "APT-9"}, flow_manager)
    record = json.loads(log.read_text().strip())
    assert record["event"] == "cancel_done"
    assert record["confirmation_id"] == "APT-9"
    assert record["patient_id"] == "P003"


async def test_confirm_reschedule_slot_emits_reschedule_done(flow_manager, tmp_path):
    log = _attach_event_log(flow_manager, tmp_path)
    flow_manager.state["selected_confirmation_id"] = "APT-5"
    flow_manager.state["patient_appointments"] = [
        {"confirmation_id": "APT-5", "slot_id": "old-slot"},
    ]
    with patch("services.receptionist.flows.nodes.reschedule_appointment",
               new=AsyncMock(return_value={
                   "status": "rescheduled",
                   "appointment": {"confirmation_id": "APT-5", "patient_id": "P001",
                                   "slot_id": "new-slot"},
               })):
        await _handle_confirm_reschedule_slot({"slot_id": "new-slot"}, flow_manager)
    record = json.loads(log.read_text().strip())
    assert record["event"] == "reschedule_done"
    assert record["confirmation_id"] == "APT-5"
    assert record["from_slot"] == "old-slot"
    assert record["to_slot"] == "new-slot"


async def test_transfer_to_human_emits_llm_handoff(flow_manager, tmp_path):
    log = _attach_event_log(flow_manager, tmp_path)
    flow_manager._node = "collect_info"
    await _handle_transfer_to_human({"reason": "medical_question"}, flow_manager)
    record = json.loads(log.read_text().strip())
    assert record["event"] == "llm_handoff"
    assert record["reason"] == "medical_question"
    assert record["from_node"] == "collect_info"


async def test_cancel_not_found_does_not_emit_event(flow_manager, tmp_path):
    log = _attach_event_log(flow_manager, tmp_path)
    with patch("services.receptionist.flows.nodes.cancel_appointment",
               new=AsyncMock(return_value={"status": "not_found"})):
        await _handle_cancel_appointment({"confirmation_id": "X"}, flow_manager)
    assert not log.exists()


# ---------------------------------------------------------------------------
# _handle_record_consent — entry into the LLM flow
# ---------------------------------------------------------------------------

async def test_record_consent_accept_transitions_to_intent(flow_manager, tmp_path):
    log = _attach_event_log(flow_manager, tmp_path)
    _, next_node = await _handle_record_consent({"given": True}, flow_manager)
    assert next_node is not None
    assert next_node.get("name") == "intent"
    assert flow_manager.state["consent_given"] is True
    assert flow_manager.state["consent_timestamp"] is not None

async def test_record_consent_decline_transitions_to_handoff(flow_manager, tmp_path):
    _attach_event_log(flow_manager, tmp_path)
    _, next_node = await _handle_record_consent({"given": False}, flow_manager)
    assert next_node is not None
    assert next_node.get("name") == "handoff"
    assert flow_manager.state["consent_given"] is False
    assert flow_manager.state["handoff_reason"] == "consent_declined"


# ---------------------------------------------------------------------------
# Structural invariants over every NodeConfig — catches "functions: []" holes
# and end_conversation race conditions before they reach a live call.
# ---------------------------------------------------------------------------

# Every node factory in the flow graph. Update this list when nodes are added.
ALL_NODE_FACTORIES = [
    create_consent_node,
    create_intent_node,
    create_collect_info_node,
    create_slot_proposal_node,
    create_confirmation_node,
    create_manage_appointment_node,
    create_reschedule_slot_proposal_node,
    create_handoff_node,
    create_closing_node,
]

# Terminal nodes that legitimately have functions: [] because they end the call
# via post_actions. Every other node must expose at least one transition tool.
TERMINAL_NODES = {"closing"}


@pytest.mark.parametrize("factory", ALL_NODE_FACTORIES, ids=lambda f: f.__name__)
def test_every_non_terminal_node_has_a_transition_function(factory):
    node = factory()
    if node["name"] in TERMINAL_NODES:
        return
    assert node.get("functions"), (
        f"Node '{node['name']}' has functions={node.get('functions')!r}. "
        "Without a callable function the LLM can never advance the flow — "
        "the call hangs in this state until the caller hangs up. "
        "(See the 'after Yes, agent stops' bug from session 2026-04-25.)"
    )


@pytest.mark.parametrize("factory", ALL_NODE_FACTORIES, ids=lambda f: f.__name__)
def test_end_conversation_post_actions_are_deferred(factory):
    """A node with `end_conversation` in post_actions must set
    `respond_immediately=False`, otherwise the action fires on node entry
    and races any in-flight TTS — the closing announcement gets cut off
    before the audio reaches the caller.
    """
    node = factory()
    has_end_conversation = any(
        a.get("type") == "end_conversation"
        for a in node.get("post_actions", [])
    )
    if not has_end_conversation:
        return
    assert node.get("respond_immediately") is False, (
        f"Node '{node['name']}' queues end_conversation but does not set "
        "respond_immediately=False. With the default True, post_actions execute "
        "synchronously on node entry and the EndFrame races the LLM's final TTS — "
        "the caller hears nothing of the closing message."
    )
