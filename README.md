# AURA Home-Care Demo

AURA demonstrates a sensor-to-care workflow for home-care events. Sensor events are quality-checked and validated, assessed by the Supervisor, debounced when non-critical, and sent to initial Triage. Triage may dismiss them, propose a direct action, or request a resident check-in. Guardian gates actions; approved non-dismissal actions are dispatched to the dashboard.

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
Sensor quality -> Supervisor -> Triage (initial)
                                                | dismiss / direct action
                                                | ask resident -> reply -> Triage (post-reply)
                                                v
                                        Guardian -> Dispatcher
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
AURA_MODE=live
AURA_LLM=openai
OPENAI_API_KEY=<api-key>
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=<model_name>
OPENAI_REASONING_MODEL=<optional-supervisor-model>
AURA_HOST=127.0.0.1
AURA_PORT=8000
AURA_DEVICE_HOST=127.0.0.1
AURA_DEVICE_PORT=8501
AURA_API_URL=http://127.0.0.1:8000
AURA_SUBJECT_ID=synthetic-resident-1
AURA_REPLY_TIMEOUT_CRITICAL_S=60
AURA_REPLY_TIMEOUT_MODERATE_S=60
```

## Model Modes

- `AURA_MODE=demo` selects deterministic fake Supervisor/Triage models, even if OpenAI credentials are present. This setting does not disable configured broker or speech integrations.
- `AURA_MODE=live` selects the real OpenAI-compatible Chat Completions adapter and requires `OPENAI_API_KEY`. `AURA_LLM` may be omitted or set to `openai`; `none` is rejected in live mode. Missing credentials stop composition/startup rather than selecting fake models.
- With `AURA_MODE` unset, legacy `AURA_LLM=openai` still selects OpenAI; `none` or an unset provider selects fake models. Unknown mode/provider names are errors.
- `OPENAI_MODEL` defaults to `gpt-4o-mini`; `OPENAI_BASE_URL` defaults to `https://api.openai.com/v1`. Remote endpoints require HTTPS; local HTTP endpoints are supported for development. Optional `OPENAI_REASONING_MODEL` selects a separate model for Supervisor assessments only; when unset, Supervisor uses `OPENAI_MODEL`. Triage always uses `OPENAI_MODEL`.

Model mode selects LLMs only. The dashboard now waits for actual resident audio and uses backend KB-Whisper for transcription; it does not generate synthetic replies. Tickets and pending conversations still remain in memory, and this is not certified for care use. The separate `python -m aura.streaming.demo run` and CLI `consume` paths retain scripted voice for offline/demo use. Guardian remains deterministic. Supervisor model failures require review; Triage/transport failures never fall back to fake proposals. Provider errors are sanitized, LLM requests have a 30-second timeout, and malformed, refused, empty, or truncated model output is rejected. Agent-specific schemas and evidence checks still apply, including at most one unknown-evidence correction attempt in Triage.

For repeatable offline model tests, explicitly override both settings (shell environment overrides `.env`):

```bash
AURA_MODE=demo AURA_LLM=none venv/bin/python -m unittest discover -s tests
```

The tests mock HTTP speech/model providers; no live broker, TTS, STT, or LLM services are needed for the test suite.

Browser speaker logic has an additional offline test suite (Node.js required):

```bash
node --test tests/speaker_playback.test.cjs
```

### Alibaba Cloud Qwen

The existing OpenAI-compatible adapter also supports Alibaba Cloud Model Studio.
Both Supervisor and Triage use the same endpoint, API key, and model; no separate
provider adapter or `OPENAI_REASONING_MODEL` setting is needed.

In your local `.env`, replace the corresponding settings below, preserving other
settings. Enter your Alibaba Cloud key locally; do not commit it or paste it into
chat. Despite their names, the `OPENAI_*` settings configure the compatible
provider, not necessarily OpenAI:

```dotenv
AURA_MODE=live
AURA_LLM=openai
OPENAI_API_KEY=<your-alibaba-cloud-model-studio-api-key>
OPENAI_BASE_URL=https://<workspace-id>.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1
OPENAI_MODEL=qwen3.7-plus
```

