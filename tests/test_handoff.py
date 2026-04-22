"""Unit tests for handoff.py — trigger evaluation logic."""
import pytest
from services.receptionist.handoff import evaluate_handoff, _utterances_similar
from services.receptionist.state import HandoffReason


# ---------------------------------------------------------------------------
# _utterances_similar (Jaccard similarity helper)
# ---------------------------------------------------------------------------

def test_similar_identical():
    assert _utterances_similar("I need an appointment", "I need an appointment") is True

def test_similar_high_overlap():
    assert _utterances_similar("I need an appointment please", "I need an appointment") is True

def test_similar_different():
    assert _utterances_similar("hello", "goodbye tomorrow evening") is False

def test_similar_empty_strings():
    assert _utterances_similar("", "hello") is False
    assert _utterances_similar("hello", "") is False


# ---------------------------------------------------------------------------
# CALLER_REQUESTED — EN + DE
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("utterance", [
    "I want to speak to a human",
    "Can I talk to a person please",
    "Please transfer me to someone",
    "I'd like to speak to a representative",
    "Connect me to a real person",
    "I want to speak to the receptionist",
])
def test_caller_requested_english(utterance):
    reason = evaluate_handoff({}, utterance)
    assert reason == HandoffReason.CALLER_REQUESTED

@pytest.mark.parametrize("utterance", [
    "Ich möchte mit einem Mitarbeiter sprechen",
    "Können Sie mich weiterverbinden?",
    "Ich brauche einen Menschen",
    "Bitte verbinden Sie mich mit der Rezeption",
    "Mit jemandem sprechen bitte",
])
def test_caller_requested_german(utterance):
    reason = evaluate_handoff({}, utterance)
    assert reason == HandoffReason.CALLER_REQUESTED


# ---------------------------------------------------------------------------
# MEDICAL_QUESTION — EN + DE
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("utterance", [
    "I have symptoms of an infection",
    "Can you prescribe me antibiotics",
    "I think I have a diagnosis",
    "What medication should I take",
    "I need a prescription for painkillers",
])
def test_medical_question_english(utterance):
    reason = evaluate_handoff({}, utterance)
    assert reason == HandoffReason.MEDICAL_QUESTION

@pytest.mark.parametrize("utterance", [
    "Ich habe eine Entzündung",
    "Brauche ich Antibiotika?",
    "Welches Medikament soll ich nehmen?",
    "Meine Wange ist geschwollen",
])
def test_medical_question_german(utterance):
    reason = evaluate_handoff({}, utterance)
    assert reason == HandoffReason.MEDICAL_QUESTION


# ---------------------------------------------------------------------------
# BILLING_DISPUTE — EN + DE
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("utterance", [
    "I have a question about my invoice",
    "I think I was overcharged",
    "My insurance denied the claim",
    "I want to file a complaint about my bill",
])
def test_billing_dispute_english(utterance):
    reason = evaluate_handoff({}, utterance)
    assert reason == HandoffReason.BILLING_DISPUTE

@pytest.mark.parametrize("utterance", [
    "Ich habe eine Frage zur Rechnung",
    "Die Kostenübernahme wurde abgelehnt",
    "Ich möchte eine Beschwerde einreichen",
    "Ich wurde zu viel berechnet",
])
def test_billing_dispute_german(utterance):
    reason = evaluate_handoff({}, utterance)
    assert reason == HandoffReason.BILLING_DISPUTE


# ---------------------------------------------------------------------------
# Reschedule / cancel — NOT a handoff trigger (self-serve flow handles it)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("utterance", [
    "I need to reschedule my appointment",
    "Can I cancel my appointment",
    "Ich möchte meinen Termin absagen",
    "Ich muss den Termin verschieben",
])
def test_reschedule_cancel_does_not_trigger_handoff(utterance):
    reason = evaluate_handoff({}, utterance)
    assert reason is None


# ---------------------------------------------------------------------------
# LOW_STT_CONFIDENCE — fires after 2 consecutive low-confidence turns
# ---------------------------------------------------------------------------

def test_low_confidence_single_turn_no_trigger():
    state = {}
    reason = evaluate_handoff(state, "something unclear", stt_language_prob=0.3)
    assert reason is None
    assert state["stt_low_confidence_count"] == 1

def test_low_confidence_two_turns_triggers():
    state = {}
    evaluate_handoff(state, "unclear turn one", stt_language_prob=0.3)
    reason = evaluate_handoff(state, "unclear turn two", stt_language_prob=0.3)
    assert reason == HandoffReason.LOW_STT_CONFIDENCE

def test_low_confidence_resets_on_high_confidence():
    state = {}
    evaluate_handoff(state, "unclear", stt_language_prob=0.2)
    assert state["stt_low_confidence_count"] == 1
    # High-confidence turn resets the counter
    evaluate_handoff(state, "I need an appointment", stt_language_prob=0.95)
    assert state["stt_low_confidence_count"] == 0

def test_confidence_exactly_at_threshold_no_trigger():
    state = {}
    # 0.45 is the threshold — not below it
    reason = evaluate_handoff(state, "hello", stt_language_prob=0.45)
    assert reason is None
    assert state.get("stt_low_confidence_count", 0) == 0


# ---------------------------------------------------------------------------
# FRUSTRATION — repeated utterances (Jaccard ≥ 0.6, count ≥ 2)
# ---------------------------------------------------------------------------

def test_frustration_triggers_on_repeated_utterance():
    state = {}
    evaluate_handoff(state, "I want to book an appointment please")
    evaluate_handoff(state, "I want to book an appointment please")
    reason = evaluate_handoff(state, "I want to book an appointment please")
    assert reason == HandoffReason.FRUSTRATION

def test_frustration_resets_on_different_utterance():
    state = {}
    evaluate_handoff(state, "book an appointment")
    evaluate_handoff(state, "book an appointment")
    evaluate_handoff(state, "completely different topic")
    reason = evaluate_handoff(state, "yet another thing")
    # Counter reset — should not trigger
    assert reason is None

def test_frustration_not_triggered_after_two_turns():
    # Two identical turns increment counter to 1 — fires at ≥ 2
    state = {}
    evaluate_handoff(state, "hello")
    reason = evaluate_handoff(state, "hello")
    assert reason is None  # count = 1, threshold is 2


# ---------------------------------------------------------------------------
# No trigger — normal utterances
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("utterance", [
    "I'd like to make an appointment",
    "Ich möchte einen Termin machen",
    "My name is Anna Schmidt",
    "I was born on the fifteenth of March",
    "I have a checkup",
    "What time is available on Thursday",
])
def test_no_trigger_normal_utterances(utterance):
    reason = evaluate_handoff({}, utterance)
    assert reason is None


# ---------------------------------------------------------------------------
# Priority order — CALLER_REQUESTED fires before MEDICAL
# ---------------------------------------------------------------------------

def test_priority_caller_requested_over_medical():
    # Contains both a human-request keyword and a medical keyword
    utterance = "I want to speak to someone about my symptoms"
    reason = evaluate_handoff({}, utterance)
    assert reason == HandoffReason.CALLER_REQUESTED
