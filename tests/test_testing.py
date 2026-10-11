import json
import unittest
from datetime import datetime, timezone

from aura.agents.triage import TriageAgent
from aura.contracts import Assessment, EvidenceItem, InputEvent
from aura.testing import DeterministicFakeChatModel, InMemoryBaselineStore


def make_event():
    return InputEvent(
        event_id="evt-fake-1",
        occurred_at=datetime(2026, 10, 11, tzinfo=timezone.utc),
        subject_id="synthetic-resident-1",
        source="test-fixture",
        observations={"door_state": "open"},
    )


def make_assessment(event):
    return Assessment(
        event_id=event.event_id,
        is_anomaly=True,
        summary="Synthetic event differs from baseline.",
        confidence=0.9,
        evidence=(
            EvidenceItem(
                source="baseline_comparison",
                description="door_state differs from baseline",
                details={"observed": "open", "baseline": "closed"},
            ),
        ),
    )


class DeterministicFakeChatModelTests(unittest.TestCase):
    def test_repeated_triage_inputs_produce_the_same_proposal(self):
        event = make_event()
        assessment = make_assessment(event)
        agent = TriageAgent(
            DeterministicFakeChatModel(),
            proposal_id_factory=lambda: "proposal-fixed",
        )

        first = agent(event, assessment)
        second = agent(event, assessment)

        self.assertEqual(first, second)
        self.assertEqual(first.action_type, "request_human_review")
        self.assertEqual(first.evidence_refs, ("supervisor.evidence.0",))

    def test_fake_uses_first_allowed_action_when_review_is_not_allowed(self):
        event = make_event()
        assessment = make_assessment(event)
        agent = TriageAgent(
            DeterministicFakeChatModel(),
            allowed_action_types=("notify_operator", "create_ticket"),
            proposal_id_factory=lambda: "proposal-fixed",
        )

        proposal = agent(event, assessment)

        self.assertEqual(proposal.action_type, "notify_operator")

    def test_configured_action_must_be_allowed(self):
        model = DeterministicFakeChatModel(action_type="unsupported")
        prompt = json.dumps(
            {
                "allowed_action_types": ["request_human_review"],
                "context": {
                    "supervisor_assessment": {
                        "evidence": [{"ref": "supervisor.evidence.0"}]
                    }
                },
            }
        )

        with self.assertRaisesRegex(ValueError, "not allowed by the prompt"):
            model.complete(system_prompt="", user_prompt=prompt)

    def test_fake_rejects_prompt_without_evidence(self):
        model = DeterministicFakeChatModel()
        prompt = json.dumps(
            {
                "allowed_action_types": ["request_human_review"],
                "context": {"supervisor_assessment": {"evidence": []}},
            }
        )

        with self.assertRaisesRegex(ValueError, "must include Supervisor evidence"):
            model.complete(system_prompt="", user_prompt=prompt)


class InMemoryBaselineStoreTests(unittest.TestCase):
    def test_get_baseline_returns_matching_profile(self):
        store = InMemoryBaselineStore(
            {
                "resident-1": {"temperature_c": 18.0, "door_state": "closed"},
                "resident-2": {"temperature_c": 20.0},
            }
        )

        baseline = store.get_baseline("resident-1")

        self.assertEqual(
            baseline,
            {"temperature_c": 18.0, "door_state": "closed"},
        )
        self.assertEqual(store.get_baseline("resident-2"), {"temperature_c": 20.0})

    def test_unknown_subject_returns_none_for_supervisor_review_path(self):
        store = InMemoryBaselineStore({"known-resident": {"door_state": "closed"}})

        self.assertIsNone(store.get_baseline("unknown-resident"))

    def test_store_copies_inputs_and_returns_isolated_snapshots(self):
        input_baseline = {"sensor": {"readings": [1, 2]}}
        store = InMemoryBaselineStore({"resident-1": input_baseline})
        input_baseline["sensor"]["readings"].append(3)

        first_snapshot = store.get_baseline("resident-1")
        first_snapshot["sensor"]["readings"].append(4)

        self.assertEqual(
            store.get_baseline("resident-1"),
            {"sensor": {"readings": [1, 2]}},
        )

    def test_set_and_remove_baseline(self):
        store = InMemoryBaselineStore()
        store.set_baseline("resident-1", {"door_state": "closed"})

        self.assertEqual(store.get_baseline("resident-1"), {"door_state": "closed"})
        self.assertTrue(store.remove_baseline("resident-1"))
        self.assertIsNone(store.get_baseline("resident-1"))
        self.assertFalse(store.remove_baseline("resident-1"))

    def test_rejects_non_json_and_non_string_keys(self):
        with self.assertRaisesRegex(ValueError, "not JSON-compatible"):
            InMemoryBaselineStore({"resident-1": {"bad": object()}})
        with self.assertRaisesRegex(ValueError, "keys must be non-empty strings"):
            InMemoryBaselineStore({"resident-1": {"nested": {1: "invalid"}}})

    def test_rejects_empty_subject_id(self):
        store = InMemoryBaselineStore()

        with self.assertRaisesRegex(ValueError, "subject_id must be a non-empty string"):
            store.get_baseline(" ")


if __name__ == "__main__":
    unittest.main()