from enum import Enum
from dataclasses import dataclass, field
from typing import Optional


class ConversationState(str, Enum):
    GREETING = "greeting"
    LANGUAGE_DETECT = "language_detection"
    HOURS_CHECK = "hours_check"
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
# return values in flows/nodes.py):
#
#   GREETING         → LANGUAGE_DETECT   (first utterance received)
#   LANGUAGE_DETECT  → HOURS_CHECK       (language detected; TTS voice switched)
#   HOURS_CHECK      → INTENT            (office is open)
#   HOURS_CHECK      → CLOSING           (office is closed)
#   INTENT           → INFO_COLLECTION   (appointment booking intent)
#   INTENT           → HANDOFF           (reschedule, cancel, medical Q, billing)
#   INFO_COLLECTION  → SLOT_PROPOSAL     (all required fields collected)
#   INFO_COLLECTION  → HANDOFF           (ambiguous patient; persistent confusion)
#   SLOT_PROPOSAL    → SLOT_PROPOSAL     (caller wants alternatives — node loops)
#   SLOT_PROPOSAL    → CONFIRMATION      (slot accepted)
#   CONFIRMATION     → CLOSING           (book + confirm called)
#   CONFIRMATION     → SLOT_PROPOSAL     (caller changes mind)
#   Any state        → HANDOFF           (evaluate_handoff() fires)
#   HANDOFF          → CLOSING           (transfer done or callback logged)


def initial_state(session_id: str) -> dict:
    return {
        "session_id": session_id,
        "language": None,                     # Language enum, set at LANGUAGE_DETECT
        "current_state": ConversationState.GREETING,
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
