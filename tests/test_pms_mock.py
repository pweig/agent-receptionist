"""Unit tests for tools/pms_mock.py — PMS tool functions."""
import pytest
from datetime import datetime
from unittest.mock import patch

from services.receptionist.tools.pms_mock import (
    _normalize,
    _name_matches,
    _german_date,
    _is_office_open,
    _spoken_phone,
    office_status_now,
    search_patient,
    get_available_slots,
    book_appointment,
    send_confirmation,
    get_office_hours,
    find_patient_appointments,
    cancel_appointment,
    reschedule_appointment,
)


# ---------------------------------------------------------------------------
# _normalize
# ---------------------------------------------------------------------------

def test_normalize_umlaut_ae():
    assert _normalize("Müller") == "mueller"

def test_normalize_umlaut_oe():
    assert _normalize("Schön") == "schoen"

def test_normalize_umlaut_ue():
    assert _normalize("Grün") == "gruen"

def test_normalize_sharp_s():
    assert _normalize("Straße") == "strasse"

def test_normalize_uppercase():
    assert _normalize("ANNA") == "anna"

def test_normalize_strips_punctuation():
    assert _normalize("O'Brien") == "obrien"

def test_normalize_trims_whitespace():
    assert _normalize("  Thomas  ") == "thomas"


# ---------------------------------------------------------------------------
# _name_matches
# ---------------------------------------------------------------------------

def test_name_matches_exact():
    assert _name_matches("Anna Schmidt", "Anna Schmidt") is True

def test_name_matches_last_name_only():
    assert _name_matches("Schmidt", "Anna Schmidt") is True

def test_name_matches_umlaut_normalized():
    assert _name_matches("Mueller", "Thomas Müller") is True

def test_name_matches_partial_first_name():
    assert _name_matches("Anna", "Anna Schmidt") is True

def test_name_matches_no_match():
    assert _name_matches("Johnson", "Anna Schmidt") is False

def test_name_matches_empty_query():
    # Empty query — all() on empty iterable is True; document the behaviour
    assert _name_matches("", "Anna Schmidt") is True


# ---------------------------------------------------------------------------
# _is_office_open
# ---------------------------------------------------------------------------

def test_is_office_open_monday(office_hours_config):
    dt = datetime(2026, 4, 20, 10, 0)  # Monday
    is_open, open_t, close_t = _is_office_open(dt, office_hours_config)
    assert is_open is True
    assert open_t == "08:00"
    assert close_t == "18:00"

def test_is_office_open_wednesday_short_day(office_hours_config):
    dt = datetime(2026, 4, 22, 10, 0)  # Wednesday
    is_open, open_t, close_t = _is_office_open(dt, office_hours_config)
    assert is_open is True
    assert close_t == "13:00"

def test_is_office_open_saturday_closed(office_hours_config):
    dt = datetime(2026, 4, 25, 10, 0)  # Saturday
    is_open, _, _ = _is_office_open(dt, office_hours_config)
    assert is_open is False

def test_is_office_open_sunday_closed(office_hours_config):
    dt = datetime(2026, 4, 19, 10, 0)  # Sunday
    is_open, _, _ = _is_office_open(dt, office_hours_config)
    assert is_open is False

def test_is_office_open_holiday_closed(office_hours_config):
    dt = datetime(2026, 4, 6, 10, 0)  # Ostermontag
    is_open, _, _ = _is_office_open(dt, office_hours_config)
    assert is_open is False

def test_is_office_open_exception_closed(office_hours_config):
    dt = datetime(2026, 5, 1, 10, 0)  # Tag der Arbeit (exception override)
    is_open, _, _ = _is_office_open(dt, office_hours_config)
    assert is_open is False

def test_is_office_open_exception_custom_hours(office_hours_config):
    dt = datetime(2026, 12, 24, 10, 0)  # Heiligabend: 08:00–12:00
    is_open, open_t, close_t = _is_office_open(dt, office_hours_config)
    assert is_open is True
    assert close_t == "12:00"


# ---------------------------------------------------------------------------
# _german_date
# ---------------------------------------------------------------------------

def test_german_date_monday():
    dt = datetime(2026, 4, 20)  # Monday
    result = _german_date(dt)
    assert result.startswith("Montag")
    assert "April" in result

def test_german_date_day_number():
    dt = datetime(2026, 4, 20)
    result = _german_date(dt)
    assert "20." in result


# ---------------------------------------------------------------------------
# search_patient
# ---------------------------------------------------------------------------

