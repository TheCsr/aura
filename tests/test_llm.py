import json
import os
import unittest
from unittest.mock import Mock, patch

import requests

from aura.llm.openai_chat import OpenAIChatModel, llm_from_environment


class LlmModeTests(unittest.TestCase):
    def select(self, environment):
        with patch.dict(os.environ, environment, clear=True), patch("aura.llm.openai_chat._load_env"):
            return llm_from_environment()

    def test_demo_never_selects_external_model(self):
        self.assertIsNone(self.select({"AURA_MODE": "demo", "AURA_LLM": "openai"}))

    def test_live_requires_credentials(self):
        with self.assertRaisesRegex(ValueError, "OPENAI_API_KEY"):
            self.select({"AURA_MODE": "live"})

    def test_live_selects_real_adapter(self):
        self.assertIsInstance(self.select({"AURA_MODE": "live", "OPENAI_API_KEY": "test-key"}), OpenAIChatModel)

    def test_invalid_or_conflicting_settings_fail(self):
        for environment in (
            {"AURA_MODE": "typo"},
            {"AURA_MODE": "live", "AURA_LLM": "none"},
            {"AURA_LLM": "typo"},
        ):
            with self.subTest(environment=environment), self.assertRaises(ValueError):
                self.select(environment)

    def test_legacy_settings_remain_supported(self):
        self.assertIsNone(self.select({"AURA_LLM": "none"}))
        self.assertIsInstance(self.select({"AURA_LLM": "openai", "OPENAI_API_KEY": "test-key"}), OpenAIChatModel)

    def test_compositions_cannot_fall_back_when_live_credentials_are_missing(self):
        from aura.composition import create_demo_system
        from aura.streaming.composition import create_streaming_demo

        with patch.dict(os.environ, {"AURA_MODE": "live"}, clear=True), patch("aura.llm.openai_chat._load_env"):
            for factory in (create_demo_system, create_streaming_demo):
                with self.subTest(factory=factory.__name__), self.assertRaisesRegex(ValueError, "OPENAI_API_KEY"):
                    factory()


class QwenCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.base_url = (
            "https://ws-test.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
        )
        environment = patch.dict(
            os.environ,
            {
                "AURA_MODE": "live",
                "AURA_LLM": "openai",
                "OPENAI_API_KEY": "test-alibaba-key",
                "OPENAI_BASE_URL": self.base_url + "/",
                "OPENAI_MODEL": "qwen3.7-plus",
            },
            clear=True,
        )
        environment.start()
        self.addCleanup(environment.stop)
        load_env = patch("aura.llm.openai_chat._load_env")
        load_env.start()
        self.addCleanup(load_env.stop)

    @patch("aura.llm.openai_chat.requests.post")
    def test_environment_selects_qwen_compatible_request(self, post):
        content = json.dumps({"summary": "Synthetic sensor analysis."})
        post.return_value.json.return_value = {
            "choices": [{"finish_reason": "stop", "message": {"content": content}}]
        }
        model = llm_from_environment()
        self.assertIsInstance(model, OpenAIChatModel)

        self.assertEqual(
            model.complete(
                system_prompt="Return a JSON object.", user_prompt="Synthetic data."
            ),
            content,
        )

        post.assert_called_once_with(
            self.base_url + "/chat/completions",
            headers={"Authorization": "Bearer test-alibaba-key"},
            json={
                "model": "qwen3.7-plus",
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": "Return a JSON object."},
                    {"role": "user", "content": "Synthetic data."},
                ],
            },
            timeout=30.0,
        )

    @patch("aura.llm.openai_chat.requests.post")
    def test_qwen_invalid_json_is_rejected(self, post):
        model = llm_from_environment()
        self.assertIsInstance(model, OpenAIChatModel)
        for content in ("[]", "not JSON", "```json\n{}\n```"):
            post.return_value.json.return_value = {
                "choices": [{"finish_reason": "stop", "message": {"content": content}}]
            }
            with self.subTest(content=content), self.assertRaisesRegex(
                RuntimeError, "invalid"
            ):
                model.complete(
                    system_prompt="Return a JSON object.", user_prompt="Synthetic data."
                )

    @patch("aura.llm.openai_chat.requests.post")
    def test_streaming_uses_qwen_for_supervisor_and_triage(self, post):
        from aura.graph import WorkflowOutcome
        from aura.streaming.composition import create_streaming_demo
        from aura.streaming.demo import make_sensor_event

        assessment = {
            "is_anomaly": True,
            "severity": "critical",
            "summary": "Synthetic temperature is far below its baseline.",
            "confidence": 0.95,
            "evidence_fields": ["temperature_c"],
        }
        proposal = {
            "action_type": "request_human_review",
            "rationale": "Review the synthetic temperature evidence.",
            "confidence": 0.95,
            "parameters": {},
            "evidence_refs": ["supervisor.evidence.0"],
        }
        post.return_value.json.side_effect = [
            {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}]}
            for value in (assessment, proposal)
        ]
        demo = create_streaming_demo()
        event = make_sensor_event("critical-cold", event_id="qwen-synthetic-test")

        result = demo.service.handle(event)

        self.assertEqual(result.outcome, WorkflowOutcome.DISPATCHED)
        self.assertFalse(result.assessment.requires_review)
        self.assertEqual(result.assessment.evidence[0].source, "supervisor_llm")
        self.assertEqual(result.proposal.event_id, event.event_id)
        self.assertEqual(result.proposal.evidence_refs, ("supervisor.evidence.0",))
        self.assertEqual(result.decision.status.value, "approve")
        self.assertEqual(len(demo.dashboard.records), 1)
        self.assertEqual(post.call_count, 2)
        for call in post.call_args_list:
            self.assertEqual(call.args, (self.base_url + "/chat/completions",))
            self.assertEqual(call.kwargs["json"]["model"], "qwen3.7-plus")
            self.assertEqual(
                call.kwargs["json"]["response_format"], {"type": "json_object"}
            )
            self.assertIn(
                "json", call.kwargs["json"]["messages"][0]["content"].lower()
            )

    @patch("aura.llm.openai_chat.requests.post")
    def test_optional_reasoning_model_is_used_only_for_supervisor(self, post):
        from aura.graph import WorkflowOutcome
        from aura.streaming.composition import create_streaming_demo
        from aura.streaming.demo import make_sensor_event

        assessment = {
            "is_anomaly": True,
            "severity": "critical",
            "summary": "Synthetic temperature differs from baseline.",
            "confidence": 0.95,
            "evidence_fields": ["temperature_c"],
        }
        proposal = {
            "action_type": "request_human_review",
            "rationale": "Review the synthetic temperature evidence.",
            "confidence": 0.95,
            "parameters": {},
            "evidence_refs": ["supervisor.evidence.0"],
        }
        post.return_value.json.side_effect = [
            {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}]}
            for value in (assessment, proposal)
        ]
        with patch.dict(os.environ, {"OPENAI_REASONING_MODEL": "qwen3-reasoner"}):
            demo = create_streaming_demo()
            result = demo.service.handle(
                make_sensor_event("critical-cold", event_id="qwen-reasoning-test")
            )

        self.assertEqual(result.outcome, WorkflowOutcome.DISPATCHED)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(
            [call.kwargs["json"]["model"] for call in post.call_args_list],
            ["qwen3-reasoner", "qwen3.7-plus"],
        )


class OpenAIChatModelTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.model = OpenAIChatModel(api_key="test-key")

    @patch("aura.llm.openai_chat.requests.post")
    def test_request_returns_valid_json_content(self, post):
        content = json.dumps({"summary": "test"})
        post.return_value.json.return_value = {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}
        self.assertEqual(self.model.complete(system_prompt="system", user_prompt="data"), content)
        self.assertEqual(post.call_args.kwargs["timeout"], 30.0)
        self.assertEqual(post.call_args.kwargs["json"]["response_format"], {"type": "json_object"})

    @patch("aura.llm.openai_chat.requests.post")
    def test_provider_errors_do_not_expose_details(self, post):
        for error in (requests.Timeout("private-url test-key"), requests.HTTPError("private-url test-key")):
            post.side_effect = error
            with self.subTest(error=type(error).__name__), self.assertRaises(RuntimeError) as raised:
                self.model.complete(system_prompt="system", user_prompt="data")
            self.assertNotIn("test-key", str(raised.exception))
            self.assertNotIn("private-url", str(raised.exception))

    @patch("aura.llm.openai_chat.requests.post")
    def test_invalid_responses_are_rejected(self, post):
        for payload in ({}, {"choices": []}, *[
            {"choices": [{"message": {"content": content}}]}
            for content in (None, "", "not json", "[]")
        ], {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]},
            {"choices": [{"message": {"content": "{}", "refusal": "refused"}}]}):
            post.return_value = Mock()
            post.return_value.json.return_value = payload
            with self.subTest(payload=payload), self.assertRaisesRegex(RuntimeError, "invalid"):
                self.model.complete(system_prompt="system", user_prompt="data")

    def test_invalid_configuration_is_rejected(self):
        for kwargs in ({"timeout": 0}, {"timeout": float("nan")}, {"base_url": "http://remote.example/v1"}, {"base_url": "https://user:password@remote.example"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                OpenAIChatModel(api_key="test-key", **kwargs)