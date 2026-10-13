import unittest
from types import SimpleNamespace
from unittest.mock import patch
from starlette.testclient import TestClient

from aura.dashboard import server as dashboard_server
from aura.dashboard.server import app
from aura.messaging.codec import EventMessageCodec
from aura.streaming.demo import make_sensor_event


class DashboardServerTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_dashboard_lifecycle_starts_and_stops_broker_consumer_thread(self):
        consumer = unittest.mock.Mock()
        thread = unittest.mock.Mock()
        settings = SimpleNamespace()

        with patch.object(dashboard_server.LavinMQSettings, "from_environment", return_value=settings), patch.object(
            dashboard_server, "PikaLavinMQConsumer", return_value=consumer
        ) as consumer_factory, patch.object(dashboard_server, "Thread", return_value=thread) as thread_factory:
            with TestClient(app):
                consumer_factory.assert_called_once()
                thread_factory.assert_called_once_with(
                    target=consumer.run_forever,
                    name="aura-lavinmq-consumer",
                    daemon=True,
                )
                thread.start.assert_called_once()

        consumer.stop.assert_called_once()
        thread.join.assert_called_once_with(timeout=5.0)

    def test_dashboard_launcher_uses_one_worker_process(self):
        with patch("uvicorn.run") as run:
            dashboard_server.run_server(host="127.0.0.1", port=8123)

        run.assert_called_once_with(
            "aura.dashboard.server:app",
            host="127.0.0.1",
            port=8123,
            reload=False,
            workers=1,
        )

    def test_status_endpoint(self):
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("broker_configured", data)
        self.assertIn("total_events_processed", data)
        self.assertIn("total_tickets", data)

    def test_simulate_normal_endpoint(self):
        response = self.client.post("/api/simulate", json={"scenario": "normal"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["result"]["outcome"], "no_action")
        self.assertEqual(data["result"]["sensor_data"]["observations"]["temperature_c"], 18.0)
        self.assertTrue(data["result"]["sensor_data"]["is_mock"])

    def test_consume_endpoint_runs_queued_event_into_dashboard_state(self):
        event = make_sensor_event("normal", event_id="dashboard-queue-event")

        class FakeChannel:
            def __init__(self):
                self.acknowledged = []

            def basic_get(self, *, queue, auto_ack):
                self.queue = queue
                self.auto_ack = auto_ack
                return SimpleNamespace(delivery_tag=7), None, EventMessageCodec.encode(event)

            def basic_ack(self, *, delivery_tag):
                self.acknowledged.append(delivery_tag)

        channel = FakeChannel()

        class FakeConnection:
            def channel(self):
                return channel

            def close(self):
                pass

        settings = SimpleNamespace(
            url="amqps://guest:guest@localhost/vhost",
            queue="aura.sensor.events",
        )
        with patch("aura.dashboard.server.LavinMQSettings.from_environment", return_value=settings), patch(
            "pika.BlockingConnection", return_value=FakeConnection()
        ):
            response = self.client.post("/api/consume")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["consumed_event_id"], event.event_id)
        self.assertEqual(payload["result"]["outcome"], "no_action")
        self.assertEqual(channel.acknowledged, [7])
        event_response = self.client.get("/api/events").json()["events"]
        matching_event = next(item for item in event_response if item["event_id"] == event.event_id)
        self.assertEqual(matching_event["sensor_data"]["source"], event.source)

    def test_simulate_critical_endpoint_creates_ticket(self):
        response = self.client.post("/api/simulate", json={"scenario": "critical-cold"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["result"]["outcome"], "dispatched")
        self.assertIsNotNone(data["result"]["dashboard_record_id"])

        # Check tickets endpoint
        tickets_resp = self.client.get("/api/tickets")
        self.assertEqual(tickets_resp.status_code, 200)
        tickets = tickets_resp.json()["tickets"]
        self.assertGreaterEqual(len(tickets), 1)

        # Test updating ticket status
        ticket_id = data["result"]["dashboard_record_id"]
        patch_resp = self.client.patch(
            f"/api/tickets/{ticket_id}",
            json={"status": "in_progress", "notes": "Contacted nurse on duty."},
        )
        self.assertEqual(patch_resp.status_code, 200)
        self.assertEqual(patch_resp.json()["meta"]["status"], "in_progress")
        self.assertEqual(patch_resp.json()["meta"]["notes"], "Contacted nurse on duty.")

    def test_device_trigger_prompt_and_tts(self):
        # Trigger prompt on device
        resp = self.client.post("/api/device/trigger-prompt", json={"scenario": "critical-cold"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertIn("prompt", data)
        self.assertEqual(data["prompt"]["status"], "pending_reply")

        # Get device prompt
        prompt_resp = self.client.get("/api/device/prompt")
        self.assertEqual(prompt_resp.status_code, 200)
        self.assertIsNotNone(prompt_resp.json()["prompt"])

        # Get TTS audio
        tts_resp = self.client.get("/api/device/tts", params={"text": "Hej, hur mår du?"})
        self.assertEqual(tts_resp.status_code, 200)
        self.assertEqual(tts_resp.headers["content-type"], "audio/mpeg")
        self.assertGreater(len(tts_resp.content), 100)

        # Post response from device
        respond_resp = self.client.post(
            "/api/device/respond",
            data={"text_response": "Jag ramlade i köket och fryser."},
        )
        self.assertEqual(respond_resp.status_code, 200)
        res_data = respond_resp.json()
        self.assertTrue(res_data["success"])
        self.assertEqual(res_data["transcript"], "Jag ramlade i köket och fryser.")
        self.assertEqual(res_data["outcome"], "dispatched")
        self.assertIsNotNone(res_data["ticket_id"])

    def test_static_html_served_from_dist(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("<!doctype html>", response.text.lower())


if __name__ == "__main__":
    unittest.main()
