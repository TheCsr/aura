import json
import unittest
from datetime import datetime, timezone

from aura.agents.triage import EvidencePacker, StructuredProposalParser, TriageAgent
from aura.contracts import Assessment, EvidenceItem, InputEvent, Transcript


class FakeChatModel:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def complete(self, *, system_prompt, user_prompt):
        self.calls.append((system_prompt, user_prompt))
        return self.response


def make_event(transcript=None, observations=None):
    return InputEvent(
        event_id="evt-1",
        occurred_at=datetime(2026, 10, 11, tzinfo=timezone.utc),
        subject_id="synthetic-1",
        source="test",
        observations=observations or {"door_state": "open"},
        transcript=transcript,
    )


def make_assessment(event):
    return Assessment(
        event_id=event.event_id,
        is_anomaly=True,
        summary="Door state differs from the synthetic baseline.",
        confidence=1.0,
        evidence=(
            EvidenceItem(
                source="baseline_comparison",
                description="door_state changed",
                details={"observed": "open", "baseline": "closed"},
            ),
        ),
    )


def model_response(**overrides):
    payload = {
        "action_type": "request_human_review",
        "rationale": "The event differs from the supplied baseline.",
        "confidence": 0.85,
        "parameters": {"priority": "review"},
        "evidence_refs": ["supervisor.evidence.0"],
    }
    payload.update(overrides)
    return json.dumps(payload)


class TriageAgentTests(unittest.TestCase):
    def test_agent_builds_valid_event_bound_proposal(self):
        event = make_event(
            transcript=Transcript(
                text="Aj, min höft", source="kb-whisper", language="sv", model="kb-whisper"
            )
        )
        assessment = make_assessment(event)
        model = FakeChatModel(model_response())
        agent = TriageAgent(model, proposal_id_factory=lambda: "proposal-1")

        proposal = agent(event, assessment)

        self.assertEqual(proposal.proposal_id, "proposal-1")
        self.assertEqual(proposal.event_id, event.event_id)
        self.assertEqual(proposal.action_type, "request_human_review")
        self.assertEqual(proposal.evidence_refs, ("supervisor.evidence.0",))
        self.assertIn("untrusted data", model.calls[0][0])
        self.assertIn("kb-whisper", model.calls[0][1])

    def test_agent_rejects_normal_assessment_without_calling_model(self):
        event = make_event()
        assessment = Assessment(
            event_id=event.event_id,
            is_anomaly=False,
            summary="Baseline match",
            confidence=1.0,
        )
        model = FakeChatModel(model_response())

        with self.assertRaisesRegex(ValueError, "anomalous or review-required"):
            TriageAgent(model)(event, assessment)
        self.assertEqual(model.calls, [])

    def test_agent_rejects_assessment_for_another_event(self):
        event = make_event()
        assessment = Assessment(
            event_id="other-event",
            is_anomaly=True,
            summary="Mismatch",
            confidence=1.0,
        )

        with self.assertRaisesRegex(ValueError, "must match the input event"):
            TriageAgent(FakeChatModel(model_response()))(event, assessment)

    def test_parser_rejects_invalid_json(self):
        event = make_event()

        with self.assertRaisesRegex(ValueError, "valid JSON"):
            StructuredProposalParser().parse(
                "not json",
                event=event,
                evidence_refs=("supervisor.evidence.0",),
                proposal_id="proposal-1",
                allowed_action_types=("request_human_review",),
            )

    def test_parser_rejects_unsupported_actions_and_unknown_evidence(self):
        event = make_event()
        parser = StructuredProposalParser()
        common = {
            "event": event,
            "evidence_refs": ("supervisor.evidence.0",),
            "proposal_id": "proposal-1",
            "allowed_action_types": ("request_human_review",),
        }

        with self.assertRaisesRegex(ValueError, "unsupported action_type"):
            parser.parse(model_response(action_type="send_medication"), **common)
        with self.assertRaisesRegex(ValueError, "unknown evidence"):
            parser.parse(
                model_response(evidence_refs=["supervisor.evidence.99"]), **common
            )

    def test_parser_rejects_extra_fields_and_invalid_confidence(self):
        event = make_event()
        parser = StructuredProposalParser()
        common = {
            "event": event,
            "evidence_refs": ("supervisor.evidence.0",),
            "proposal_id": "proposal-1",
            "allowed_action_types": ("request_human_review",),
        }

        with self.assertRaisesRegex(ValueError, "exactly the required"):
            parser.parse(model_response(authorized=True), **common)
        with self.assertRaisesRegex(ValueError, "confidence must be a finite number"):
            parser.parse(model_response(confidence=True), **common)

    def test_evidence_packer_respects_context_size_limit(self):
        event = make_event(
            transcript=Transcript(text="x" * 5000, source="test"),
            observations={"payload": "y" * 5000},
        )
        assessment = Assessment(
            event_id=event.event_id,
            is_anomaly=True,
            summary="z" * 3000,
            confidence=1.0,
            evidence=(
                EvidenceItem(
                    source="test",
                    description="Evidence",
                    details={"value": "q" * 3000},
                ),
            ),
        )

        package = EvidencePacker(max_context_chars=1200).pack(event, assessment)

        self.assertLessEqual(len(package.context_json), 1200)
        self.assertEqual(package.evidence_refs, ("supervisor.evidence.0",))
        packed = json.loads(package.context_json)
        self.assertEqual(packed["omitted_evidence_count"], 0)
        self.assertEqual(packed["evidence_details_truncated_count"], 1)

    def test_agent_does_not_call_model_without_supervisor_evidence(self):
        event = make_event()
        assessment = Assessment(
            event_id=event.event_id,
            is_anomaly=True,
            summary="No evidence supplied",
            confidence=0.0,
        )
        model = FakeChatModel(model_response())

        with self.assertRaisesRegex(ValueError, "at least one Supervisor evidence item"):
            TriageAgent(model)(event, assessment)
        self.assertEqual(model.calls, [])


if __name__ == "__main__":
    unittest.main()