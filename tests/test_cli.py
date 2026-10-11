import contextlib
import io
import json
import unittest

from aura.cli import main


class CliTests(unittest.TestCase):
    def test_scenarios_print_each_agent_and_expected_ticket(self):
        cases = (
            ("normal", "no_action", None),
            ("anomaly", "dispatched", "mock-ticket-anomaly"),
            ("missing-baseline", "dispatched", "mock-ticket-missing-baseline"),
        )

        for scenario, outcome, ticket_id in cases:
            with self.subTest(scenario=scenario):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    exit_code = main([scenario])

                report = json.loads(output.getvalue())
                self.assertEqual(exit_code, 0)
                self.assertEqual(report["outcome"], outcome)
                self.assertEqual(report["mock_ticket_id"], ticket_id)
                self.assertEqual(
                    set(report["agents"]),
                    {"supervisor", "triage", "guardian", "dispatcher"},
                )
                self.assertIsNotNone(report["agents"]["supervisor"])

    def test_normal_scenario_marks_later_agents_as_not_run(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            main(["normal"])

        report = json.loads(output.getvalue())
        self.assertIsNone(report["agents"]["triage"])
        self.assertIsNone(report["agents"]["guardian"])
        self.assertIsNone(report["agents"]["dispatcher"])

    def test_default_scenario_is_anomaly(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            main([])

        report = json.loads(output.getvalue())
        self.assertEqual(report["event"]["event_id"], "cli-anomaly")
        self.assertEqual(report["outcome"], "dispatched")


if __name__ == "__main__":
    unittest.main()