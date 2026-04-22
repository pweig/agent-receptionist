"""
Handoff trigger evaluation for Phase 1 (eager handoff policy).

Call evaluate_handoff() at the start of any state handler that can transition
to HANDOFF. Returns a HandoffReason if a trigger fires, None to continue normally.

Phase 2 loosening: see the comment block at the bottom of this file.
"""

import re
from typing import Optional

from .state import HandoffReason


# ---------------------------------------------------------------------------
# Compiled trigger patterns (checked in priority order)
# ---------------------------------------------------------------------------

def _compile(patterns: list[str]) -> re.Pattern:
    return re.compile("|".join(patterns), re.IGNORECASE)


_HUMAN_REQUEST = _compile([
    r"\bhuman\b", r"\bperson\b", r"\brepresentative\b", r"\boperator\b",
    r"\bsomeone else\b", r"\brecept", r"\btransfer\b",
    r"\bspeak to\b", r"\btalk to\b", r"\bconnect me\b",
    r"\bmensche?n?\b",                  # Mensch / Menschen / Menscher
    r"\bmitarbeiter", r"\brezeption",
    r"\bweiterstel", r"\bweiterverbinden",
    r"\bsprechen mit\b", r"\bjemande[mn] sprechen",  # jemanden / jemandem
    r"\bverbinden\b",
])

_MEDICAL = _compile([
    r"\bsymptom", r"\btreatment\b", r"\bprescri", r"\bantibiotic",
    r"\bpainkiller", r"\bdiagnos", r"\bmedication\b", r"\bpill\b",
    r"\bEntzündung\b", r"\bAntibiotika\b", r"\bMedikament", r"\bBehandlung empfehlen\b",
    r"\bwhat.*wrong\b", r"\bwas.*habe ich\b", r"\bist das ernst\b",
    r"\bschmerzen.*seit\b", r"\bschwellung\b", r"\bgeschwollen\b",
])

_BILLING = _compile([
    r"\binvoice\b", r"\bbill\b", r"\bcharge\b", r"\binsurance.*refus",
    r"\binsurance.*denied\b", r"\bovercharged\b", r"\bcomplaint\b",
    r"\bRechnung\b", r"\bKostenübernahme\b", r"\babgelehnt\b",
    r"\bGeld zurück\b", r"\bBeschwerde\b", r"\bzu viel berechnet\b",
])

# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def evaluate_handoff(
    state: dict,
    utterance: str,
    stt_language_prob: Optional[float] = None,
) -> Optional[HandoffReason]:
    """
    Evaluate whether the current turn should trigger a handoff.

    Checks triggers in priority order:
      1. CALLER_REQUESTED  — explicit request for a human
      2. MEDICAL_QUESTION  — symptom/treatment/medication content
      3. BILLING_DISPUTE   — invoicing or insurance dispute
      4. LOW_STT_CONFIDENCE — Whisper language_probability below threshold for N turns
      5. FRUSTRATION       — repeated utterances or failed turns

    Reschedule and cancel requests are handled autonomously by the
    manage_appointment flow, not by this evaluator.

    Returns HandoffReason or None.
    """
    lower = utterance.lower()

    if _HUMAN_REQUEST.search(lower):
        return HandoffReason.CALLER_REQUESTED

    if _MEDICAL.search(lower):
        return HandoffReason.MEDICAL_QUESTION

    if _BILLING.search(lower):
        return HandoffReason.BILLING_DISPUTE

    if stt_language_prob is not None and stt_language_prob < 0.45:
        state["stt_low_confidence_count"] = state.get("stt_low_confidence_count", 0) + 1
        if state["stt_low_confidence_count"] >= 2:
            return HandoffReason.LOW_STT_CONFIDENCE
    else:
        state["stt_low_confidence_count"] = 0

    # Track repetition: if the caller says almost the same thing twice, bump the counter
    last = state.get("last_user_utterance", "")
    if last and _utterances_similar(last, utterance):
        state["repeated_turn_count"] = state.get("repeated_turn_count", 0) + 1
    else:
        state["repeated_turn_count"] = 0
    state["last_user_utterance"] = utterance

    if state.get("repeated_turn_count", 0) >= 2:
        return HandoffReason.FRUSTRATION

    return None


def _utterances_similar(a: str, b: str) -> bool:
    """Rough similarity: Jaccard on word sets, threshold 0.6."""
    wa = set(re.findall(r"\w+", a.lower()))
    wb = set(re.findall(r"\w+", b.lower()))
    if not wa or not wb:
        return False
    return len(wa & wb) / len(wa | wb) >= 0.6


# ---------------------------------------------------------------------------
# Phase 2 loosening notes
# ---------------------------------------------------------------------------
#
# When Phase 2 is ready, make these changes:
#
# 1. Replace _utterances_similar + repeated_turn_count with a lightweight
#    sentiment classifier (e.g. transformers zero-shot on "frustration" label).
#    Raise threshold to 3 repeated turns after calibration on real call logs.
#
# 2. Raise stt_low_confidence_count threshold to 3 after calibrating on
#    production audio quality (phone network vs. browser mic differs).
#
# 3. Add AMBIGUOUS_PATIENT trigger: call evaluate_ambiguous_patient(search_result)
#    from the info_collection handler when search_patient returns status="multiple"
#    and the caller cannot disambiguate after 2 clarification turns.
#
# 4. Add OUTSIDE_HOURS trigger separately from AFTER_HOURS (currently handled
#    in the hours_check node directly via get_office_hours tool call).
#
# Reschedule and cancel requests are already handled autonomously by the
# manage_appointment flow (services/receptionist/flows/nodes.py).
