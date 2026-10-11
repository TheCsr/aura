"""Run the mock-backed AURA workflow against a synthetic scenario."""

import argparse
from datetime import datetime, timezone
import json
from typing import Any, Dict, Optional, Sequence

from .composition import create_demo_system
from .contracts import Assessment, GuardianDecision, InputEvent
from .graph import WorkflowOutcome, WorkflowState


SCENARIOS = ("normal", "anomaly", "missing-baseline")


def _make_scenario(name: str) -> tuple[InputEvent, Dict[str, Dict[str, Any]]]:
    subject_id = "synthetic-resident-1"
    baseline = {subject_id: {"door_state": "closed", "temperature_c": 19.0}}
    observed_door_state = "closed" if name == "normal" else "open"
    event = InputEvent(
        event_id=f"cli-{name}",
        occurred_at=datetime.now(timezone.utc),
        subject_id=subject_id,
        source="synthetic-cli-scenario",
        observations={"door_state": observed_door_state, "temperature_c": 19.0},
    )
    if name == "missing-baseline":
        baseline = {}
    return event, baseline


def _assessment_result(assessment: Optional[Assessment]) -> Optional[Dict[str, Any]]:
    if assessment is None:
        return None
    return {
        "event_id": assessment.event_id,
        "is_anomaly": assessment.is_anomaly,
        "requires_review": assessment.requires_review,
        "summary": assessment.summary,
        "confidence": assessment.confidence,
        "evidence": [
            {
                "source": item.source,
                "description": item.description,
                "details": dict(item.details),
            }
            for item in assessment.evidence
        ],
    }


def _proposal_result(proposal: Any) -> Optional[Dict[str, Any]]:
    if proposal is None:
        return None
    return {
        "proposal_id": proposal.proposal_id,
        "event_id": proposal.event_id,
        "action_type": proposal.action_type,
        "rationale": proposal.rationale,
        "confidence": proposal.confidence,
        "parameters": dict(proposal.parameters),
        "evidence_refs": list(proposal.evidence_refs),
    }


def _guardian_result(decision: Optional[GuardianDecision]) -> Optional[Dict[str, Any]]:
    if decision is None:
        return None
    return {
        "event_id": decision.event_id,
        "proposal_id": decision.proposal_id,
        "status": decision.status.value,
        "reason_codes": list(decision.reason_codes),
        "rationale": decision.rationale,
    }


def _workflow_output(event: InputEvent, result: WorkflowState) -> Dict[str, Any]:
    receipt = result["receipt"]
    return {
        "event": {
            "event_id": event.event_id,
            "occurred_at": event.occurred_at.isoformat(),
            "subject_id": event.subject_id,
            "source": event.source,
            "observations": dict(event.observations),
        },
        "agents": {
            "supervisor": _assessment_result(result["assessment"]),
            "triage": _proposal_result(result["proposal"]),
            "guardian": _guardian_result(result["decision"]),
            "dispatcher": (
                {
                    "event_id": receipt.event_id,
                    "proposal_id": receipt.proposal_id,
                    "status": receipt.status.value,
                    "message": receipt.message,
                    "external_reference": receipt.external_reference,
                }
                if receipt is not None
                else None
            ),
        },
        "outcome": result["outcome"].value,
        "mock_ticket_id": (
            receipt.external_reference
            if receipt is not None
            and result["outcome"] is WorkflowOutcome.DISPATCHED
            else None
        ),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run a synthetic AURA agent scenario.")
    parser.add_argument(
        "scenario",
        nargs="?",
        choices=SCENARIOS,
        default="anomaly",
        help="scenario to execute (default: anomaly)",
    )
    args = parser.parse_args(argv)

    event, baselines = _make_scenario(args.scenario)
    system = create_demo_system(
        baselines=baselines,
        proposal_id_factory=lambda: f"proposal-{args.scenario}",
        ticket_id_factory=lambda: f"mock-ticket-{args.scenario}",
    )
    result = system.run(event)
    print(json.dumps(_workflow_output(event, result), ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())