async def test_search_patient_exact_match():
    result = await search_patient("Anna Schmidt")
    assert result["status"] == "found"
    assert result["patient"]["id"] == "P003"

async def test_search_patient_last_name_only_unique():
    result = await search_patient("Schmidt")
    assert result["status"] == "found"

async def test_search_patient_ambiguous_pair():
    result = await search_patient("Müller")
    assert result["status"] == "multiple"
    assert len(result["candidates"]) == 2

async def test_search_patient_umlaut_normalized():
    result = await search_patient("Mueller")
    assert result["status"] == "multiple"  # both Müllers match

async def test_search_patient_dob_disambiguates_thomas():
    result = await search_patient("Müller", dob="1985-03-15")
    assert result["status"] == "found"
    assert result["patient"]["id"] == "P001"  # Thomas Müller

async def test_search_patient_dob_disambiguates_tobias():
    result = await search_patient("Müller", dob="1990-11-02")
    assert result["status"] == "found"
    assert result["patient"]["id"] == "P002"  # Tobias Müller

async def test_search_patient_dob_no_match_falls_back_to_multiple():
    # DOB doesn't match either Müller — falls through to name-only result
    result = await search_patient("Müller", dob="2000-01-01")
    assert result["status"] == "multiple"

async def test_search_patient_not_found():
    result = await search_patient("Nobody Known")
    assert result["status"] == "not_found"

async def test_search_patient_pediatric_patient():
    result = await search_patient("Emma Wilson")
    assert result["status"] == "found"
    assert result["patient"]["id"] == "P008"


# ---------------------------------------------------------------------------
# get_available_slots
# ---------------------------------------------------------------------------

# Freeze to Tuesday 2026-04-21 09:30 — within open hours, slots at 10:00+ available
_FROZEN = "2026-04-21 09:30:00"

async def test_get_available_slots_returns_slots(office_hours_config):
    with patch("services.receptionist.tools.pms_mock._load_office_hours",
               return_value=office_hours_config):
        from freezegun import freeze_time
        with freeze_time(_FROZEN):
            result = await get_available_slots("checkup", "routine")
    assert "slots" in result
    assert len(result["slots"]) > 0

async def test_get_available_slots_max_six(office_hours_config):
    with patch("services.receptionist.tools.pms_mock._load_office_hours",
               return_value=office_hours_config):
        from freezegun import freeze_time
        with freeze_time(_FROZEN):
            result = await get_available_slots("consultation", "routine")
    assert len(result["slots"]) <= 6

async def test_get_available_slots_no_past_slots(office_hours_config):
    with patch("services.receptionist.tools.pms_mock._load_office_hours",
               return_value=office_hours_config):
        from freezegun import freeze_time
        with freeze_time(_FROZEN):
            result = await get_available_slots("checkup", "routine")
    base = datetime(2026, 4, 21, 9, 30)
    for slot in result["slots"]:
        assert datetime.fromisoformat(slot["datetime_iso"]) > base

async def test_get_available_slots_emergency_2_day_window(office_hours_config):
    with patch("services.receptionist.tools.pms_mock._load_office_hours",
               return_value=office_hours_config):
        from freezegun import freeze_time
        with freeze_time(_FROZEN):
            result = await get_available_slots("pain", "emergency")
    slots = result["slots"]
    assert len(slots) > 0
    base = datetime(2026, 4, 21, 9, 30)
    for slot in slots:
        diff = (datetime.fromisoformat(slot["datetime_iso"]) - base).days
        assert diff <= 2

async def test_get_available_slots_has_known_providers(office_hours_config):
    with patch("services.receptionist.tools.pms_mock._load_office_hours",
               return_value=office_hours_config):
        from freezegun import freeze_time
        with freeze_time(_FROZEN):
            result = await get_available_slots("cleaning", "routine")
    for slot in result["slots"]:
        assert slot["provider"] in ("Dr. Fischer", "Dr. Braun")

async def test_get_available_slots_slot_has_required_keys(office_hours_config):
    with patch("services.receptionist.tools.pms_mock._load_office_hours",
               return_value=office_hours_config):
        from freezegun import freeze_time
        with freeze_time(_FROZEN):
            result = await get_available_slots("checkup", "routine")
    required = {"id", "datetime_iso", "date_human", "time_human", "duration_mins", "provider"}
    for slot in result["slots"]:
        assert required.issubset(slot.keys())

