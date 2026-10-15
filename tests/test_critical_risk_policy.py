import unittest
from datetime import datetime, timezone

from aura.agents.critical_risk_policy import CriticalRiskPolicy, CriticalRiskThresholds
from aura.agents.guardian import GuardianAgent
from aura.contracts import (
    AssessmentSeverity,
    ActionProposal,
    Assessment,
    DecisionStatus,
    EvidenceItem,
    InputEvent,
    Transcript,
    TriageStage,
)
from aura.composition import create_demo_system
from aura.graph import WorkflowOutcome


def make_event(*, transcript=None, **overrides):
    observations = {
        "temperature_c": 20.0,
        "humidity_pct": 45.0,
        "door_state": "closed",
        "motion_detected": True,
    }
    observations.update(overrides)
    return InputEvent(
        event_id="evt-risk-1",
        occurred_at=datetime(2026, 10, 11, tzinfo=timezone.utc),
        subject_id="synthetic-resident-1",
        source="test-sensors",
        observations=observations,
        transcript=transcript,
    )


def make_assessment(
    event, *, is_anomaly=True, requires_review=False, confidence=0.9, severity=None
):
    return Assessment(
        event_id=event.event_id,
        is_anomaly=is_anomaly,
        requires_review=requires_review,
        summary="Synthetic sensor assessment",
        confidence=confidence,
        severity=severity,
        evidence=(
            EvidenceItem(
                source="supervisor",
                description="Synthetic sensor evidence",
                details={"fixture": "synthetic"},
            ),
        ),
    )


def make_proposal(
    event,
    *,
    action_type="request_human_review",
    confidence=0.9,
    triage_stage=TriageStage.INITIAL,
):
    return ActionProposal(
        proposal_id="proposal-risk-1",
        event_id=event.event_id,
        action_type=action_type,
        rationale="Request review of the event.",
        confidence=confidence,
        evidence_refs=("supervisor.evidence.0",),
        triage_stage=triage_stage,
    )