Copy the **OpenAI Compatible Endpoint** from your Model Studio workspace, without
adding `/chat/completions`; the adapter appends that path. The example is for
Singapore: use an API key created in the same region and confirm that the model
is enabled in that workspace. Do not set `AURA_LLM=qwen`; `openai` selects the
compatible protocol. Existing shell environment variables override `.env`;
remove stale exported values or update them before restarting the dashboard.

Alibaba documents JSON-object output support for the Qwen3.7-Plus series.
AURA requests that format and still validates the JSON, agent schemas, and
evidence references. Provider errors remain fail-closed, and Guardian remains
the deterministic authorization gate. No model output alone authorizes dispatch.
See [OpenAI compatibility](https://www.alibabacloud.com/help/en/model-studio/compatibility-of-openai-with-dashscope)
and [structured output](https://www.alibabacloud.com/help/en/model-studio/qwen-structured-output).

Before sending requests, enable **Free Quota Only** in Model Studio if you do not
want paid usage. Free quota is limited and expires; check your console for the
model's actual balance and expiry. Model access being enabled does not mean this
billing safeguard is enabled. See [free-quota rules](https://www.alibabacloud.com/help/en/model-studio/new-free-quota).
Use only synthetic data until resident-data handling and regional requirements
have been reviewed.

After configuring the key and endpoint, you can explicitly run this live-model
smoke test from the repository root:

```bash
AURA_MODE=live AURA_LLM=openai venv/bin/python -m aura.streaming.demo run --scenario critical-cold
```

This sends synthetic sensor data to the configured LLM, consumes quota, and uses
scripted resident voice and an in-memory dashboard. It does not contact the broker
or the running dashboard, and it does not test real microphone/STT delivery.
Inspect the output: Supervisor should have `requires_review: false` and supported
evidence; Triage should be non-null and cite that evidence; Guardian must approve
before any dispatcher receipt appears. A completed command alone does not prove
success: Supervisor failures can return a review-required result. Model decisions
can vary, so do not treat a particular dispatch outcome as guaranteed. If a call
fails or output is rejected, keep validation enabled and investigate the sanitized
error before changing provider parameters. Restart the dashboard after changing
its configuration.

## Run the Dashboard

```bash
source venv/bin/activate
python app.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). `AURA_HOST` and `AURA_PORT` in `.env` configure the bind address and port. The server runs one Uvicorn worker so the consumer and UI share the same in-memory state. With a configured broker, it consumes sensor events automatically. The Agent Workflow shows the latest event, current readings, the latest Guardian-approved critical event, and details for each agent. Approved remote follow-ups, home/urgent visits, and maintenance requests appear as typed Caregiver Tickets with action-specific priorities. Dismissals, relative notifications, and planned-visit notes are separate in-memory action records available at `GET /api/action-records`; they do not send real notifications or update an external visit schedule.

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

In-memory debounce requires two consecutive readings with the same non-critical anomaly fields for that resident before starting resident follow-up. A normal, unassessable, or sensor-quality-fault reading resets the streak. Critical assessments bypass debounce. Once a streak has been forwarded, matching readings are suppressed until normal readings reset it. Streak state is lost when the process restarts; this is a demo noise filter, not a clinical urgency policy.

The dashboard quality monitor routes missing required fields and out-of-range temperature/humidity readings to a Guardian-approved `maintenance_ticket`, not a resident follow-up. Rate-of-change and repeated-identical-value checks are opt-in because their limits depend on the sensor specifications and reporting interval. Configure them in `.env` as JSON objects, for example:

```dotenv
AURA_SENSOR_MAX_CHANGE_PER_SECOND='{"temperature_c": 0.05}'
AURA_SENSOR_REPEATED_VALUE_LIMITS='{"temperature_c": 12}'
```

Use manufacturer-approved values and the sensor's actual cadence; these examples are illustrative, not recommended thresholds. The repeat rule creates one finding when the configured consecutive-reading count is first reached. Its per-process history is in memory and resets when the dashboard restarts.

The default input queue is `aura.sensor.events.v2`; settings can be overridden with `LAVINMQ_QUEUE`, `LAVINMQ_EXCHANGE`, and `LAVINMQ_ROUTING_KEY`. Failed or invalid messages are nacked without requeue and routed to the durable dead-letter queue `<main-queue>.dead` through `aura.events.dead`. If the broker already has the older `aura.sensor.events` queue, the versioned queue avoids redeclaring it with incompatible DLX arguments; messages already in the old queue are not moved automatically.

## Guardian Actions and Review Safety

Guardian deterministically gates the action ladder: `dismiss`, `ask_resident`, `remote_followup`, `home_visit`, `urgent_visit`, `notify_relative`, `attach_to_planned_visit`, and `maintenance_ticket`. `ask_resident` requires an initial-pass proposal at moderate or critical severity. Remote follow-up and home visit require configured confidence thresholds. Urgent visit requires critical severity or a critical sensor reason; otherwise Guardian holds it with a `home_visit` recommendation for human review. A critical dismissal requires a post-reply proposal and a non-empty transcript; Guardian checks that a reply exists but does not interpret its content. No-reply escalation is separately Guardian-gated.

Relative notification and planned-visit attachment are demo records only. They are held unless matching in-memory resident profile data is supplied to `CriticalRiskPolicy`; the demo does not send messages or integrate with a real schedule. Approved dismissals, notifications, and planned-visit notes are visible at `GET /api/action-records` and do not create caregiver tickets. Maintenance and care follow-up actions appear as typed tickets only after Guardian approves them.

After a ticket is resolved, a caseworker may label it `false_alarm`, `justified`, or `sensor_fault`. Three false-alarm reports for the same resident/field create a baseline suggestion at `GET /api/baseline-suggestions`. Suggestions never change a baseline automatically; a human must explicitly apply them. `GET /api/metrics` reports session counts and an estimated visit-avoidance metric. These metrics, records, feedback, suggestions, and baselines are in memory and reset when the backend restarts.

This prototype is **not an approved municipal care policy**, clinical system, or emergency-response service. The demo thresholds, severity/timeouts, escalation rules, and synthetic resident profile are not validated by a municipality. Review them with the selected municipality and responsible care/safety staff before any operational use.

## Real Resident Reply Demo (No Database)

Configure `ORANGE_API_KEY` on the dashboard backend in `.env`. The Whisper endpoint accepts `KB_WHISPER_URL` (preferred) or the legacy `BSER_URL` alias; `KB_WHISPER_MODEL` optionally overrides the default model `KBLab/kb-whisper-large`. Use `AURA_MODE=live` with OpenAI credentials for real Supervisor and Triage calls. Use nonpersonal demo audio until access controls and cloud-provider privacy requirements are reviewed.

Start the dashboard as above, then start the separate device in another terminal:

```bash
venv/bin/python run_resident_device.py
```

Open [http://127.0.0.1:8501](http://127.0.0.1:8501). `run_resident_device.py` loads `.env` before Streamlit starts, so `AURA_DEVICE_HOST`, `AURA_DEVICE_PORT`, `AURA_API_URL`, and `AURA_SUBJECT_ID` configure the device launch and polling target. The checked-in example values use local ports 8000 and 8501 and the synthetic demo resident. Other residents need explicitly configured baselines; they must not inherit the synthetic resident's baseline. Browser microphone capture requires localhost or HTTPS and microphone permission.

1. Publish the critical event above. Supervisor assesses it once; initial Triage decides whether to ask the resident, dismiss, or propose an action. An approved check-in appears as `awaiting_resident`; the dashboard shows the initial Triage proposal and Guardian decision. The resident device displays the server-computed remaining reply time and refreshes it during its three-second poll. The example `.env` sets both critical and moderate reply timeouts to 60 seconds; adjust `AURA_REPLY_TIMEOUT_CRITICAL_S` and `AURA_REPLY_TIMEOUT_MODERATE_S` locally as needed.
2. Press **Aktivera ljud** once in the resident device. It polls every three seconds for that resident's oldest pending prompt and attempts Swedish browser speech once per new prompt, without restarting on each poll. **Spela igen** replays; **Stoppa** cancels. The browser needs an installed Swedish voice and may require another click to permit playback. If unavailable, explicitly select **Google Translate (demo)** to synthesize and play an MP3 with gTTS. This unofficial Translate interface is not Google Cloud TTS; it sends the prompt text to Google. Browser voices may also use network processing depending on the platform. No paid TTS setup is required.
3. Record a Swedish reply and press **Skicka rostsvar** (Swedish label in the UI). The device uploads PCM WAV and the subject/event/conversation IDs. The backend transcribes once with KB-Whisper and measures acoustic features on that same recording; it does not infer a diagnosis.
4. Post-reply Triage uses the actual transcript, acoustic features, and original Supervisor assessment. Explicit distress phrases such as “mår inte bra”, “inte bra hemma”, “har ont”, or “ramlat” are escalated to an urgent-priority human-review ticket. This is a conservative phrase list, not general Swedish sentiment analysis; reassuring negations such as “jag har inte ont” do not match. Guardian approves from the service-added reason code, stage, and transcript presence without interpreting transcript content. This creates a demo ticket only; it does not dispatch a real visit or diagnose. Other replies follow the normal post-reply Triage proposal. An approved dismissal is recorded in event history without dispatch. Event history updates the original event rather than adding a second event.

Recordings must be non-silent uncompressed 16-bit PCM WAV (one or two channels), at most 120 seconds and 5 MiB. Missing STT configuration or provider failure is shown as an error, never a scripted fallback reply. Retry the same recording if processing fails; successful transcription is cached in memory for that conversation. Repeated identical replies return the same result/ticket without a second STT call. A different recording cannot replace an already-transcribed reply.

The resident device does not call KB-Whisper directly or receive its API key. It polls `GET /api/device/prompt?subject_id=<resident-id>` and posts multipart audio to `POST /api/device/respond` with fields `subject_id`, `event_id`, `conversation_id`, and `audio_file`. The backend calls `<KB_WHISPER_URL>/v1/audio/transcriptions` with the captured WAV, `language=sv`, `model=KBLab/kb-whisper-large` (unless overridden), and `response_format=json`. The response contains the actual transcript and `stt_source=orange-kb-whisper`; it is not replaced with canned text. The standalone `record_transcribe.py` workbench remains independent of this event-correlated flow.

When a resident does not reply before the severity-specific deadline, a background dashboard sweeper proposes `urgent_visit` for critical severity or `remote_followup` for moderate severity, with reason `no_resident_reply`. Guardian still authorizes the action, and Dispatcher creates the corresponding in-memory demo ticket only when approved. The escalation is idempotent within the process; pending events and escalation state are lost on restart. These timeout values and rules are demo workflow behavior, not approved Swedish care policy or an emergency-response service. Replies must match the resident, event, and conversation IDs; replies arriving after escalation are rejected.

Playback reports go to `POST /api/device/playback` with the same three IDs, provider (`browser_speech` or `gtts`), and status (`started`, `completed`, `stopped`, `failed`, or `blocked`). Only a browser playback-completion callback marks the prompt's speaker receipt `delivered=true`; synthesis, Stop, and failures do not. This is device-reported completion, not proof of working speakers or that the resident heard the message. Playback reports never run Triage or create a ticket. Keep the resident page open; closing it stops playback. There is no device authentication in this demo, so receipts are not independently trusted delivery evidence.

All pending events, cached transcripts, history and tickets are lost on restart. The broker acknowledges intake once it is staged in memory, not after the resident replies; restarting therefore cannot recover already-acknowledged pending events. Keep one backend process running throughout the demo. Raw audio is not retained in the service after the request; the speech provider receives it for transcription. Authentication/device authorization and production persistence are not implemented by this milestone.
