# Flow Bridge — iPhone Safari cloud queue

Flow Bridge is a small authenticated FastAPI + SQLite queue. The cloud stores jobs and coordinates a worker; **Google Flow generation still runs in your already signed-in browser tab**. The server does not call private Google endpoints and never receives Google cookies or session credentials.

## What is implemented

- Persistent SQLite jobs with UUIDs, timestamps, mode/model/aspect-ratio, reference metadata and result metadata.
- Atomic SQLite job claiming using `BEGIN IMMEDIATE`, worker IDs and expiring leases.
- Heartbeats, lease recovery, configurable retry count and exponential retry delay.
- Queued/running/completed/failed/cancelled states and progress.
- Job history, per-job status, cancellation and manual retry.
- Queue pause/resume/stop endpoint.
- Bearer-token authentication, owner scoping, input validation and basic per-IP rate limiting.
- CORS allowlist and Render persistent disk configuration.
- iPhone Safari userscript with a compact control panel, cloud submit/poll, worker heartbeat, pause/resume/stop, settings persistence and activity log.

## Important Flow / iPhone limitations

The userscript only runs while the Flow page is open and the browser permits it to run. iOS may suspend background tabs; closing Safari stops the worker. Keep the Flow tab in the foreground while processing.

The script can locate a visible prompt composer and insert text. It **does not click Generate**: userscript-generated clicks are synthetic and are not trusted browser input. Review the prompt, choose the Flow model/aspect ratio/duration, complete any Google challenge yourself, and tap Flow's own Generate button. Then press **Mark completed** in the panel. If generation fails, the worker lease will expire and the server will retry within its configured limit; use the job history to inspect status.

Reference metadata can be stored with a job, but this release does not upload binary images to cloud storage or automatically attach them to Flow. The current iPhone connector intentionally reports this limitation rather than claiming that an image has been attached. Frames, Ingredients, Scene Chain, and automatic download detection need additional verified selectors and browser-permitted file handling before being advertised as automated.

G-Labs Studio's current public documentation describes its own desktop application and local webhook integration, including batch queues, image references, frames/ingredients, model settings, progress and result polling. Those local webhook endpoints are **not** Google Flow endpoints and are not used here. See [G-Labs Studio README](https://github.com/duckmartians/G-Labs-Studio/blob/main/docs/readme/README.en.md) and [Webhook Integration](https://github.com/duckmartians/G-Labs-Studio/blob/main/WEBHOOK_INTEGRATION.en.md).

## Render deployment

1. Deploy this repository through Render Blueprint using `render.yaml`.
2. The Blueprint uses a **Starter** web service and a 1 GB persistent disk mounted at `/var/data`. Render persistent disks are a paid feature; the Free plan cannot provide durable SQLite storage.
3. Render generates `FLOW_BRIDGE_TOKEN`. Copy its value from the service's Environment page; do not put it in GitHub or share it.
4. Wait for `GET /api/health` to return `ok: true`.
5. Keep `FLOW_BRIDGE_DB=/var/data/flow_bridge.sqlite3`. If deploying manually, attach a persistent disk at `/var/data` and set this variable.
6. CORS defaults to `https://labs.google,https://flow.google.com`. If your Flow page uses another exact origin, set `FLOW_BRIDGE_ALLOWED_ORIGINS` to a comma-separated allowlist. Do not use `*`.

The Dockerfile runs Uvicorn on Render's `PORT`. SQLite WAL mode is enabled. A persistent disk is mounted to one service instance; do not scale this SQLite deployment horizontally.

## iPhone installation

1. Install a Safari userscript manager that supports standard userscripts and persistent userscript storage.
2. Import `flow-bridge/flow-bridge-iphone.user.js` from this repository.
3. Open the userscript settings panel on your iPhone and enter:
   - **Bridge API URL**: your Render service URL, e.g. `https://flow-bridge.onrender.com` (no trailing slash required).
   - **Bearer token**: the Render `FLOW_BRIDGE_TOKEN` value.
