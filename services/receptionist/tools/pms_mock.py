"""
Mock Practice Management System (PMS) implementations.
Appointments and booked slots are persisted to data/pms.json so they survive
server restarts — reschedule/cancel calls work across sessions.
Swap with real PMS adapters (Dampsoft, Dentrix, etc.) without changing the
calling interface.
"""

import json
import os
import re
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import yaml

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "office_hours.yaml"
_DB_PATH = Path(__file__).parent.parent / "data" / "pms.json"

# ---------------------------------------------------------------------------
# In-memory patient database
# ---------------------------------------------------------------------------

_PATIENTS: dict[str, dict] = {
    "P001": {
        "id": "P001",
        "full_name": "Thomas Müller",
        "dob": "1985-03-15",
        "phone": "+49-176-1234567",
        "insurance": "AOK Bayern",
        "insurance_type": "GKV",
        "is_new": False,
        "notes": "Amalgam allergy noted.",
    },
    "P002": {
        "id": "P002",
        "full_name": "Tobias Müller",
        "dob": "1990-11-02",
        "phone": "+49-89-5554433",
        "insurance": "TK",
        "insurance_type": "GKV",
        "is_new": False,
        "notes": "",
    },
    "P003": {
        "id": "P003",
        "full_name": "Anna Schmidt",
        "dob": "1992-07-22",
        "phone": "+49-89-9876543",
        "insurance": "DKV",
        "insurance_type": "PKV",
        "is_new": False,
        "notes": "",
    },
    "P004": {
        "id": "P004",
        "full_name": "Sarah Johnson",
        "dob": "1988-04-10",
        "phone": "+1-312-555-0198",
        "insurance": "Blue Cross Blue Shield",
        "insurance_type": "PPO",
        "is_new": False,
        "notes": "Prefers morning appointments.",
    },
    "P005": {
        "id": "P005",
        "full_name": "Michael Chen",
        "dob": "1975-09-30",
        "phone": "+1-415-555-0174",
        "insurance": "Delta Dental",
        "insurance_type": "HMO",
        "is_new": False,
        "notes": "",
    },
    "P006": {
        "id": "P006",
        "full_name": "Fatima Al-Hassan",
        "dob": "2001-06-14",
        "phone": "+49-30-5551234",
        "insurance": "Barmer",
        "insurance_type": "GKV",
        "is_new": False,
        "notes": "",
    },
    "P007": {
        "id": "P007",
        "full_name": "Klaus Bergmann",
        "dob": "1965-12-03",
        "phone": "+49-711-9998877",
        "insurance": "Selbstzahler",
        "insurance_type": "Selbstzahler",
        "is_new": False,
        "notes": "Prefers Dr. Fischer.",
    },
    "P008": {
        "id": "P008",
        "full_name": "Emma Wilson",
        "dob": "2010-02-28",
        "phone": "+1-206-555-0133",
        "insurance": "Cigna",
        "insurance_type": "PPO",
        "is_new": False,
        "notes": "Pediatric patient — parent accompanies.",
    },
}

# Booked slot IDs (prevents double-booking).  Derived from _APPOINTMENTS on load.
_BOOKED_SLOTS: set[str] = set()

# Confirmed appointments — keyed by confirmation_id.
_APPOINTMENTS: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _save_db() -> None:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_DB_PATH, "w") as f:
        json.dump(
            {"patients": _PATIENTS, "appointments": _APPOINTMENTS, "booked_slots": list(_BOOKED_SLOTS)},
            f,
            indent=2,
        )


def _load_db() -> None:
    """Load persisted state from disk; seed demo data only on first run."""
    if _DB_PATH.exists():
        with open(_DB_PATH) as f:
            data = json.load(f)
        # Patients key absent in files created before this feature — keep static seed.
        loaded_patients = data.get("patients", {})
        if loaded_patients:
            _PATIENTS.clear()
            _PATIENTS.update(loaded_patients)
        _APPOINTMENTS.update(data.get("appointments", {}))
        _BOOKED_SLOTS.update(data.get("booked_slots", []))
        if not loaded_patients:
            _save_db()  # backfill patients into the existing file
    else:
        _seed_demo_appointments()
        _save_db()


