import unittest
from unittest.mock import MagicMock, patch

from aura.dashboard.ui import (
    _get_or_create_state,
    _load_dotenv_if_present,
    _process_event_locally,
)
from aura.graph import WorkflowOutcome
from aura.streaming.demo import make_sensor_event


class SessionStateMock(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name, value):
        self[name] = value

    def __delattr__(self, name):
        try:
            del self[name]
        except KeyError:
            raise AttributeError(name)


class DashboardUITests(unittest.TestCase):
    def setUp(self):
        self.mock_session_state = SessionStateMock()
        self.st_patch = patch("aura.dashboard.ui.st.session_state", self.mock_session_state)
        self.st_patch.start()

    def tearDown(self):
        self.st_patch.stop()

    def test_state_initialization(self):
        _get_or_create_state()
        self.assertIn("aura_demo", self.mock_session_state)
        self.assertIn("processed_results", self.mock_session_state)
        self.assertIn("tickets_meta", self.mock_session_state)

    def test_process_event_locally_normal(self):
        _get_or_create_state()
        event = make_sensor_event("normal", event_id="ui-test-normal")

        result = _process_event_locally(event)

        self.assertEqual(result.outcome, WorkflowOutcome.NO_ACTION)
        self.assertEqual(len(self.mock_session_state["processed_results"]), 1)
        self.assertIsNone(result.receipt)

    def test_process_event_locally_critical_creates_ticket(self):
        _get_or_create_state()
        event = make_sensor_event("critical-cold", event_id="ui-test-critical")

        result = _process_event_locally(event, resident_response="Hjälp mig!")

        self.assertEqual(result.outcome, WorkflowOutcome.DISPATCHED)
        self.assertIsNotNone(result.receipt)
        self.assertIn(result.receipt.external_reference, self.mock_session_state["tickets_meta"])
        meta = self.mock_session_state["tickets_meta"][result.receipt.external_reference]
        self.assertEqual(meta["status"], "awaiting_human_review")


if __name__ == "__main__":
    unittest.main()
