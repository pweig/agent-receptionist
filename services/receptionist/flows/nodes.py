"""
NodeConfig factories for each of the 9 conversation states.

pipecat-flows drives the conversation by injecting per-state system messages
and restricting which tools are available in each state. State transitions happen
by returning the next NodeConfig from a tool handler (see tools/schemas.py).

Tool availability per state:
  greeting          — none
  language_detect   — set_language (internal)
  hours_check       — get_office_hours
  intent            — none
  info_collection   — search_patient
  slot_proposal     — get_available_slots
  confirmation      — book_appointment, send_confirmation
  handoff           — none
  closing           — none
"""

from pipecat_flows import FlowConfig, FlowsFunctionSchema

from ..prompt import PERSONA_SYSTEM_PROMPT, STATE_TASK_MESSAGES
from ..tools.schemas import (
    BOOK_APPOINTMENT_SCHEMA,
    GET_AVAILABLE_SLOTS_SCHEMA,
    GET_OFFICE_HOURS_SCHEMA,
    SEARCH_PATIENT_SCHEMA,
    SEND_CONFIRMATION_SCHEMA,
    SET_LANGUAGE_SCHEMA,
)


def _role(extra: str = "") -> list[dict]:
    content = PERSONA_SYSTEM_PROMPT + ("\n\n" + extra if extra else "")
    return [{"role": "system", "content": content}]


def _task(state_key: str) -> list[dict]:
    return [{"role": "system", "content": STATE_TASK_MESSAGES[state_key]}]


def _schema(raw: dict) -> FlowsFunctionSchema:
    return FlowsFunctionSchema(
        name=raw["name"],
        description=raw["description"],
        properties=raw["input_schema"].get("properties", {}),
        required=raw["input_schema"].get("required", []),
        handler=raw["handler"],
    )


# ---------------------------------------------------------------------------
# Node factories
# ---------------------------------------------------------------------------

def create_greeting_node() -> dict:
    return {
        "role_messages": _role(),
        "task_messages": _task("greeting"),
        "functions": [],
    }


def create_language_detection_node() -> dict:
    return {
        "role_messages": _role(),
        "task_messages": _task("language_detection"),
        "functions": [_schema(SET_LANGUAGE_SCHEMA)],
    }


def create_hours_check_node() -> dict:
    return {
        "role_messages": _role(),
        "task_messages": _task("hours_check"),
        "functions": [_schema(GET_OFFICE_HOURS_SCHEMA)],
    }


def create_intent_node() -> dict:
    return {
        "role_messages": _role(),
        "task_messages": _task("intent"),
        "functions": [],
        # Transitions to info_collection or handoff are driven by LLM response
        # and the handoff.evaluate_handoff() call in the event handler.
    }


def create_info_collection_node() -> dict:
    return {
        "role_messages": _role(),
        "task_messages": _task("info_collection"),
        "functions": [_schema(SEARCH_PATIENT_SCHEMA)],
    }


def create_slot_proposal_node() -> dict:
    return {
        "role_messages": _role(),
        "task_messages": _task("slot_proposal"),
        "functions": [_schema(GET_AVAILABLE_SLOTS_SCHEMA)],
    }


def create_confirmation_node() -> dict:
    return {
        "role_messages": _role(),
        "task_messages": _task("confirmation"),
        "functions": [
            _schema(BOOK_APPOINTMENT_SCHEMA),
            _schema(SEND_CONFIRMATION_SCHEMA),
        ],
    }


def create_handoff_node() -> dict:
    return {
        "role_messages": _role(),
        "task_messages": _task("handoff"),
        "functions": [],
    }


def create_closing_node() -> dict:
    return {
        "role_messages": _role(),
        "task_messages": _task("closing"),
        "functions": [],
        "post_actions": [{"type": "end_conversation"}],
    }


# ---------------------------------------------------------------------------
# Full flow config (all nodes wired together for FlowManager initialization)
# ---------------------------------------------------------------------------

def build_flow_config() -> FlowConfig:
    """
    Returns the complete FlowConfig used to initialize the FlowManager.
    The initial node is 'greeting'; transitions are driven by tool call handlers.
    """
    return FlowConfig(
        initial_node="greeting",
        nodes={
            "greeting": create_greeting_node(),
            "language_detection": create_language_detection_node(),
            "hours_check": create_hours_check_node(),
            "intent": create_intent_node(),
            "info_collection": create_info_collection_node(),
            "slot_proposal": create_slot_proposal_node(),
            "confirmation": create_confirmation_node(),
            "handoff": create_handoff_node(),
            "closing": create_closing_node(),
        },
    )