def _seed_demo_appointments() -> None:
    """Pre-populate a few upcoming appointments so the reschedule/cancel flow
    has something to act on during manual POC testing. Called only when no
    persisted DB file exists yet. Reset by the reset_pms_state fixture in
    tests/conftest.py."""
    today = date.today()
    demo = [
        # Thomas Müller — checkup 3 days out at 10:00
        ("P001", today + timedelta(days=3), "10:00", "checkup"),
        # Anna Schmidt — cleaning 5 days out at 14:00
        ("P003", today + timedelta(days=5), "14:00", "cleaning"),
        # Sarah Johnson — consultation 7 days out at 09:00
        ("P004", today + timedelta(days=7), "09:00", "consultation"),
    ]
    for patient_id, d, t, visit_type in demo:
        slot_id = f"{d.isoformat()}-{t.replace(':', '')}-{visit_type}"
        confirmation_id = f"APT-SEED{patient_id[-1]}"
        slot_dt = datetime.fromisoformat(f"{d.isoformat()}T{t}:00")
        _APPOINTMENTS[confirmation_id] = {
            "confirmation_id": confirmation_id,
            "patient_id": patient_id,
            "patient_name": _PATIENTS[patient_id]["full_name"],
            "slot_id": slot_id,
            "datetime_iso": slot_dt.isoformat(),
            "visit_type": visit_type,
            "provider": "Dr. Fischer",
            "notes": "",
            "booked_at": (datetime.now() - timedelta(days=2)).isoformat(),
        }
        _BOOKED_SLOTS.add(slot_id)


_load_db()


# ---------------------------------------------------------------------------
# Visit type durations (minutes)
# ---------------------------------------------------------------------------

_VISIT_DURATIONS: dict[str, int] = {
    "checkup": 30,
    "cleaning": 45,
    "consultation": 20,
    "pain": 30,
    "emergency": 30,
}


# ---------------------------------------------------------------------------
# Helper: office hours
# ---------------------------------------------------------------------------

def _load_office_hours() -> dict:
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _is_office_open(dt: datetime, config: dict) -> tuple[bool, Optional[str], Optional[str]]:
    """Returns (is_open, open_time, close_time)."""
    date_str = dt.strftime("%Y-%m-%d")

    # 1. Check exceptions
    exceptions = config.get("exceptions", {})
    if date_str in exceptions:
        val = exceptions[date_str]
        if val == "closed":
            return False, None, None
        return True, val["open"], val["close"]

    # 2. Check holidays
    holidays = config.get("holidays", [])
    if date_str in holidays:
        return False, None, None

    # 3. Check weekday
    weekday = dt.strftime("%A").lower()
    hours = config.get("weekday_hours", {}).get(weekday)
    if hours is None or hours == "closed":
        return False, None, None

    return True, hours["open"], hours["close"]


# ---------------------------------------------------------------------------
# Fuzzy name matching
# ---------------------------------------------------------------------------

