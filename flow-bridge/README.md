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

The current Render Blueprint targets the Free web-service plan for testing. Free services spin down after 15 minutes without inbound traffic, may take about a minute to wake, and have an ephemeral filesystem. The SQLite queue can be lost on restart, spin-down, or redeploy. This is a test setup, not durable storage.

Never put Google cookies, session tokens, or the bridge secret in this repository. Render generates FLOW_BRIDGE_TOKEN as a private environment variable. Use HTTPS only.

## Deployment
Open the Render Blueprint flow for this repository and create the service using the Free plan. After deployment, test GET /api/health before connecting the Userscript.
