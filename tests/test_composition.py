import unittest
from datetime import datetime, timezone

from aura.agents.guardian import ActionRule, RuleBasedActionPolicy
from aura.composition import create_demo_system
from aura.contracts import InputEvent
from aura.graph import WorkflowOutcome
from aura.testing import InMemoryBaselineStore


def make_event(event_id, subject_id, door_state):
    return InputEvent(
        event_id=event_id,
        occurred_at=datetime(2026, 10, 11, tzinfo=timezone.utc),
        subject_id=subject_id,
        source="synthetic-scenario",
        observations={"door_state": door_state},
    )


class DemoCompositionTests(unittest.TestCase):
    def test_normal_scenario_stops_without_ticket(self):
        system = create_demo_system(
            baselines={"resident-1": {"door_state": "closed"}},
            ticket_id_factory=lambda: "unused-ticket",
        )

        result = system.run(make_event("evt-normal", "resident-1", "closed"))

        self.assertEqual(result["outcome"], WorkflowOutcome.NO_ACTION)
        self.assertIsNone(result["proposal"])
        self.assertEqual(system.mock_ticket_tool.tickets, ())

    def test_anomaly_runs_all_agents_and_creates_mock_ticket(self):
        system = create_demo_system(
            baselines={"resident-1": {"door_state": "closed"}},
            proposal_id_factory=lambda: "proposal-anomaly",
            ticket_id_factory=lambda: "ticket-anomaly",
        )

        result = system.run(make_event("evt-anomaly", "resident-1", "open"))

        self.assertEqual(result["outcome"], WorkflowOutcome.DISPATCHED)
        self.assertEqual(result["proposal"].action_type, "request_human_review")
        self.assertEqual(result["decision"].status.value, "approve")
        self.assertEqual(result["receipt"].external_reference, "ticket-anomaly")
        self.assertEqual(len(system.mock_ticket_tool.tickets), 1)

    def test_missing_baseline_routes_to_review_and_mock_ticket(self):
        system = create_demo_system(ticket_id_factory=lambda: "ticket-review")

        result = system.run(make_event("evt-no-baseline", "unknown-resident", "open"))

        self.assertTrue(result["assessment"].requires_review)
        self.assertEqual(result["outcome"], WorkflowOutcome.DISPATCHED)
        self.assertEqual(result["receipt"].external_reference, "ticket-review")

    def test_held_proposal_does_not_create_ticket(self):
        system = create_demo_system(
            baselines={"resident-1": {"door_state": "closed"}},
            guardian_policy=RuleBasedActionPolicy(
                {
                    "request_human_review": ActionRule(
                        minimum_assessment_confidence=0.0,
                        minimum_proposal_confidence=0.99,
                        allow_when_review_required=True,
                    )
                }
            ),
        )

        result = system.run(make_event("evt-held", "resident-1", "open"))

        self.assertEqual(result["outcome"], WorkflowOutcome.HELD)
        self.assertEqual(result["decision"].reason_codes, ("confidence_below_policy_threshold",))
        self.assertEqual(system.mock_ticket_tool.tickets, ())

    def test_denied_unsupported_action_does_not_create_ticket(self):
        system = create_demo_system(
            baselines={"resident-1": {"door_state": "closed"}},
            allowed_action_types=("notify_operator",),
            proposal_id_factory=lambda: "proposal-denied",
        )

        result = system.run(make_event("evt-denied", "resident-1", "open"))

        self.assertEqual(result["outcome"], WorkflowOutcome.DENIED)
        self.assertEqual(result["decision"].reason_codes, ("unsupported_action_type",))
        self.assertEqual(system.mock_ticket_tool.tickets, ())

    def test_repeating_same_event_and_proposal_creates_one_ticket(self):
        system = create_demo_system(
            baselines={"resident-1": {"door_state": "closed"}},
            proposal_id_factory=lambda: "proposal-repeat",
            ticket_id_factory=lambda: "ticket-repeat",
        )
        event = make_event("evt-repeat", "resident-1", "open")

        first = system.run(event)
        second = system.run(event)

        self.assertEqual(first["receipt"].external_reference, "ticket-repeat")
        self.assertEqual(second["receipt"].external_reference, "ticket-repeat")
        self.assertEqual(len(system.mock_ticket_tool.tickets), 1)

    def test_cannot_provide_both_baseline_mapping_and_store(self):
        with self.assertRaisesRegex(ValueError, "either baselines or baseline_store"):
            create_demo_system(
                baselines={"resident-1": {"door_state": "closed"}},
                baseline_store=InMemoryBaselineStore(),
            )


if __name__ == "__main__":
    unittest.main()