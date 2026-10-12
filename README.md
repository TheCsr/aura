# AURA

Multi-agent home-care pipeline fed by sensor events over LavinMQ.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests
```

## Sensor input UI (local testing)

One command, no other terminals:

```bash
./start-sensor-ui.sh
```

The first run creates `.venv` and installs requirements. Every run opens
**http://localhost:8501** (this machine only). The page:

- reads `LAVINMQ_URL` from `.env` (git-ignored). Without it, it uses a local LavinMQ
  (`amqp://guest:guest@localhost:5672/`). For a shared broker, put one line in `.env`:
  `LAVINMQ_URL='amqps://user:pass@host.lmq.cloudamqp.com/vhost'`
- starts the agents' consumer automatically. Stop and start it from the sidebar to watch
  readings wait in the queue.
- **🧪 Run automatic test** publishes every preset through LavinMQ and checks each outcome
  (normal → no action; door open, critical cold, hot and humid → ticket created; 500 °C → blocked).
- lets you enter a reading by hand and shows the agents' results live.

The page publishes to exchange `aura.events`, routing key `sensor.event`, and queue
`aura.sensor.events`. Don't also run `python -m aura.streaming.demo consume` against the
same broker: two consumers on one queue split the messages.
