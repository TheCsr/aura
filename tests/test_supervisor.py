import unittest
from datetime import datetime, timezone

from aura.agents.supervisor import (
    AssessmentValidator,
    BaselineComparisonStrategy,
    SupervisorAgent,
)
from aura.contracts import Assessment, InputEvent


class FakeBaselineStore:
    def __init__(self, baseline):
        self.baseline = baseline
        self.subject_ids = []

    def get_baseline(self, subject_id):
        self.subject_ids.append(subject_id)
        return self.baseline


def make_event(observations=None):
    return InputEvent(
        event_id="evt-1",
        occurred_at=datetime(2026, 10, 11, tzinfo=timezone.utc),
        subject_id="synthetic-1",
        source="test",
        observations=observations or {},
    )


class SupervisorAgentTests(unittest.TestCase):
    def test_matching_observations_are_assessed_as_normal(self):
        event = make_event({"temperature_c": 18.0, "door_state": "closed"})
        store = FakeBaselineStore({"temperature_c": 18.0, "door_state": "closed"})
        agent = SupervisorAgent(store)

        assessment = agent(event)

        self.assertFalse(assessment.is_anomaly)
        self.assertFalse(assessment.requires_review)
        self.assertEqual(assessment.confidence, 1.0)
        self.assertEqual(store.subject_ids, [event.subject_id])

    def test_numeric_tolerance_prevents_small_change_from_being_anomaly(self):
        event = make_event({"temperature_c": 18.5})
        store = FakeBaselineStore({"temperature_c": 18.0})
        agent = SupervisorAgent(
            store,
            strategy=BaselineComparisonStrategy(numeric_tolerances={"temperature_c": 1.0}),
        )

        assessment = agent.run(event)

        self.assertFalse(assessment.is_anomaly)
        self.assertEqual(assessment.evidence[0].details["compared_fields"], ["temperature_c"])

    def test_change_outside_numeric_tolerance_is_anomaly_with_evidence(self):
        event = make_event({"temperature_c": 21.0})
        store = FakeBaselineStore({"temperature_c": 18.0})
        agent = SupervisorAgent(
            store,
            strategy=BaselineComparisonStrategy(numeric_tolerances={"temperature_c": 1.0}),
        )

        assessment = agent.run(event)

        self.assertTrue(assessment.is_anomaly)
        self.assertFalse(assessment.requires_review)
        self.assertEqual(assessment.evidence[0].details["observed"], 21.0)
        self.assertEqual(assessment.evidence[0].details["baseline"], 18.0)

    def test_missing_baseline_is_review_not_a_confirmed_anomaly(self):
        event = make_event({"door_state": "open"})
        agent = SupervisorAgent(FakeBaselineStore(None))

        assessment = agent.run(event)

        self.assertFalse(assessment.is_anomaly)
        self.assertTrue(assessment.requires_review)
        self.assertEqual(assessment.confidence, 0.0)

    def test_no_comparable_fields_requires_review(self):
        event = make_event({"new_sensor": 4})
        agent = SupervisorAgent(FakeBaselineStore({"temperature_c": 18.0}))

        assessment = agent.run(event)

        self.assertFalse(assessment.is_anomaly)
        self.assertTrue(assessment.requires_review)
        self.assertEqual(assessment.evidence[0].source, "baseline_comparison")

    def test_validator_rejects_assessment_for_another_event(self):
        event = make_event()
        assessment = Assessment(
            event_id="different-event",
            is_anomaly=False,
            summary="Mismatch",
            confidence=1.0,
        )

        with self.assertRaisesRegex(ValueError, "must match the input event"):
            AssessmentValidator().validate(event, assessment)

    def test_invalid_numeric_tolerance_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "finite, non-negative"):
            BaselineComparisonStrategy(numeric_tolerances={"temperature_c": -1.0})


if __name__ == "__main__":
    unittest.main()