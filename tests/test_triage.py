import json
import unittest
from datetime import datetime, timezone

from aura.agents.triage import (
    INITIAL_ACTION_TYPES,
    POST_REPLY_ACTION_TYPES,
    RESIDENT_REPLY_EVIDENCE_REF,
    EvidencePacker,
    ResidentQuestionValidator,
    StructuredProposalParser,
    TriageAgent,
)
from aura.contracts import Assessment, EvidenceItem, InputEvent, Transcript, TriageStage


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
    def test_initial_pass_omits_transcript_and_requires_swedish_question(self):
        event = make_event(
            transcript=Transcript("Sensitive resident reply", "test-stt", "sv")
        )
        model = FakeChatModel(model_response(
            action_type="ask_resident",
            parameters={"question_sv": "Är allt bra hemma?"},
        ))

        proposal = TriageAgent(model).run(
            event,
            make_assessment(event),
            stage=TriageStage.INITIAL,
        )

        payload = json.loads(model.calls[0][1])
        self.assertEqual(tuple(payload["allowed_action_types"]), INITIAL_ACTION_TYPES)
        self.assertEqual(payload["context"]["event"]["transcript"], None)
        self.assertIn("short, plain Swedish question", model.calls[0][0])
        self.assertEqual(proposal.action_type, "ask_resident")
        self.assertEqual(proposal.parameters["question_sv"], "Är allt bra hemma?")
        self.assertEqual(proposal.triage_stage, TriageStage.INITIAL)

    def test_invalid_initial_questions_fall_back_to_existing_swedish_template(self):
        event = make_event()
        assessment = make_assessment(event)
        from aura.streaming.voice import SensorAnomalyPromptBuilder

        fallback = SensorAnomalyPromptBuilder().build(event, assessment)
        self.assertLessEqual(len(fallback), 200)
        invalid_questions = (
            None,
            "Are you feeling okay?",
            "Är du bra? " * 30,
            "Har du diabetes?",
            "Stanna hemma och vila?",
            "Du bör stanna hemma i kväll?",
            "Håll dig inomhus?",
            "Kan du läsa https://example.com och svara?",
            "<script>Är du bra?</script>",
        )
        for question in invalid_questions:
            with self.subTest(question=question):
                model = FakeChatModel(model_response(
                    action_type="ask_resident",
                    parameters={} if question is None else {"question_sv": question},
                ))
                proposal = TriageAgent(model).run(
                    event,
                    assessment,
                    stage=TriageStage.INITIAL,
                )
                self.assertEqual(proposal.parameters["question_sv"], fallback)
                self.assertEqual(proposal.action_type, "ask_resident")

    def test_post_reply_pass_includes_transcript_and_acoustic_features(self):
        event = make_event(
            transcript=Transcript("Jag föll och har ont.", "test-stt", "sv"),
            observations={
                "door_state": "open",
                "vocal_signal_features": {"rms_amplitude": 0.2},
            },
        )
        model = FakeChatModel(model_response(
            action_type="remote_followup",
            rationale='Residenten sade "Jag föll" och behöver följas upp.',
            evidence_refs=["supervisor.evidence.0", RESIDENT_REPLY_EVIDENCE_REF],
        ))

        proposal = TriageAgent(model).run(
            event,
            make_assessment(event),
            stage=TriageStage.POST_REPLY,
        )

        prompt, payload = model.calls[0]
        context = json.loads(payload)["context"]
        self.assertEqual(tuple(json.loads(payload)["allowed_action_types"]), POST_REPLY_ACTION_TYPES)
        self.assertEqual(context["event"]["transcript"]["text"], event.transcript.text)
        self.assertEqual(context["event"]["observations"]["vocal_signal_features"]["rms_amplitude"], 0.2)
        self.assertIn(RESIDENT_REPLY_EVIDENCE_REF, json.loads(payload)["allowed_evidence_refs"])
        self.assertIn("quoting one short exact phrase", prompt)
        self.assertEqual(proposal.triage_stage, TriageStage.POST_REPLY)

    def test_post_reply_missing_reply_reference_gets_only_one_repair_retry(self):
        event = make_event(transcript=Transcript("Jag föll hemma.", "test-stt", "sv"))
        model = FakeChatModel(model_response(
            action_type="remote_followup",
            rationale="The resident needs a follow-up.",
            evidence_refs=["supervisor.evidence.0"],
        ))

        with self.assertRaisesRegex(ValueError, "unknown evidence"):
            TriageAgent(model).run(event, make_assessment(event), stage=TriageStage.POST_REPLY)
        self.assertEqual(len(model.calls), 2)
        self.assertIn("correction", json.loads(model.calls[1][1]))

    def test_post_reply_evidence_retry_can_correct_to_transcript_reference(self):
        class RetryingPostReplyModel:
            def __init__(self):
                self.responses = [
                    model_response(
                        action_type="remote_followup",
                        rationale='Residenten sade "Jag föll" och behöver följas upp.',
                        evidence_refs=["supervisor.evidence.99"],
                    ),
                    model_response(
                        action_type="remote_followup",
                        rationale='Residenten sade "Jag föll" och behöver följas upp.',
                        evidence_refs=["supervisor.evidence.0", RESIDENT_REPLY_EVIDENCE_REF],
                    ),
                ]
                self.calls = []

            def complete(self, *, system_prompt, user_prompt):
                self.calls.append((system_prompt, json.loads(user_prompt)))
                return self.responses.pop(0)

        event = make_event(transcript=Transcript("Jag föll hemma.", "test-stt", "sv"))
        model = RetryingPostReplyModel()
        proposal = TriageAgent(model, proposal_id_factory=lambda: "proposal-repaired").run(
            event,
            make_assessment(event),
            stage=TriageStage.POST_REPLY,
        )

        self.assertEqual(len(model.calls), 2)
        expected_refs = ["supervisor.evidence.0", RESIDENT_REPLY_EVIDENCE_REF]
        self.assertEqual(model.calls[0][1]["allowed_evidence_refs"], expected_refs)
        self.assertEqual(model.calls[1][1]["allowed_evidence_refs"], expected_refs)
        self.assertIn("correction", model.calls[1][1])
        self.assertEqual(proposal.proposal_id, "proposal-repaired")
        self.assertEqual(proposal.evidence_refs, tuple(expected_refs))

    def test_post_reply_action_allowlist_excludes_ask_resident(self):
        event = make_event(transcript=Transcript("Jag mår bra.", "test-stt", "sv"))
        model = FakeChatModel(model_response(
            action_type="ask_resident",
            rationale='Residenten svarade "Jag mår bra".',
            parameters={"question_sv": "Är du säker?"},
            evidence_refs=["supervisor.evidence.0", RESIDENT_REPLY_EVIDENCE_REF],
        ))

        with self.assertRaisesRegex(ValueError, "unsupported action_type"):
            TriageAgent(model).run(event, make_assessment(event), stage=TriageStage.POST_REPLY)

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

    def test_agent_retries_once_when_model_cites_unknown_evidence(self):
        class RetryingChatModel:
            def __init__(self):
                self.responses = [
                    model_response(evidence_refs=["supervisor.evidence.99"]),
                    model_response(),
                ]
                self.calls = []

            def complete(self, *, system_prompt, user_prompt):
                self.calls.append((system_prompt, json.loads(user_prompt)))
                return self.responses.pop(0)

        event = make_event()
        model = RetryingChatModel()
        proposal = TriageAgent(model)(event, make_assessment(event))

        self.assertEqual(proposal.evidence_refs, ("supervisor.evidence.0",))
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(model.calls[0][1]["allowed_evidence_refs"], ["supervisor.evidence.0"])
        self.assertIn("correction", model.calls[1][1])

    def test_agent_still_rejects_unknown_evidence_after_one_retry(self):
        class InvalidReferenceChatModel:
            def complete(self, *, system_prompt, user_prompt):
                return model_response(evidence_refs=["supervisor.evidence.99"])

        event = make_event()
        with self.assertRaisesRegex(ValueError, "unknown evidence"):
            TriageAgent(InvalidReferenceChatModel())(event, make_assessment(event))

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
        self.assertEqual(
            package.evidence_refs,
            ("supervisor.evidence.0", "resident.reply.transcript"),
        )
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