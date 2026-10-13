# AURA Home-Care Demo

AURA demonstrates a sensor-to-care workflow for home-care events. Sensor events are validated, assessed by the Supervisor, optionally followed up through resident voice, proposed by Triage, gated by Guardian, and dispatched to the dashboard when approved.

This is a demo, not a clinical or production care system. Dashboard records and event history are in memory and are cleared when the server restarts. Sensor baselines, voice/STT components, and dashboard storage are demo-backed unless configured otherwise.

## Architecture

```text
Sensor terminal / device
        |
        v
LavinMQ durable queue
        |
        v
Dashboard's background consumer
        |
        v
Supervisor -> Voice follow-up -> Triage -> Guardian -> Dispatcher
                                                    |
                                                    v
                                      In-memory caregiver ticket
```

The dashboard server starts one background consumer during FastAPI startup when `LAVINMQ_URL` is configured. It processes events into the same in-memory service used by the dashboard UI. The React UI refreshes event and ticket data automatically. Do not start `python -m aura.streaming.demo consume` at the same time; that separate consumer competes for queue messages and writes results to its own in-memory demo store.

## Setup

Use a Python version supported by the project dependencies. The current workspace virtual environment uses Python 3.14. The Vite frontend requires Node.js 20.19+ or 22.12+.

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cd frontend
npm install
npm run build
cd ..
```

Configure `.env` in the repository root. Do not commit real credentials:

```dotenv
LAVINMQ_URL=amqps://<user>:<password>@<host>/<vhost>
AURA_LLM=openai
OPENAI_API_KEY=<api-key>
OPENAI_MODEL=<model_name>
```

## Run the Dashboard

```bash
source venv/bin/activate
python app.py --host 0.0.0.0 --port 8000
```

Open [http://localhost:8000](http://localhost:8000). The server runs one Uvicorn worker so the consumer and UI share the same in-memory state. With a configured broker, it consumes sensor events automatically. The Agent Workflow shows the latest event, current readings, the latest Guardian-approved critical event, and details for each agent. Approved actions appear in Caregiver Tickets.

## Publish Sensor Readings

With the dashboard running, publish readings from another terminal:

```bash
source venv/bin/activate
python -m aura.streaming.demo publish \
  --temperature-c -20 \
  --humidity-pct 45 \
  --door-state open \
  --motion-detected false \
  --subject-id synthetic-resident-1 \
  --source living-room-sensor
```

The command validates the values and publishes one event to LavinMQ. The dashboard's background consumer then runs it through the agents; no consume button or separate CLI consumer is needed. Use `python -m aura.streaming.demo publish --help` for all options. Valid sensor fields are:

- `temperature_c`: finite number from -80 to 100
- `humidity_pct`: finite number from 0 to 100
- `door_state`: `open`, `closed`, or `unknown`
- `motion_detected`: `true` or `false`

An event ID is generated automatically; `--event-id` can set one explicitly. `--subject-id` defaults to `synthetic-resident-1`, which is the only subject with the default demo baseline. Configure a subject-specific baseline before publishing other subject IDs if you want normal baseline comparison.

The default input queue is `aura.sensor.events.v2`; settings can be overridden with `LAVINMQ_QUEUE`, `LAVINMQ_EXCHANGE`, and `LAVINMQ_ROUTING_KEY`. Failed or invalid messages are nacked without requeue and routed to the durable dead-letter queue `<main-queue>.dead` through `aura.events.dead`. If the broker already has the older `aura.sensor.events` queue, the versioned queue avoids redeclaring it with incompatible DLX arguments; messages already in the old queue are not moved automatically.

Guardian only dispatches a ticket when its configured policy approves the proposal. A baseline anomaly can be held if no critical-risk rule is met.
```
