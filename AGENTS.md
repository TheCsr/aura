# AURA Repository Guidance

## Understand the system before editing

- Read the relevant implementation and nearby tests before changing behavior. Prefer the existing ports, contracts, adapters, and composition roots over adding parallel implementations.
- The streaming event path is: sensor-quality checks -> `SensorEventValidator` -> Supervisor deterministic baseline comparison -> reasoning model only for deviations -> in-memory noncritical debounce -> initial Triage -> deterministic Guardian -> dismissal, approved direct action, or resident check-in -> correlated audio/STT -> post-reply Triage -> Guardian -> approved Dispatcher action.
- The real-time dashboard path is owned by `aura/dashboard/server.py`. Its FastAPI lifespan starts one LavinMQ consumer thread and one reply-deadline sweeper thread; `run_server()` uses one Uvicorn worker so the consumer, sweeper, and UI share the same in-memory state.
- Dashboard sensor intake uses `StreamingCareService.start()` for the initial Triage/Guardian decision. For a resident check-in, `resume_audio()` transcribes the correlated recording once and invokes post-reply Triage with the saved original Supervisor assessment; never rerun Supervisor or fabricate a resident reply. Critical and moderate no-reply deadlines default to 120 and 600 seconds (`AURA_REPLY_TIMEOUT_CRITICAL_S`, `AURA_REPLY_TIMEOUT_MODERATE_S`). The sweeper submits `urgent_visit` or `remote_followup` with reason `no_resident_reply` through Guardian and Dispatcher once. Pending conversations and acknowledged broker intake are not recoverable after a process restart.
- Do not run `python -m aura.streaming.demo consume` alongside the dashboard server for the same queue. They compete for messages and have separate in-memory results.
- Terminal sensor publishing is `python -m aura.streaming.demo publish` with explicit sensor readings. The separate `run` command is a synthetic offline demo, not a hardware connection.

## Safety and behavior boundaries

- Guardian is the deterministic authorization gate. Do not dispatch an action unless Guardian approves it. Triage proposes actions; it does not authorize them.
- Guardian's action ladder supports dismissal, resident check-in, remote follow-up, home visit, urgent visit, relative notification, planned-visit note, and sensor maintenance. Critical dismissal requires a post-reply proposal and a real non-empty transcript; Guardian checks stage, transcript presence, severity, and deterministic reason codes, never transcript text. Relative/planned-visit actions require explicit in-memory demo profile data; no real contact or scheduling integration exists.
- Explicit post-reply distress phrases are converted by the streaming service into a `resident_reply_distress` reason code and an urgent-priority human-review ticket. Guardian checks that reason code, the post-reply stage, and transcript presence; it does not inspect transcript text. The phrase list is not general sentiment analysis and does not constitute approved clinical or municipal policy.
- Noncritical deviations must persist across two readings by default before Triage is called; critical assessments bypass debounce. Debounce state is in memory. Optional jump/repeated-value sensor-quality limits must come from sensor specifications, not invented thresholds.
- This prototype is not an approved municipality, clinical, emergency-response, or care policy. Severity thresholds, timeout values, and demo baselines are not validated municipal procedures; obtain local care and policy approval before operational use.
- Preserve event IDs, resident IDs, Supervisor evidence references, and voice conversation correlation across the workflow. Invalid evidence references must remain fail-closed; the one bounded Triage repair attempt does not permit invented evidence.
- Do not describe acoustic features as medical assessments or use them to infer diagnoses.
- The default streaming baseline is in-memory and applies to `synthetic-resident-1`. Real residents need explicit subject-specific baseline configuration; do not silently use another resident's baseline.
- Dashboard event history, ticket metadata, dismissal audits, action records, metrics, caseworker feedback, suggested baselines, debounce, and escalation state are in memory and are lost when the process restarts. Suggested baselines are never applied without explicit human action. Do not present this storage as production persistence.

## Real and demo integrations

- `AURA_MODE=demo` forces fake chat models; `AURA_MODE=live` requires the OpenAI-compatible adapter and credentials, rejecting `AURA_LLM=none`. Without a mode, legacy `AURA_LLM=openai` selects the compatible adapter and `none` selects fakes. Unknown settings are errors. `OPENAI_REASONING_MODEL` optionally selects the Supervisor model only; Triage uses `OPENAI_MODEL`. `OPENAI_BASE_URL` and `OPENAI_API_KEY` are shared by both. Mode controls LLM selection; dashboard audio uses backend KB-Whisper regardless of model mode. `KB_WHISPER_URL` is preferred; legacy `BSER_URL` is accepted as its fallback. `AURA_REPLY_TIMEOUT_CRITICAL_S` and `AURA_REPLY_TIMEOUT_MODERATE_S` default to 120 and 600 seconds. Optional `AURA_SENSOR_MAX_CHANGE_PER_SECOND` and `AURA_SENSOR_REPEATED_VALUE_LIMITS` are JSON objects keyed by sensor field; configure them only from device specifications and sampling cadence. Never print or commit API keys or broker URLs/passwords. Do not inspect `.env` values in command output.
- The LavinMQ adapter is a real Pika AMQP adapter. Its default input queue is `aura.sensor.events.v2`; failed messages are nacked without requeue and should route through the configured durable dead-letter exchange/queue. A legacy queue may require an explicit migration; do not change queue topology without considering existing broker declarations and messages.
- The resident device is a separate Streamlit app in `resident_device_app.py`. It polls subject-specific pending prompts and uploads PCM WAV with subject/event/conversation IDs. Only the backend transcribes with KB-Whisper; never transcribe twice or replace failed STT with canned text. Scripted CLI/offline demos remain separate. gTTS playback uses an unofficial Google Translate interface, not Google Cloud TTS; do not claim synthesis proves speaker delivery.
- Keep synthetic data clearly identified as demo data. Do not add placeholder sensor values that look like incoming readings.

## Tests and validation

- Run Python tests without external LLM calls:
  `AURA_MODE=demo AURA_LLM=none venv/bin/python -m unittest discover -s tests`
- Run focused tests for the changed subsystem first, such as `tests.test_messaging`, `tests.test_dashboard_server`, `tests.test_streaming_demo`, or `tests.test_triage`.
- Build the React dashboard from `frontend/` with `npm run build`. Vite requires Node.js 20.19+ or 22.12+.
- Avoid live broker or LLM calls in automated tests. Mock transport boundaries and assert event handling, ack/nack behavior, correlation, and dispatch outcomes.
- Update the root README when launch commands, environment variables, queue names, or user-facing workflow changes.