async def test_get_available_slots_booked_slot_excluded(office_hours_config):
    import services.receptionist.tools.pms_mock as pms
    with patch("services.receptionist.tools.pms_mock._load_office_hours",
               return_value=office_hours_config):
        from freezegun import freeze_time
        with freeze_time(_FROZEN):
            first = await get_available_slots("checkup", "routine")
            first_slot_id = first["slots"][0]["id"]
            pms._BOOKED_SLOTS.add(first_slot_id)
            second = await get_available_slots("checkup", "routine")
    slot_ids = {s["id"] for s in second["slots"]}
    assert first_slot_id not in slot_ids


# ---------------------------------------------------------------------------
# book_appointment
# ---------------------------------------------------------------------------

async def test_book_appointment_confirmed():
    result = await book_appointment("P001", "slot-001", "checkup")
    assert result["status"] == "confirmed"
    assert result["confirmation_id"].startswith("APT-")
    assert result["appointment"]["patient_id"] == "P001"

async def test_book_appointment_stores_slot_as_booked():
    import services.receptionist.tools.pms_mock as pms
    await book_appointment("P001", "slot-001", "checkup")
    assert "slot-001" in pms._BOOKED_SLOTS

async def test_book_appointment_double_booking_prevented():
    await book_appointment("P001", "slot-001", "checkup")
    result = await book_appointment("P002", "slot-001", "checkup")
    assert result["status"] == "slot_taken"

async def test_book_appointment_patient_not_found():
    result = await book_appointment("UNKNOWN", "slot-001", "checkup")
    assert result["status"] == "patient_not_found"

async def test_book_appointment_with_notes():
    result = await book_appointment("P001", "slot-001", "checkup", notes="Amalgam allergy")
    assert result["status"] == "confirmed"
    assert result["appointment"]["notes"] == "Amalgam allergy"


# ---------------------------------------------------------------------------
# send_confirmation
# ---------------------------------------------------------------------------

async def test_send_confirmation_sms():
    appt = {"confirmation_id": "APT-TEST01"}
    result = await send_confirmation("P001", appt, channel="sms")
    assert result["status"] == "sent"
    assert result["channel"] == "sms"
    assert "+49" in result["recipient"]  # Thomas Müller has a DE number

async def test_send_confirmation_email():
    appt = {"confirmation_id": "APT-TEST01"}
    result = await send_confirmation("P001", appt, channel="email")
    assert result["status"] == "sent"
    assert result["channel"] == "email"
    assert "@" in result["recipient"]

async def test_send_confirmation_includes_confirmation_id():
    appt = {"confirmation_id": "APT-TEST01"}
    result = await send_confirmation("P001", appt)
    assert result["confirmation_id"] == "APT-TEST01"

async def test_send_confirmation_patient_not_found():
    result = await send_confirmation("UNKNOWN", {})
    assert result["status"] == "failed"
    assert result["reason"] == "patient_not_found"


# ---------------------------------------------------------------------------
# get_office_hours
# ---------------------------------------------------------------------------

async def test_get_office_hours_monday_open(office_hours_config):
    with patch("services.receptionist.tools.pms_mock._load_office_hours",
               return_value=office_hours_config):
        result = await get_office_hours("2026-04-20")  # Monday
    assert result.get("open") == "08:00"
    assert result.get("close") == "18:00"

async def test_get_office_hours_wednesday_short(office_hours_config):
    with patch("services.receptionist.tools.pms_mock._load_office_hours",
               return_value=office_hours_config):
        result = await get_office_hours("2026-04-22")  # Wednesday
    assert result.get("close") == "13:00"

async def test_get_office_hours_saturday_closed(office_hours_config):
    with patch("services.receptionist.tools.pms_mock._load_office_hours",
               return_value=office_hours_config):
        result = await get_office_hours("2026-04-25")  # Saturday
    assert result.get("closed") is True
    assert "emergency_number" in result

async def test_get_office_hours_sunday_closed(office_hours_config):
    with patch("services.receptionist.tools.pms_mock._load_office_hours",
               return_value=office_hours_config):
        result = await get_office_hours("2026-04-19")  # Sunday
    assert result.get("closed") is True

async def test_get_office_hours_holiday_closed(office_hours_config):
    with patch("services.receptionist.tools.pms_mock._load_office_hours",
               return_value=office_hours_config):
        result = await get_office_hours("2026-04-06")  # Ostermontag
    assert result.get("closed") is True

async def test_get_office_hours_exception_closed(office_hours_config):
    with patch("services.receptionist.tools.pms_mock._load_office_hours",
               return_value=office_hours_config):
        result = await get_office_hours("2026-05-01")  # Tag der Arbeit
    assert result.get("closed") is True

