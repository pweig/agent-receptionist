"""Shared fixtures for all tests."""
import pytest


# ---------------------------------------------------------------------------
# Office hours config — avoids file I/O in tests
# ---------------------------------------------------------------------------

@pytest.fixture
def office_hours_config():
    return {
        "timezone": "Europe/Berlin",
        "weekday_hours": {
            "monday":    {"open": "08:00", "close": "18:00"},
            "tuesday":   {"open": "08:00", "close": "18:00"},
            "wednesday": {"open": "08:00", "close": "13:00"},
            "thursday":  {"open": "08:00", "close": "18:00"},
            "friday":    {"open": "08:00", "close": "15:00"},
            "saturday":  "closed",
            "sunday":    "closed",
        },
        "exceptions": {
            "2026-05-01": "closed",
            "2026-12-24": {"open": "08:00", "close": "12:00"},
        },
        "holidays": [
            "2026-01-01",  # Neujahr
            "2026-04-03",  # Karfreitag
            "2026-04-06",  # Ostermontag
            "2026-05-14",  # Christi Himmelfahrt
        ],
        "after_hours_routing": {
            "emergency_number_de": "+49-800-111-2222",
            "emergency_number_us": "+1-800-555-0123",
            "message_de": (
                "Unsere Praxis ist derzeit nicht erreichbar. "
                "Für zahnärztliche Notfälle rufen Sie bitte {emergency_number} an."
            ),
            "message_en": (
                "Our practice is currently closed. "
                "For dental emergencies, please call {emergency_number}."
            ),
        },
    }


# ---------------------------------------------------------------------------
# Mock FlowManager — used by handler tests
# ---------------------------------------------------------------------------

class MockTask:
    def __init__(self):
        self.queued_frames: list = []

    async def queue_frame(self, frame) -> None:
        self.queued_frames.append(frame)


class MockFlowManager:
    def __init__(self, current_node: str = "hours_check"):
        self.state: dict = {}
        self._node = current_node
        self.task = MockTask()

    @property
    def current_node(self) -> str:
        return self._node


@pytest.fixture
def flow_manager():
    return MockFlowManager()


@pytest.fixture
def flow_manager_greeting():
    return MockFlowManager(current_node="greeting")


@pytest.fixture
def flow_manager_collect_info():
    return MockFlowManager(current_node="collect_info")


# ---------------------------------------------------------------------------
# Reset module-level PMS state between tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_pms_state():
    import services.receptionist.tools.pms_mock as pms
    pms._BOOKED_SLOTS.clear()
    pms._APPOINTMENTS.clear()
    yield
    pms._BOOKED_SLOTS.clear()
    pms._APPOINTMENTS.clear()
