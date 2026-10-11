import unittest
from datetime import datetime, timezone

from aura.agents.critical_risk_policy import CriticalRiskPolicy
from aura.composition import create_demo_system
from aura.contracts import ActionProposal, DispatchStatus, InputEvent
from aura.dashboard import MockDashboardAdapter
from aura.graph import WorkflowOutcome


def make_event(event_id="evt-dashboard-1", door_state="open", motion_detected=False):
    return InputEvent(
        event_id=event_id,
        occurred_at=datetime(2026, 10, 11, tzinfo=timezone.utc),
        subject_id="synthetic-resident-1",
        source="test-sensors",
        observations={
            "temperature_c": -2.0,
            "humidity_pct": 45.0,
            "door_state": door_state,
            "motion_detected": motion_detected,
        },
    )


class MockDashboardAdapterTests(unittest.TestCase):
    def test_creates_dashboard_review_record_and_receipt(self):
        event = make_event()
        proposal = ActionProposal(
            proposal_id="proposal-1",
            event_id=event.event_id,
            action_type="request_human_review",
            rationale="Guardian approved human review.",
            confidence=0.9,
            parameters={"priority": "high"},
        )
        adapter = MockDashboardAdapter(record_id_factory=lambda: "dashboard-1")

        receipt = adapter.execute(event, proposal)

        self.assertEqual(receipt.status, DispatchStatus.SUCCEEDED)
        self.assertEqual(receipt.external_reference, "dashboard-1")
        self.assertEqual(len(adapter.records), 1)
        record = adapter.records[0]
        self.assertEqual(record.status, "awaiting_human_review")
        self.assertEqual(record.subject_id, event.subject_id)
        self.assertEqual(record.observations, dict(event.observations))

    def test_repeated_event_and_proposal_are_idempotent(self):
        event = make_event()
        proposal = ActionProposal(
            proposal_id="proposal-1",
            event_id=event.event_id,
            action_type="request_human_review",
            rationale="Review request.",
            confidence=0.9,
        )
        adapter = MockDashboardAdapter(record_id_factory=lambda: "dashboard-1")

        first = adapter.execute(event, proposal)
        second = adapter.execute(event, proposal)

        self.assertEqual(first.external_reference, second.external_reference)
        self.assertEqual(len(adapter.records), 1)

    def test_rejects_unsupported_action_without_record(self):
        event = make_event()
        proposal = ActionProposal(
            proposal_id="proposal-2",
            event_id=event.event_id,
            action_type="change_schedule",
            rationale="Not enabled by this adapter.",
            confidence=0.9,
        )
        adapter = MockDashboardAdapter()

        receipt = adapter.execute(event, proposal)

        self.assertEqual(receipt.status, DispatchStatus.FAILED)
        self.assertEqual(adapter.records, ())

    def test_composed_guardian_dispatch_updates_dashboard_only_for_critical_event(self):
        event = make_event()
        dashboard = MockDashboardAdapter(record_id_factory=lambda: "dashboard-critical")
        system = create_demo_system(
            baselines={
                event.subject_id: {
                    "temperature_c": 18.0,
                    "humidity_pct": 45.0,
                    "door_state": "closed",
                    "motion_detected": True,
                }
            },
            guardian_policy=CriticalRiskPolicy(),
            dispatcher_tool=dashboard,
            proposal_id_factory=lambda: "proposal-critical",
        )

        result = system.run(event)

        self.assertEqual(result["outcome"], WorkflowOutcome.DISPATCHED)
        self.assertEqual(result["receipt"].external_reference, "dashboard-critical")
        self.assertEqual(len(dashboard.records), 1)
        self.assertEqual(dashboard.records[0].event_id, event.event_id)
        self.assertEqual(system.mock_ticket_tool.tickets, ())


if __name__ == "__main__":
    unittest.main()