class CriticalRiskPolicyTests(unittest.TestCase):
    def test_noncritical_dismissal_is_approved_but_critical_requires_real_post_reply(self):
        policy = CriticalRiskPolicy()
        ordinary_event = make_event()
        ordinary = policy.evaluate(
            ordinary_event,
            make_assessment(ordinary_event, severity=AssessmentSeverity.MODERATE),
            make_proposal(ordinary_event, action_type="dismiss"),
        )
        critical_event = make_event(temperature_c=-20.0)
        first_pass = policy.evaluate(
            critical_event,
            make_assessment(critical_event, severity=AssessmentSeverity.CRITICAL),
            make_proposal(critical_event, action_type="dismiss", triage_stage=TriageStage.INITIAL),
        )
        no_reply = policy.evaluate(
            critical_event,
            make_assessment(critical_event, severity=AssessmentSeverity.CRITICAL),
            make_proposal(critical_event, action_type="dismiss", triage_stage=TriageStage.POST_REPLY),
        )
        replied_event = make_event(
            temperature_c=-20.0,
            transcript=Transcript("Jag mår bra.", "test-stt", "sv"),
        )
        replied = policy.evaluate(
            replied_event,
            make_assessment(replied_event, severity=AssessmentSeverity.CRITICAL),
            make_proposal(replied_event, action_type="dismiss", triage_stage=TriageStage.POST_REPLY),
        )

        self.assertEqual(ordinary.status, DecisionStatus.APPROVE)
        self.assertEqual(first_pass.status, DecisionStatus.DENY)
        self.assertEqual(no_reply.status, DecisionStatus.DENY)
        self.assertEqual(replied.status, DecisionStatus.APPROVE)

    def test_ask_resident_requires_initial_moderate_or_critical_severity(self):
        policy = CriticalRiskPolicy()
        for severity, stage, expected in (
            (AssessmentSeverity.MODERATE, TriageStage.INITIAL, DecisionStatus.APPROVE),
            (AssessmentSeverity.CRITICAL, TriageStage.INITIAL, DecisionStatus.APPROVE),
            (AssessmentSeverity.INFO, TriageStage.INITIAL, DecisionStatus.HOLD),
            (AssessmentSeverity.MODERATE, TriageStage.POST_REPLY, DecisionStatus.HOLD),
        ):
            with self.subTest(severity=severity, stage=stage):
                event = make_event()
                decision = policy.evaluate(
                    event,
                    make_assessment(event, severity=severity),
                    make_proposal(event, action_type="ask_resident", triage_stage=stage),
                )
                self.assertEqual(decision.status, expected)

    def test_remote_followup_and_home_visit_use_confidence_thresholds(self):
        policy = CriticalRiskPolicy(CriticalRiskThresholds(
            minimum_assessment_confidence=0.6,
            minimum_proposal_confidence=0.7,
        ))
        for action in ("remote_followup", "home_visit"):
            event = make_event()
            approved = policy.evaluate(
                event,
                make_assessment(event, severity=AssessmentSeverity.MODERATE, confidence=0.8),
                make_proposal(event, action_type=action, confidence=0.8),
            )
            held = policy.evaluate(
                event,
                make_assessment(event, severity=AssessmentSeverity.MODERATE, confidence=0.5),
                make_proposal(event, action_type=action, confidence=0.8),
            )
            self.assertEqual(approved.status, DecisionStatus.APPROVE)
            self.assertEqual(held.status, DecisionStatus.HOLD)

    def test_urgent_visit_is_held_as_home_visit_without_critical_support(self):
        event = make_event()

        decision = CriticalRiskPolicy().evaluate(
            event,
            make_assessment(event, severity=AssessmentSeverity.MODERATE),
            make_proposal(event, action_type="urgent_visit"),
        )

        self.assertEqual(decision.status, DecisionStatus.HOLD)
        self.assertEqual(decision.reason_codes, ("urgent_visit_downgraded_to_home_visit",))
        self.assertIn("home_visit", decision.rationale)

    def test_urgent_visit_is_approved_for_critical_severity_or_reason_code(self):
        policy = CriticalRiskPolicy()
        critical_severity_event = make_event()
        critical_severity = policy.evaluate(
            critical_severity_event,
            make_assessment(critical_severity_event, severity=AssessmentSeverity.CRITICAL),
            make_proposal(critical_severity_event, action_type="urgent_visit"),
        )
        critical_reason_event = make_event(temperature_c=-20.0)
        critical_reason = policy.evaluate(
            critical_reason_event,
            make_assessment(critical_reason_event, severity=AssessmentSeverity.MODERATE),
            make_proposal(critical_reason_event, action_type="urgent_visit"),
        )

        self.assertEqual(critical_severity.status, DecisionStatus.APPROVE)
        self.assertEqual(critical_severity.reason_codes, ("critical_severity",))
        self.assertEqual(critical_reason.status, DecisionStatus.APPROVE)
        self.assertIn("critical_low_temperature", critical_reason.reason_codes)

    def test_relative_and_planned_visit_actions_require_resident_profile_data(self):
        event = make_event()
        assessment = make_assessment(event, severity=AssessmentSeverity.MODERATE)
        policy_without_profile = CriticalRiskPolicy()
        for action, reason in (
            ("notify_relative", "relative_contact_unavailable"),
            ("attach_to_planned_visit", "planned_visit_unavailable"),
        ):
            decision = policy_without_profile.evaluate(
                event, assessment, make_proposal(event, action_type=action)
            )
            self.assertEqual(decision.status, DecisionStatus.HOLD)
            self.assertEqual(decision.reason_codes, (reason,))

        configured_policy = CriticalRiskPolicy(resident_contexts={
            event.subject_id: {
                "relative_contact": {"name": "Synthetic contact", "phone": "demo-only"},
                "next_planned_visit": "2026-10-16T10:00:00+00:00",
            }
        })
        for action in ("notify_relative", "attach_to_planned_visit"):
            decision = configured_policy.evaluate(
                event, assessment, make_proposal(event, action_type=action)
            )
            self.assertEqual(decision.status, DecisionStatus.APPROVE)

    def test_critical_cold_approves_human_review_with_reason_code(self):
        event = make_event(temperature_c=-10.0)

        decision = GuardianAgent(CriticalRiskPolicy())(
            event,
            make_assessment(event),
            make_proposal(event),
        )

        self.assertEqual(decision.status, DecisionStatus.APPROVE)
        self.assertEqual(decision.reason_codes, ("critical_low_temperature",))

    def test_critical_heat_and_humidity_thresholds_are_configurable(self):
        thresholds = CriticalRiskThresholds(
            critical_temperature_min_c=-20.0,
            critical_temperature_max_c=40.0,
            critical_humidity_min_pct=10.0,
            critical_humidity_max_pct=90.0,
        )
        policy = CriticalRiskPolicy(thresholds)

        hot_event = make_event(temperature_c=40.0)
        hot = policy.evaluate(hot_event, make_assessment(hot_event), make_proposal(hot_event))
        dry_event = make_event(humidity_pct=10.0)
        dry = policy.evaluate(dry_event, make_assessment(dry_event), make_proposal(dry_event))

        self.assertEqual(hot.status, DecisionStatus.APPROVE)
        self.assertEqual(hot.reason_codes, ("critical_high_temperature",))
        self.assertEqual(dry.status, DecisionStatus.APPROVE)
        self.assertEqual(dry.reason_codes, ("critical_low_humidity",))

    def test_open_door_without_motion_requests_human_review(self):
        event = make_event(door_state="open", motion_detected=False)

        decision = GuardianAgent(CriticalRiskPolicy())(
            event,
            make_assessment(event),
            make_proposal(event),
        )

        self.assertEqual(decision.status, DecisionStatus.APPROVE)
        self.assertEqual(decision.reason_codes, ("door_open_without_motion",))

    def test_supervisor_review_requirement_allows_review_action(self):
        event = make_event()

        decision = GuardianAgent(CriticalRiskPolicy())(
            event,
            make_assessment(event, is_anomaly=False, requires_review=True, confidence=0.0),
            make_proposal(event, confidence=0.0),
        )

        self.assertEqual(decision.status, DecisionStatus.APPROVE)
        self.assertEqual(decision.reason_codes, ("supervisor_review_required",))

    def test_noncritical_anomaly_is_held_and_does_not_authorize_dispatch(self):
        event = make_event(temperature_c=21.0)

        decision = GuardianAgent(CriticalRiskPolicy())(
            event,
            make_assessment(event),
            make_proposal(event),
        )

        self.assertEqual(decision.status, DecisionStatus.HOLD)
        self.assertEqual(decision.reason_codes, ("critical_threshold_not_met",))

    def test_missing_or_invalid_sensor_context_is_held(self):
        incomplete_event = make_event(temperature_c=-20.0)
        incomplete_event.observations.clear()
        incomplete_event.observations["temperature_c"] = -20.0
        invalid_event = make_event()
        invalid_event.observations["temperature_c"] = float("nan")

        for event, expected_reason in (
            (incomplete_event, "sensor_context_incomplete"),
            (invalid_event, "sensor_context_invalid"),
        ):
            with self.subTest(expected_reason=expected_reason):
                decision = GuardianAgent(CriticalRiskPolicy())(
                    event,
                    make_assessment(event),
                    make_proposal(event),
                )
                self.assertEqual(decision.status, DecisionStatus.HOLD)
                self.assertEqual(decision.reason_codes, (expected_reason,))

    def test_acoustic_features_alone_never_trigger_critical_approval(self):
        event = make_event(
            vocal_signal={"rms_amplitude": 0.0, "silence_ratio": 1.0}
        )

        decision = GuardianAgent(CriticalRiskPolicy())(
            event,
            make_assessment(event),
            make_proposal(event),
        )

        self.assertEqual(decision.status, DecisionStatus.HOLD)
        self.assertEqual(decision.reason_codes, ("critical_threshold_not_met",))

    def test_only_human_review_action_is_authorized(self):
        event = make_event(temperature_c=-30.0)

        decision = GuardianAgent(CriticalRiskPolicy())(
            event,
            make_assessment(event),
            make_proposal(event, action_type="change_schedule"),
        )

        self.assertEqual(decision.status, DecisionStatus.DENY)
        self.assertEqual(decision.reason_codes, ("unsupported_action_type",))

    def test_invalid_threshold_configuration_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "less than maximum"):
            CriticalRiskThresholds(
                critical_temperature_min_c=40.0,
                critical_temperature_max_c=40.0,
            )

    def test_critical_risk_policy_flows_through_composition_to_mock_ticket(self):
        event = InputEvent(
            event_id="evt-composed-critical",
            occurred_at=datetime(2026, 10, 11, tzinfo=timezone.utc),
            subject_id="synthetic-resident-1",
            source="sensor-test",
            observations={
                "temperature_c": -20.0,
                "humidity_pct": 45.0,
                "door_state": "closed",
                "motion_detected": True,
            },
        )
        system = create_demo_system(
            baselines={
                event.subject_id: {
                    "temperature_c": 20.0,
                    "humidity_pct": 45.0,
                    "door_state": "closed",
                    "motion_detected": True,
                }
            },
            guardian_policy=CriticalRiskPolicy(),
            proposal_id_factory=lambda: "proposal-critical",
            ticket_id_factory=lambda: "ticket-critical",
        )

        result = system.run(event)

        self.assertEqual(result["outcome"], WorkflowOutcome.DISPATCHED)
        self.assertEqual(result["decision"].reason_codes, ("critical_low_temperature",))
        self.assertEqual(result["receipt"].external_reference, "ticket-critical")


if __name__ == "__main__":
    unittest.main()