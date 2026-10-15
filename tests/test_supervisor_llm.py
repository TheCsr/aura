import json
import unittest
from datetime import datetime, timezone

from aura.agents.supervisor import SupervisorAgent
from aura.agents.supervisor_llm import AssessmentResponseParser, LlmAssessmentStrategy
from aura.contracts import InputEvent
from aura.testing import DeterministicFakeAssessmentChatModel


class StaticChatModel:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def complete(self, *, system_prompt, user_prompt):
        self.calls.append((system_prompt, user_prompt))
        if self.error is not None:
            raise self.error
        return self.response


class FakeBaselineStore:
    def __init__(self, baseline):
        self.baseline = baseline

    def get_baseline(self, subject_id):
        return self.baseline


def make_event(observations=None):
    return InputEvent(
        event_id="evt-supervisor-llm",
        occurred_at=datetime(2026, 10, 11, tzinfo=timezone.utc),
        subject_id="synthetic-1",
        source="sensor-test",
        observations=observations or {"temperature_c": 21.0, "door_state": "open"},
    )


def response(**overrides):
    payload = {
        "is_anomaly": True,
        "severity": "moderate",
        "summary": "The door state differs from its baseline.",
        "confidence": 0.9,
        "evidence_fields": ["door_state"],
    }
    payload.update(overrides)
    return json.dumps(payload)


class LlmAssessmentStrategyTests(unittest.TestCase):
    def test_fake_llm_strategy_assesses_anomaly_with_grounded_evidence(self):
        event = make_event()
        strategy = LlmAssessmentStrategy(DeterministicFakeAssessmentChatModel())

        assessment = strategy.assess(
            event,
            {"temperature_c": 18.0, "door_state": "closed"},
        )

        self.assertTrue(assessment.is_anomaly)
        self.assertEqual(assessment.event_id, event.event_id)
        self.assertEqual(assessment.severity.value, "moderate")
        self.assertEqual(
            {item.details["field"] for item in assessment.evidence},
            {"temperature_c", "door_state"},
        )

    def test_fake_llm_strategy_assesses_matching_values_as_normal(self):
        event = make_event({"temperature_c": 18.0, "door_state": "closed"})
        model = StaticChatModel(response())
        strategy = LlmAssessmentStrategy(model)

        assessment = strategy.assess(
            event,
            {"temperature_c": 18.0, "door_state": "closed"},
        )

        self.assertFalse(assessment.is_anomaly)
        self.assertFalse(assessment.requires_review)
        self.assertEqual(assessment.drop_reason, "within_baseline")
        self.assertEqual(model.calls, [])

    def test_invalid_model_output_fails_closed_to_review(self):
        event = make_event()
        strategy = LlmAssessmentStrategy(StaticChatModel("not-json"))

        assessment = strategy.assess(
            event,
            {"temperature_c": 18.0, "door_state": "closed"},
        )

        self.assertFalse(assessment.is_anomaly)
        self.assertTrue(assessment.requires_review)
        self.assertEqual(assessment.confidence, 0.0)

    def test_model_exception_fails_closed_to_review(self):
        event = make_event()
        strategy = LlmAssessmentStrategy(StaticChatModel(error=RuntimeError("model offline")))

        assessment = strategy.assess(
            event,
            {"temperature_c": 18.0, "door_state": "closed"},
        )

        self.assertTrue(assessment.requires_review)
        self.assertFalse(assessment.is_anomaly)

    def test_anomaly_cannot_cite_unchanged_or_unavailable_sensor_field(self):
        event = make_event({"temperature_c": 18.0, "door_state": "open"})
        baseline = {"temperature_c": 18.0, "door_state": "closed"}
        parser = AssessmentResponseParser()
        comparable_fields = ["temperature_c", "door_state"]

        with self.assertRaisesRegex(ValueError, "does not show a baseline difference"):
            parser.parse(
                response(evidence_fields=["temperature_c"]),
                event=event,
                baseline=baseline,
                comparable_fields=comparable_fields,
                numeric_tolerances={},
            )
        with self.assertRaisesRegex(ValueError, "not present in the event baseline"):
            parser.parse(
                response(evidence_fields=["motion_detected"]),
                event=event,
                baseline=baseline,
                comparable_fields=comparable_fields,
                numeric_tolerances={},
            )

    def test_parser_rejects_unknown_severity(self):
        event = make_event()
        with self.assertRaisesRegex(ValueError, "severity must be one of"):
            AssessmentResponseParser().parse(
                response(severity="emergency"),
                event=event,
                baseline={"temperature_c": 18.0, "door_state": "closed"},
                comparable_fields=("temperature_c", "door_state"),
                numeric_tolerances={},
            )

    def test_supervisor_agent_validator_still_checks_event_identity(self):
        event = make_event()
        agent = SupervisorAgent(
            FakeBaselineStore({"temperature_c": 18.0, "door_state": "closed"}),
            strategy=LlmAssessmentStrategy(DeterministicFakeAssessmentChatModel()),
        )

        assessment = agent.run(event)

        self.assertEqual(assessment.event_id, event.event_id)
        self.assertTrue(assessment.is_anomaly)


if __name__ == "__main__":
    unittest.main()