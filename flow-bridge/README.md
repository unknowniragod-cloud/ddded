# Flow Bridge prototype

Authenticated queue API intended to connect a browser Userscript to a cloud queue.

## Endpoints
- GET /api/health — health check (public)
- POST /api/jobs — enqueue a text prompt (Bearer token)
- GET /api/next — claim next queued job (Bearer token)
- POST /api/jobs/{id}/status — update job state (Bearer token)
- GET /api/jobs — list recent jobs (Bearer token)

## Important limitations
This server does not control Google Flow, generate media server-side, upload reference images, or download results. A compatible Userscript in an authenticated Google Flow tab is still required. Browser execution, Google account access, quotas, and any CAPTCHA remain under Google's control.

Never put Google cookies, session tokens, or the bridge secret in this repository. Set FLOW_BRIDGE_TOKEN as a private environment variable in the hosting provider. Use HTTPS only.

## Deployment
The included Render Blueprint describes a web service and persistent disk. Review current hosting costs and disk requirements before deploying; a persistent disk may require a paid plan. Do not assume a free instance provides durable SQLite storage.