async def test_get_office_hours_exception_custom_hours(office_hours_config):
    with patch("services.receptionist.tools.pms_mock._load_office_hours",
               return_value=office_hours_config):
        result = await get_office_hours("2026-12-24")  # Heiligabend: 08:00–12:00
    assert result.get("close") == "12:00"

async def test_get_office_hours_no_date_uses_today(office_hours_config):
    # Without a date arg, should use datetime.now() — just check it returns a valid shape
    with patch("services.receptionist.tools.pms_mock._load_office_hours",
               return_value=office_hours_config):
        result = await get_office_hours()
    assert "open" in result or "closed" in result

async def test_get_office_hours_invalid_date_falls_back(office_hours_config):
    # Invalid date string falls back to datetime.now() without crashing
    with patch("services.receptionist.tools.pms_mock._load_office_hours",
               return_value=office_hours_config):
        result = await get_office_hours("not-a-date")
    assert "open" in result or "closed" in result


# ---------------------------------------------------------------------------
# find_patient_appointments / cancel_appointment / reschedule_appointment
# ---------------------------------------------------------------------------

async def _seed_one_appointment(patient_id: str, slot_id: str) -> str:
    """Helper — create a known future appointment and return its confirmation_id."""
    import services.receptionist.tools.pms_mock as pms
    from datetime import datetime, timedelta
    cid = "APT-TEST" + slot_id[-4:]
    dt = datetime.now() + timedelta(days=3)
    pms._APPOINTMENTS[cid] = {
        "confirmation_id": cid,
        "patient_id": patient_id,
        "patient_name": pms._PATIENTS[patient_id]["full_name"],
        "slot_id": slot_id,
        "datetime_iso": dt.isoformat(),
        "visit_type": "checkup",
        "provider": "Dr. Fischer",
        "notes": "",
        "booked_at": datetime.now().isoformat(),
    }
    pms._BOOKED_SLOTS.add(slot_id)
    return cid


async def test_find_patient_appointments_returns_upcoming():
    cid = await _seed_one_appointment("P001", "2026-05-01-1000-checkup")
    result = await find_patient_appointments("P001")
    ids = [a["confirmation_id"] for a in result["appointments"]]
    assert cid in ids


async def test_find_patient_appointments_excludes_past():
    import services.receptionist.tools.pms_mock as pms
    from datetime import datetime, timedelta
    past_cid = "APT-PAST"
    pms._APPOINTMENTS[past_cid] = {
        "confirmation_id": past_cid,
        "patient_id": "P001",
        "slot_id": "2020-01-01-1000-checkup",
        "datetime_iso": (datetime.now() - timedelta(days=5)).isoformat(),
    }
    result = await find_patient_appointments("P001")
    ids = [a["confirmation_id"] for a in result["appointments"]]
    assert past_cid not in ids


async def test_find_patient_appointments_empty_for_unknown_patient():
    result = await find_patient_appointments("UNKNOWN")
    assert result["appointments"] == []


async def test_cancel_appointment_frees_slot():
    import services.receptionist.tools.pms_mock as pms
    cid = await _seed_one_appointment("P001", "2026-05-02-1000-checkup")
    result = await cancel_appointment(cid)
    assert result["status"] == "cancelled"
    assert cid not in pms._APPOINTMENTS
    assert "2026-05-02-1000-checkup" not in pms._BOOKED_SLOTS


async def test_cancel_appointment_not_found():
    result = await cancel_appointment("APT-DOESNOTEXIST")
    assert result["status"] == "not_found"


async def test_reschedule_appointment_moves_slot():
    import services.receptionist.tools.pms_mock as pms
    cid = await _seed_one_appointment("P001", "2026-05-03-1000-checkup")
    result = await reschedule_appointment(cid, "2026-05-04-1400-checkup")
    assert result["status"] == "rescheduled"
    # Old slot freed, new slot booked
    assert "2026-05-03-1000-checkup" not in pms._BOOKED_SLOTS
    assert "2026-05-04-1400-checkup" in pms._BOOKED_SLOTS
    assert pms._APPOINTMENTS[cid]["slot_id"] == "2026-05-04-1400-checkup"
    # datetime_iso reparsed from new slot_id
    assert "2026-05-04T14:00" in pms._APPOINTMENTS[cid]["datetime_iso"]


async def test_reschedule_appointment_not_found():
    result = await reschedule_appointment("APT-DOESNOTEXIST", "2026-05-04-1400-checkup")
    assert result["status"] == "not_found"


