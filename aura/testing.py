"""Deterministic test and demo adapters for AURA components."""

import json
import math
from threading import RLock
from typing import Dict, Mapping, Optional

from .contracts import ActionType, JsonValue
from .ports import BaselineStore


def _copy_json_value(value: object, path: str) -> JsonValue:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if type(value) is bool:
        return value
    if type(value) is int:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{path} must not contain NaN or infinity")
        return value
    if isinstance(value, list):
        return [_copy_json_value(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, Mapping):
        copied: Dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise ValueError(f"{path} keys must be non-empty strings")
            copied[key] = _copy_json_value(item, f"{path}.{key}")
        return copied
    raise ValueError(f"{path} contains a value that is not JSON-compatible")


def _copy_json_object(value: Mapping[str, JsonValue], name: str) -> Dict[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    copied = _copy_json_value(value, name)
    if not isinstance(copied, dict):
        raise ValueError(f"{name} must be a JSON object")
    return copied


class InMemoryBaselineStore:
    """Thread-safe baseline snapshots for deterministic tests and demos."""

    def __init__(
        self,
        baselines: Optional[Mapping[str, Mapping[str, JsonValue]]] = None,
    ) -> None:
        self._lock = RLock()
        self._baselines: Dict[str, Dict[str, JsonValue]] = {}
        for subject_id, baseline in (baselines or {}).items():
            self.set_baseline(subject_id, baseline)

    def get_baseline(self, subject_id: str) -> Optional[Mapping[str, JsonValue]]:
        self._validate_subject_id(subject_id)
        with self._lock:
            baseline = self._baselines.get(subject_id)
            if baseline is None:
                return None
            return _copy_json_object(baseline, "baseline")

    def set_baseline(self, subject_id: str, baseline: Mapping[str, JsonValue]) -> None:
        self._validate_subject_id(subject_id)
        copied = _copy_json_object(baseline, "baseline")
        with self._lock:
            self._baselines[subject_id] = copied

    def remove_baseline(self, subject_id: str) -> bool:
        self._validate_subject_id(subject_id)
        with self._lock:
            return self._baselines.pop(subject_id, None) is not None

    @staticmethod
    def _validate_subject_id(subject_id: str) -> None:
        if not isinstance(subject_id, str) or not subject_id.strip():
            raise ValueError("subject_id must be a non-empty string")


class DeterministicFakeChatModel:
    """Return a stable Triage proposal based only on the supplied prompt context."""

    def __init__(
        self,
        *,
        action_type: Optional[str] = None,
        rationale: str = "Requesting a human review of the supplied evidence.",
        confidence: float = 0.85,
        parameters: Optional[Mapping[str, JsonValue]] = None,
        scripted_responses: Optional[Mapping[str, Mapping[str, JsonValue]]] = None,
    ) -> None:
        if action_type is not None and (not isinstance(action_type, str) or not action_type.strip()):
            raise ValueError("action_type must be a non-empty string when provided")
        if not isinstance(rationale, str) or not rationale.strip():
            raise ValueError("rationale must be a non-empty string")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence)
            or not 0.0 <= confidence <= 1.0
        ):
            raise ValueError("confidence must be a finite number between 0 and 1")

        supplied_parameters = dict(parameters or {})
        try:
            self._parameters = json.loads(
                json.dumps(supplied_parameters, allow_nan=False, separators=(",", ":"))
            )
        except (TypeError, ValueError) as error:
            raise ValueError("parameters must contain only JSON-compatible values") from error
        if not isinstance(self._parameters, dict):
            raise ValueError("parameters must be a JSON object")

        self._action_type = action_type
        self._rationale = rationale
        self._confidence = float(confidence)
        self._scripted_responses: Dict[str, Dict[str, JsonValue]] = {}
        for stage, response in (scripted_responses or {}).items():
            if stage not in {"initial", "post_reply"}:
                raise ValueError("scripted response stages must be initial or post_reply")
            self._scripted_responses[stage] = _copy_json_object(response, f"{stage} response")

    def complete(self, *, system_prompt: str, user_prompt: str) -> str:
        try:
            prompt = json.loads(user_prompt)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("fake chat model expected a JSON user prompt") from error
        if not isinstance(prompt, dict):
            raise ValueError("fake chat model expected a JSON object prompt")

        allowed_action_types = prompt.get("allowed_action_types")
        context = prompt.get("context")
        if (
            not isinstance(allowed_action_types, list)
            or not allowed_action_types
            or any(not isinstance(action, str) or not action.strip() for action in allowed_action_types)
        ):
            raise ValueError("prompt must provide non-empty allowed_action_types")
        if self._action_type is not None and self._action_type not in allowed_action_types:
            raise ValueError("configured fake action_type is not allowed by the prompt")
        if not isinstance(context, dict):
            raise ValueError("prompt must provide a context object")

        assessment = context.get("supervisor_assessment")
        if not isinstance(assessment, dict):
            raise ValueError("prompt context must include a Supervisor assessment")
        evidence = assessment.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError("prompt context must include Supervisor evidence")
        first_evidence = evidence[0]
        if not isinstance(first_evidence, dict):
            raise ValueError("Supervisor evidence entries must be JSON objects")
        evidence_ref = first_evidence.get("ref")
        if not isinstance(evidence_ref, str) or not evidence_ref:
            raise ValueError("first Supervisor evidence entry must have a reference")

        event = context.get("event")
        if not isinstance(event, dict):
            raise ValueError("prompt context must include an event object")
        transcript = event.get("transcript")
        stage = "post_reply" if isinstance(transcript, dict) else "initial"
        scripted = self._scripted_responses.get(stage, {})
        action_type = scripted.get("action_type", self._choose_stage_action(allowed_action_types, stage))
        rationale = scripted.get("rationale", self._default_rationale(transcript, stage))
        confidence = scripted.get("confidence", self._confidence)
        parameters = scripted.get("parameters", self._parameters)
        if not isinstance(parameters, dict):
            raise ValueError("scripted parameters must be a JSON object")
        if stage == "initial" and action_type == ActionType.ASK_RESIDENT.value:
            parameters = dict(parameters)
            parameters.setdefault("question_sv", "Är allt bra hemma?")

        evidence_refs = [evidence_ref]
        reply_evidence_ref = context.get("resident_reply_evidence_ref")
        if (
            prompt.get("reply_citation_required") is True
            and isinstance(reply_evidence_ref, str)
            and reply_evidence_ref not in evidence_refs
        ):
            evidence_refs.append(reply_evidence_ref)

        response = {
            "action_type": action_type,
            "rationale": rationale,
            "confidence": confidence,
            "parameters": parameters,
            "evidence_refs": scripted.get("evidence_refs", evidence_refs),
        }
        return json.dumps(response, ensure_ascii=False, separators=(",", ":"), allow_nan=False)

    def _choose_stage_action(self, allowed_action_types: list[str], stage: str) -> str:
        if self._action_type is not None:
            return self._choose_action(allowed_action_types)
        preferred = (
            ActionType.ASK_RESIDENT.value
            if stage == "initial"
            else ActionType.REMOTE_FOLLOWUP.value
        )
        if preferred in allowed_action_types:
            return preferred
        return self._choose_action(allowed_action_types)

    def _default_rationale(self, transcript: object, stage: str) -> str:
        if stage == "post_reply" and isinstance(transcript, dict):
            text = transcript.get("text")
            if isinstance(text, str) and text.strip():
                quote = text.strip()[:80]
                return f'Residenten svarade "{quote}", vilket motiverar uppföljning.'
        return self._rationale

    def _choose_action(self, allowed_action_types: list[str]) -> str:
        if self._action_type is not None:
            if self._action_type not in allowed_action_types:
                raise ValueError("configured fake action_type is not allowed by the prompt")
            return self._action_type
        if "request_human_review" in allowed_action_types:
            return "request_human_review"
        return allowed_action_types[0]