def _normalize(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[äÄ]", "ae", s)
    s = re.sub(r"[öÖ]", "oe", s)
    s = re.sub(r"[üÜ]", "ue", s)
    s = re.sub(r"[ß]", "ss", s)
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return s


def _name_matches(query: str, full_name: str) -> bool:
    q = _normalize(query)
    n = _normalize(full_name)
    q_parts = q.split()
    n_parts = n.split()
    # All query parts must appear somewhere in the name parts
    return all(any(qp in np for np in n_parts) for qp in q_parts)


# ---------------------------------------------------------------------------
# Public async tool functions
# ---------------------------------------------------------------------------

async def search_patient(name: str, dob: Optional[str] = None) -> dict:
    """
    Fuzzy-match patient by name + optional DOB.

    Returns:
        {"status": "found", "patient": {...}}
        {"status": "multiple", "candidates": [...]}  → triggers AMBIGUOUS_PATIENT handoff
        {"status": "not_found"}
    """
    matches = [p for p in _PATIENTS.values() if _name_matches(name, p["full_name"])]

    if dob:
        exact = [p for p in matches if p["dob"] == dob]
        if exact:
            return {"status": "found", "patient": exact[0]}
        # DOB provided but no exact match — fall through to name-only result

    if len(matches) == 0:
        return {"status": "not_found"}
    if len(matches) == 1:
        return {"status": "found", "patient": matches[0]}
    # Multiple matches without disambiguating DOB
    candidates = [{"id": p["id"], "full_name": p["full_name"], "dob": p["dob"]} for p in matches]
    return {"status": "multiple", "candidates": candidates}


async def create_patient(
    full_name: str,
    dob: str,
    phone: str,
    insurance: str = "",
    insurance_type: str = "",
    notes: str = "",
) -> dict:
    """
    Register a new patient and persist the record.

    Returns:
        {"status": "created", "patient": {...}}
    """
    new_id = f"P{len(_PATIENTS) + 1:03d}"
    while new_id in _PATIENTS:
        new_id = f"P{int(new_id[1:]) + 1:03d}"

    patient = {
        "id": new_id,
        "full_name": full_name,
        "dob": dob,
        "phone": phone,
        "insurance": insurance,
        "insurance_type": insurance_type,
        "is_new": True,
        "notes": notes,
    }
    _PATIENTS[new_id] = patient
    _save_db()
    return {"status": "created", "patient": patient}


async def get_available_slots(
    visit_type: str,
    urgency: str,
    date_range: Optional[dict] = None,
) -> dict:
    """
    Generate available appointment slots.

    Args:
        visit_type: checkup|cleaning|pain|emergency|consultation
        urgency: routine|urgent|emergency
        date_range: {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"} — defaults to today+14d

    Returns:
        {"slots": [{id, datetime_iso, date_human, time_human, duration_mins, provider}]}
    """
    config = _load_office_hours()
    now = datetime.now()

    if date_range and date_range.get("start"):
        start = datetime.fromisoformat(date_range["start"])
    else:
        start = now

    if urgency == "emergency":
        end = start + timedelta(days=2)
    elif date_range and date_range.get("end"):
        end = datetime.fromisoformat(date_range["end"])
    else:
        end = start + timedelta(days=14)

    duration = _VISIT_DURATIONS.get(visit_type, 30)
    providers = ["Dr. Fischer", "Dr. Braun"]
    slot_times = ["09:00", "10:00", "11:00", "14:00", "15:00", "16:00"]

    slots = []
    current = start.date()
    end_date = end.date()

    while current <= end_date and len(slots) < 6:
        dt = datetime(current.year, current.month, current.day)
        is_open, open_t, close_t = _is_office_open(dt, config)
        if is_open:
            for t in slot_times:
                slot_dt = datetime.fromisoformat(f"{current.isoformat()}T{t}:00")
                # Skip past slots
                if slot_dt <= now:
                    continue
                slot_id = f"{current.isoformat()}-{t.replace(':', '')}-{visit_type}"
                if slot_id not in _BOOKED_SLOTS:
                    provider = providers[len(slots) % len(providers)]
                    slots.append({
                        "id": slot_id,
                        "datetime_iso": slot_dt.isoformat(),
                        "date_human": slot_dt.strftime("%A, %B %d"),
                        "date_human_de": _german_date(slot_dt),
                        "time_human": slot_dt.strftime("%I:%M %p"),
                        "duration_mins": duration,
                        "provider": provider,
                        "visit_type": visit_type,
                    })
                if len(slots) >= 6:
                    break
        current += timedelta(days=1)

    return {"slots": slots}


def _german_date(dt: datetime) -> str:
    weekdays = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    months = [
        "", "Januar", "Februar", "März", "April", "Mai", "Juni",
        "Juli", "August", "September", "Oktober", "November", "Dezember",
    ]
    return f"{weekdays[dt.weekday()]}, {dt.day}. {months[dt.month]}"


async def book_appointment(
    patient_id: str,
    slot_id: str,
    visit_type: str,
    notes: str = "",
) -> dict:
    """
    Book an appointment slot.

    Returns:
        {"status": "confirmed", "confirmation_id": "...", "appointment": {...}}
        {"status": "slot_taken"} if already booked
        {"status": "patient_not_found"} if patient_id unknown
    """
    if slot_id in _BOOKED_SLOTS:
        return {"status": "slot_taken"}

    patient = _PATIENTS.get(patient_id)
    if not patient:
        return {"status": "patient_not_found"}

    _BOOKED_SLOTS.add(slot_id)
    confirmation_id = f"APT-{uuid.uuid4().hex[:8].upper()}"

    appointment = {
        "confirmation_id": confirmation_id,
        "patient_id": patient_id,
        "patient_name": patient["full_name"],
        "slot_id": slot_id,
        "visit_type": visit_type,
        "notes": notes,
        "booked_at": datetime.now().isoformat(),
    }
    _APPOINTMENTS[confirmation_id] = appointment
    _save_db()

    return {"status": "confirmed", "confirmation_id": confirmation_id, "appointment": appointment}


async def send_confirmation(
    patient_id: str,
    appointment: dict,
    channel: str = "sms",
) -> dict:
    """
    Mock send a booking confirmation via SMS or email.

    Returns:
        {"status": "sent", "channel": "sms"|"email", "timestamp": "..."}
    """
    patient = _PATIENTS.get(patient_id)
    if not patient:
        return {"status": "failed", "reason": "patient_not_found"}

    return {
        "status": "sent",
        "channel": channel,
        "recipient": patient.get("phone") if channel == "sms" else f"{patient_id}@example.com",
        "confirmation_id": appointment.get("confirmation_id"),
        "timestamp": datetime.now().isoformat(),
    }


async def find_patient_appointments(patient_id: str) -> dict:
    """
    List upcoming appointments for a patient (past appointments excluded).

    Returns:
        {"appointments": [{confirmation_id, datetime_iso, date_human, date_human_de,
                           time_human, visit_type, provider}, ...]}
    """
    now = datetime.now()
    matching = []
    for appt in _APPOINTMENTS.values():
        if appt.get("patient_id") != patient_id:
            continue
        iso = appt.get("datetime_iso")
        if not iso:
            continue
        try:
            dt = datetime.fromisoformat(iso)
        except ValueError:
            continue
        if dt <= now:
            continue
        matching.append({
            "confirmation_id": appt["confirmation_id"],
            "slot_id": appt["slot_id"],
            "datetime_iso": dt.isoformat(),
            "date_human": dt.strftime("%A, %B %d"),
            "date_human_de": _german_date(dt),
            "time_human": dt.strftime("%I:%M %p"),
            "visit_type": appt.get("visit_type"),
            "provider": appt.get("provider", "Dr. Fischer"),
        })
    matching.sort(key=lambda a: a["datetime_iso"])
    return {"appointments": matching}


async def cancel_appointment(confirmation_id: str) -> dict:
    """
    Cancel an appointment by its confirmation_id.

    Returns:
        {"status": "cancelled", "appointment": {...}}
        {"status": "not_found"} if unknown confirmation_id
    """
    appt = _APPOINTMENTS.pop(confirmation_id, None)
    if appt is None:
        return {"status": "not_found"}
    slot_id = appt.get("slot_id")
    if slot_id:
        _BOOKED_SLOTS.discard(slot_id)
    _save_db()
    return {"status": "cancelled", "appointment": appt}


async def reschedule_appointment(confirmation_id: str, new_slot_id: str) -> dict:
    """
    Move an existing appointment to a new slot.

    Returns:
        {"status": "rescheduled", "appointment": {...}}
        {"status": "not_found"} if confirmation_id unknown
        {"status": "slot_taken"} if new_slot_id is already booked by someone else
    """
    appt = _APPOINTMENTS.get(confirmation_id)
    if appt is None:
        return {"status": "not_found"}
    if new_slot_id != appt.get("slot_id") and new_slot_id in _BOOKED_SLOTS:
        return {"status": "slot_taken"}

    old_slot = appt.get("slot_id")
    if old_slot and old_slot != new_slot_id:
        _BOOKED_SLOTS.discard(old_slot)
    _BOOKED_SLOTS.add(new_slot_id)

    # slot_id format: YYYY-MM-DD-HHMM-visit_type
    parts = new_slot_id.split("-")
    if len(parts) >= 5:
        iso_date = "-".join(parts[:3])
        hhmm = parts[3]
        slot_dt = datetime.fromisoformat(f"{iso_date}T{hhmm[:2]}:{hhmm[2:]}:00")
        appt["datetime_iso"] = slot_dt.isoformat()
    appt["slot_id"] = new_slot_id
    appt["rescheduled_at"] = datetime.now().isoformat()
    _save_db()

    return {"status": "rescheduled", "appointment": appt}


async def get_office_hours(date_str: Optional[str] = None) -> dict:
    """
    Return office hours for a given date (defaults to today).

    Returns:
        {"open": "HH:MM", "close": "HH:MM"} or {"closed": true, "message_de": "...", "message_en": "..."}
    """
    config = _load_office_hours()

    if date_str:
        try:
            dt = datetime.fromisoformat(date_str)
        except ValueError:
            dt = datetime.now()
    else:
        dt = datetime.now()

    is_open, open_t, close_t = _is_office_open(dt, config)

    if not is_open:
        routing = config.get("after_hours_routing", {})
        locale = os.environ.get("OFFICE_LOCALE", "de")
        emergency = routing.get(f"emergency_number_{locale}", routing.get("emergency_number_de", ""))
        msg_key = f"message_{locale}"
        message = routing.get(msg_key, routing.get("message_en", "")).format(
            emergency_number=emergency
        )
        return {
            "closed": True,
            "emergency_number": emergency,
            "message": message,
        }

    return {"open": open_t, "close": close_t}