async def test_reschedule_appointment_slot_taken():
    import services.receptionist.tools.pms_mock as pms
    cid = await _seed_one_appointment("P001", "2026-05-05-1000-checkup")
    pms._BOOKED_SLOTS.add("2026-05-06-1000-checkup")  # someone else holds the target
    result = await reschedule_appointment(cid, "2026-05-06-1000-checkup")
    assert result["status"] == "slot_taken"


# ---------------------------------------------------------------------------
# _spoken_phone — TTS-friendly phone rendering for the after-hours gate
# ---------------------------------------------------------------------------

def test_spoken_phone_de_uses_german_digits_with_commas():
    spoken = _spoken_phone("+49-800-111-2222", "de")
    assert spoken == "vier, neun, acht, null, null, eins, eins, eins, zwei, zwei, zwei, zwei"

def test_spoken_phone_en_uses_digits_with_commas():
    spoken = _spoken_phone("+1-800-555-0123", "en")
    assert spoken == "1, 8, 0, 0, 5, 5, 5, 0, 1, 2, 3"

def test_spoken_phone_strips_non_digits():
    # Spaces, dashes, plus, parens — all get stripped before rendering.
    spoken = _spoken_phone("+1 (800) 555-0123", "en")
    assert spoken == "1, 8, 0, 0, 5, 5, 5, 0, 1, 2, 3"


# ---------------------------------------------------------------------------
# office_status_now — entry-gate helper invoked before the LLM pipeline.
# Patches the YAML loader and wall clock so tests are deterministic.
# ---------------------------------------------------------------------------

_OPEN_CONFIG = {
    "weekday_hours": {
        "monday": {"open": "08:00", "close": "18:00"},
        "tuesday": {"open": "08:00", "close": "18:00"},
        "wednesday": {"open": "08:00", "close": "18:00"},
        "thursday": {"open": "08:00", "close": "18:00"},
        "friday": {"open": "08:00", "close": "18:00"},
        "saturday": "closed",
        "sunday": "closed",
    },
    "exceptions": {},
    "holidays": [],
    "after_hours_routing": {
        "emergency_number_de": "+49-800-111-2222",
        "emergency_number_us": "+1-800-555-0123",
        "message_de": "Geschlossen. Notfall: {emergency_number}.",
        "message_en": "Closed. Emergency: {emergency_number}.",
    },
}

# Tuesday 2026-04-21 at 10:00 — open per _OPEN_CONFIG.
_OPEN_DT = datetime(2026, 4, 21, 10, 0)
# Saturday 2026-04-25 at 23:00 — closed per _OPEN_CONFIG.
_CLOSED_DT = datetime(2026, 4, 25, 23, 0)


def test_office_status_open_returns_minimal_dict():
    with patch("services.receptionist.tools.pms_mock._load_office_hours",
               return_value=_OPEN_CONFIG), \
         patch("services.receptionist.tools.pms_mock.datetime") as mock_dt:
        mock_dt.now.return_value = _OPEN_DT
        mock_dt.strftime = datetime.strftime
        status = office_status_now()
    assert status == {"open": True}

def test_office_status_closed_returns_message_with_spoken_digits():
    with patch("services.receptionist.tools.pms_mock._load_office_hours",
               return_value=_OPEN_CONFIG), \
         patch("services.receptionist.tools.pms_mock.datetime") as mock_dt:
        mock_dt.now.return_value = _CLOSED_DT
        mock_dt.strftime = datetime.strftime
        # OFFICE_LOCALE defaults to "de" when not set
        with patch.dict("os.environ", {"OFFICE_LOCALE": "de"}, clear=False):
            status = office_status_now()
    assert status["open"] is False
    assert status["emergency_number"] == "+49-800-111-2222"
    # Phone rendered digit-by-digit so Piper inserts pauses on commas.
    assert "vier, neun, acht, null" in status["message"]
    # Raw +49-... must NOT appear; that pronunciation is what the gate avoids.
    assert "+49" not in status["message"]

def test_office_status_locale_en_uses_us_emergency_number():
    with patch("services.receptionist.tools.pms_mock._load_office_hours",
               return_value=_OPEN_CONFIG), \
         patch("services.receptionist.tools.pms_mock.datetime") as mock_dt:
        mock_dt.now.return_value = _CLOSED_DT
        mock_dt.strftime = datetime.strftime
        with patch.dict("os.environ", {"OFFICE_LOCALE": "en"}, clear=False):
            status = office_status_now()
    assert status["emergency_number"] == "+1-800-555-0123"
    assert "1, 8, 0, 0, 5" in status["message"]