class DeterministicFakeAssessmentChatModel:
    """Return a deterministic baseline comparison using the Supervisor prompt data."""

    def __init__(self, numeric_tolerances: Optional[Mapping[str, float]] = None) -> None:
        self._numeric_tolerances = dict(numeric_tolerances or {})

    def complete(self, *, system_prompt: str, user_prompt: str) -> str:
        try:
            context = json.loads(user_prompt)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("fake assessment model expected a JSON user prompt") from error
        if not isinstance(context, dict):
            raise ValueError("fake assessment model expected a JSON object prompt")
        observations = context.get("observations")
        baseline = context.get("baseline")
        comparable_fields = context.get("comparable_fields")
        prompt_tolerances = context.get("numeric_tolerances")
        if not isinstance(observations, dict) or not isinstance(baseline, dict):
            raise ValueError("Supervisor prompt must include observations and baseline objects")
        if not isinstance(comparable_fields, list) or not comparable_fields:
            raise ValueError("Supervisor prompt must include comparable fields")
        if not isinstance(prompt_tolerances, dict):
            raise ValueError("Supervisor prompt must include numeric tolerances")

        changed_fields = []
        for field in comparable_fields:
            if field not in observations or field not in baseline:
                raise ValueError("comparable fields must exist in observations and baseline")
            observed = observations[field]
            expected = baseline[field]
            tolerance = prompt_tolerances.get(field, self._numeric_tolerances.get(field, 0.0))
            if type(observed) in (int, float) and type(expected) in (int, float):
                differs = abs(observed - expected) > tolerance
            else:
                differs = type(observed) is not type(expected) or observed != expected
            if differs:
                changed_fields.append(field)

        observations = context.get("observations", {})
        temperature = observations.get("temperature_c")
        humidity = observations.get("humidity_pct")
        door_state = observations.get("door_state")
        motion_detected = observations.get("motion_detected")
        critical = (
            isinstance(temperature, (int, float)) and not isinstance(temperature, bool)
            and (temperature <= -10.0 or temperature >= 45.0)
        ) or (
            isinstance(humidity, (int, float)) and not isinstance(humidity, bool)
            and (humidity <= 5.0 or humidity >= 95.0)
        ) or (door_state == "open" and motion_detected is False)

        payload = {
            "is_anomaly": bool(changed_fields),
            "severity": "critical" if critical and changed_fields else "moderate" if changed_fields else "info",
            "summary": (
                f"{len(changed_fields)} sensor field(s) differ from baseline."
                if changed_fields
                else "Comparable sensor readings match the baseline."
            ),
            "confidence": 1.0,
            "evidence_fields": changed_fields or comparable_fields[:1],
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)