4. Open [Google Flow](https://labs.google/fx/tools/flow) in Safari and sign in normally.
5. Open the Flow project, then tap **Start worker** in the Flow Bridge panel.
6. Submit prompts from the panel or another client using `POST /api/jobs`. When a job is claimed, its prompt is inserted into the visible Flow composer.
7. Review the Flow settings, manually tap Generate, and tap **Mark completed** after checking the result.

Settings are stored in the userscript manager's local storage on the iPhone. Do not export settings containing the Bearer token into public files. Use HTTPS.

## API

All endpoints except `GET /api/health` require `Authorization: Bearer <FLOW_BRIDGE_TOKEN>`. Worker endpoints also use `X-Worker-ID: <stable-worker-id>`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Public health check |
| POST | `/api/jobs` | Submit a job |
| GET | `/api/next` | Atomically claim the next job (worker header required) |
| GET | `/api/jobs` | List history; optional `limit` and `status` |
| GET | `/api/jobs/{id}` | Get one job |
| POST | `/api/jobs/{id}/heartbeat` | Extend lease and update progress (worker header required) |
| POST | `/api/jobs/{id}/status` | Report running/completed/failed/cancelled/queued (worker header required for claimed jobs) |
| POST | `/api/jobs/{id}/retry` | Manually requeue failed/cancelled job |
| POST | `/api/jobs/{id}/cancel` | Cancel queued/running job |
| POST | `/api/queue/control` | Global pause/resume/stop |

### Submit example

```bash
curl -X POST "$FLOW_BRIDGE_URL/api/jobs" \
  -H "Authorization: Bearer $FLOW_BRIDGE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"A cinematic mountain lake at sunrise","mode":"image","aspect_ratio":"16:9","references":[],"options":{"resolution":"2K"}}'
```

Supported `mode`: `auto`, `image`, `video`, `text_to_image`, `image_to_video`, `frames_to_video`, `ingredients_to_video`. Mode is metadata for the browser operator; the server does not enforce a particular Flow UI mode.

Reference entries are metadata objects with optional `name`, `mime_type`, `url`, and `kind`. This version does not upload, proxy, or persist binary reference data. Do not send Google cookies or private browser URLs.

### Claim example

```bash
curl "$FLOW_BRIDGE_URL/api/next" \
  -H "Authorization: Bearer $FLOW_BRIDGE_TOKEN" \
  -H "X-Worker-ID: iphone-safari-01"
```

### Heartbeat / result example

```bash
curl -X POST "$FLOW_BRIDGE_URL/api/jobs/JOB_ID/heartbeat" \
  -H "Authorization: Bearer $FLOW_BRIDGE_TOKEN" \
  -H "X-Worker-ID: iphone-safari-01" -H "Content-Type: application/json" \
  -d '{"progress":40,"message":"Generation in progress"}'

curl -X POST "$FLOW_BRIDGE_URL/api/jobs/JOB_ID/status" \
  -H "Authorization: Bearer $FLOW_BRIDGE_TOKEN" \
  -H "X-Worker-ID: iphone-safari-01" -H "Content-Type: application/json" \
  -d '{"status":"completed","progress":100,"result":{"url":"https://example.invalid/result"}}'
```

The result URL above is illustrative only; use a real result URL obtained from the browser. Do not mark a job completed until the output has been verified. Queue pause/stop prevents new claims; it does not terminate a generation already running in Flow. Cancel that job explicitly with \`POST /api/jobs/{id}/cancel\`.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `FLOW_BRIDGE_TOKEN` | unset | Required shared Bearer secret |
| `FLOW_BRIDGE_DB` | `/var/data/flow_bridge.sqlite3` | SQLite database path |
| `FLOW_BRIDGE_ALLOWED_ORIGINS` | Google Flow origins | Comma-separated CORS allowlist |
| `FLOW_BRIDGE_LEASE_SECONDS` | `180` | Worker lease duration |
| `FLOW_BRIDGE_MAX_RETRIES` | `2` | Default retries per job |
| `FLOW_BRIDGE_RETRY_BASE_SECONDS` | `20` | Exponential retry base |
| `FLOW_BRIDGE_RATE_LIMIT_PER_MINUTE` | `60` | Per-IP in-process API request limit |

Rate limiting is process-local and is not a substitute for an edge/WAF limit on a horizontally scaled deployment. The shared Bearer token is a single-tenant credential: anyone holding it can submit and manage jobs. Rotate it immediately if exposed.

## Development and tests

```bash
cd flow-bridge
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export FLOW_BRIDGE_TOKEN='replace-with-a-long-random-secret'
export FLOW_BRIDGE_DB='./flow_bridge.sqlite3'
uvicorn server:app --reload
```

Then visit `/docs` for the generated OpenAPI documentation. Test health, authenticated job submission, claiming with a worker ID, heartbeat, status update, cancellation, retry and lease recovery. Run one worker instance against the SQLite database.

## Security

- Never commit the Bearer token or Google session material.
- Do not export cookies, CAPTCHA tokens, local storage credentials, or browser profiles.
- Google authentication and CAPTCHA remain in the user's normal browser session.
- No undocumented Flow API calls, CAPTCHA bypass, or trusted-click simulation are implemented.
