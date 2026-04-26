from enum import Enum
from dataclasses import dataclass, field
from typing import Optional


class ConversationState(str, Enum):
    CONSENT = "consent"
    INTENT = "intent"
    INFO_COLLECTION = "info_collection"
    SLOT_PROPOSAL = "slot_proposal"
    CONFIRMATION = "confirmation"
    HANDOFF = "handoff"
    CLOSING = "closing"


class Language(str, Enum):
    EN = "en"
    DE = "de"


class HandoffReason(str, Enum):
    CALLER_REQUESTED = "caller_requested"
    FRUSTRATION = "frustration"
    MEDICAL_QUESTION = "medical_question"
    BILLING_DISPUTE = "billing_dispute"
    AMBIGUOUS_PATIENT = "ambiguous_patient"
    RESCHEDULE = "reschedule"
    LOW_STT_CONFIDENCE = "low_stt_confidence"
    OUTSIDE_SCOPE = "outside_scope"
    AFTER_HOURS = "after_hours"


@dataclass
class CollectedInfo:
    full_name: Optional[str] = None
    date_of_birth: Optional[str] = None          # ISO date YYYY-MM-DD
    phone_number: Optional[str] = None
    is_existing_patient: Optional[bool] = None
    visit_reason: Optional[str] = None            # checkup|cleaning|pain|emergency|consultation
    insurance_type: Optional[str] = None          # GKV|PKV|Selbstzahler|provider_name|unknown
    caller_on_behalf: bool = False
    behalf_patient_name: Optional[str] = None


# State transition table (informational — transitions are enforced by NodeConfig handler
# return values in flows/nodes.py). The after-hours gate runs in main.py before
# the flow is initialized; closed calls never enter any of these states.
#
#   CONSENT          → INTENT            (caller answers Ja)
#   CONSENT          → HANDOFF           (caller answers Nein)
#   INTENT           → INFO_COLLECTION   (booking)
#   INTENT           → HANDOFF           (medical Q, billing, etc.)
#   INFO_COLLECTION  → SLOT_PROPOSAL     (all required fields collected)
#   INFO_COLLECTION  → HANDOFF           (ambiguous patient)
#   SLOT_PROPOSAL    → SLOT_PROPOSAL     (caller wants alternatives — node loops)
#   SLOT_PROPOSAL    → CONFIRMATION      (slot accepted)
#   CONFIRMATION     → CLOSING           (book + confirm called)
#   Any state        → HANDOFF           (evaluate_handoff() fires)
#   HANDOFF          → CLOSING           (transfer done or callback logged)


def initial_state(session_id: str) -> dict:
    return {
        "session_id": session_id,
        "language": None,                     # Language enum, set when caller's language is detected
        "current_state": ConversationState.CONSENT,
        "consent_given": None,                # True/False once record_consent is called
        "consent_timestamp": None,            # ISO-8601 timestamp of the consent decision
        "info": CollectedInfo(),
        "patient_record": None,               # result of search_patient
        "proposed_slots": [],                 # result of get_available_slots
        "chosen_slot": None,
        "confirmation_id": None,
        "handoff_reason": None,               # HandoffReason if in HANDOFF
        "stt_low_confidence_count": 0,
        "repeated_turn_count": 0,
        "last_user_utterance": None,
        "tool_error_count": 0,
    }
