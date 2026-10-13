# AURA Repository Guidance

## Understand the system before editing

- Read the relevant implementation and nearby tests before changing behavior. Prefer the existing ports, contracts, adapters, and composition roots over adding parallel implementations.
- The main event path is: sensor event -> `SensorEventValidator` -> Supervisor -> optional resident voice follow-up -> Triage proposal -> deterministic Guardian decision -> Dispatcher -> dashboard action.
- The real-time dashboard path is owned by `aura/dashboard/server.py`. Its FastAPI lifespan starts one LavinMQ consumer thread; `run_server()` uses one Uvicorn worker so the consumer and UI share the same in-memory state.
- Do not run `python -m aura.streaming.demo consume` alongside the dashboard server for the same queue. They compete for messages and have separate in-memory results.
- Terminal sensor publishing is `python -m aura.streaming.demo publish` with explicit sensor readings. The separate `run` command is a synthetic offline demo, not a hardware connection.

## Safety and behavior boundaries

- Guardian is the deterministic authorization gate. Do not dispatch an action unless Guardian approves it. Triage proposes actions; it does not authorize them.
- Preserve event IDs, resident IDs, Supervisor evidence references, and voice conversation correlation across the workflow. Invalid evidence references must remain fail-closed; the one bounded Triage repair attempt does not permit invented evidence.
- Do not describe acoustic features as medical assessments or use them to infer diagnoses.
- The default streaming baseline is in-memory and applies to `synthetic-resident-1`. Real residents need explicit subject-specific baseline configuration; do not silently use another resident's baseline.
- Dashboard event history, ticket metadata, and mock dashboard records are in memory and are lost when the process restarts. Do not present this storage as production persistence.

## Real and demo integrations

- Set `AURA_LLM=openai` to select the OpenAI Chat Completions adapter; otherwise compositions use deterministic fake chat models. Never print or commit API keys or broker URLs/passwords. Do not inspect `.env` values in command output.
- The LavinMQ adapter is a real Pika AMQP adapter. Its default input queue is `aura.sensor.events.v2`; failed messages are nacked without requeue and should route through the configured durable dead-letter exchange/queue. A legacy queue may require an explicit migration; do not change queue topology without considering existing broker declarations and messages.
- The resident device is a separate Streamlit app in `resident_device_app.py`. Its browser microphone and KB-Whisper integration depend on configuration; voice and dashboard components elsewhere may remain fake/demo adapters.
- Keep synthetic data clearly identified as demo data. Do not add placeholder sensor values that look like incoming readings.

## Tests and validation

- Run Python tests without external LLM calls:
  `AURA_LLM=none venv/bin/python -m unittest discover -s tests`
- Run focused tests for the changed subsystem first, such as `tests.test_messaging`, `tests.test_dashboard_server`, `tests.test_streaming_demo`, or `tests.test_triage`.
- Build the React dashboard from `frontend/` with `npm run build`. Vite requires Node.js 20.19+ or 22.12+.
- Avoid live broker or LLM calls in automated tests. Mock transport boundaries and assert event handling, ack/nack behavior, correlation, and dispatch outcomes.
- Update the root README when launch commands, environment variables, queue names, or user-facing workflow changes.
