# AURA

Multi-agent home-care pipeline fed by sensor events over LavinMQ.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests
```

## Sensor input UI (local testing)

A Streamlit page for entering sensor values and publishing them to LavinMQ,
instead of using the terminal. It runs on your own machine only:
**http://localhost:8501**.

```bash
# Which broker? Default is a local LavinMQ (amqp://guest:guest@localhost:5672/).
# For a shared/hosted broker, put LAVINMQ_URL='amqps://...' in .env (git-ignored) and load it:
set -a; source .env; set +a

# terminal 1: the page
.venv/bin/streamlit run aura/sensors/ui.py

# terminal 2: the consumer that runs the agents on each published reading
.venv/bin/python -m aura.streaming.demo consume
```

The consumer needs `LAVINMQ_URL` set, even for the local broker:
`export LAVINMQ_URL=amqp://guest:guest@localhost:5672/`.

The page publishes to exchange `aura.events`, routing key `sensor.event`, and queue
`aura.sensor.events`. The **Broker** section on the page shows which broker it is using.
