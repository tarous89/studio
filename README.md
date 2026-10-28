# Studio ✦
B2B video campaigns and publication management, with a glass-lens dashboard and a ChatGPT MCP endpoint.

**Status: implementation prototype, not a fully operational publishing product.** The React/FastAPI/Python source archive shared in the companion ChatGPT conversation contains an expanded draft of the app and render worker. This GitHub branch contains a standalone database-backed web prototype and MCP tools; deploy only for internal testing.

## Capabilities in this branch
- Login/registration, user-scoped campaign data, video draft library, internal scheduling records.
- Campaign controls for topics, audiences, channels, duration, cadence and style.
- Postiz API key encrypted at rest and a channel-discovery endpoint.
- MCP transport at `/mcp/`: the default tool is `studio_connection_status` (no account data).
- Legacy account tools are explicitly disabled by default; they require a future OAuth implementation before ChatGPT access.
- Render Blueprint for the app and PostgreSQL. Configure ENCRYPTION_KEY on Render.

## Not yet complete
- Actual media rendering, automatic generation, external Postiz video upload/publishing, imported post history and analytics.
- Durable cloud video storage, OAuth for ChatGPT, rate limiting, production migrations.
- Full reusable ports of the two uploaded rendering workflows.

## MCP private smoke test

The server runs through `uvicorn run:app` (not `uvicorn app:app`). Its mounted Streamable HTTP endpoint is `/mcp/`, including the trailing slash. By default it exposes only the harmless `studio_connection_status` tool for connection testing. Do not set `STUDIO_UNSAFE_LEGACY_TOKEN_TOOLS=1` on an internet-facing service: those older tools accept account tokens as model-supplied arguments.

Before account-level tools are enabled in ChatGPT, implement OAuth 2.1 user authentication with protected-resource discovery and per-request authorization. Postiz keys and video rendering are not prerequisites for the transport smoke test. See `docs/MCP_STAGING.md`.

## Run
```sh
pip install -r requirements.txt
uvicorn run:app --reload
```

Generate a Fernet encryption key with `python -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())'`. Do not put it in GitHub.

## Render
The `render.yaml` Blueprint provisions a Frankfurt web service and PostgreSQL database. Do not deploy production traffic or enable public registrations before auth hardening.

## Licenses
This repository does not vendor Postiz's AGPL-covered source. It only integrates with Postiz over HTTP. Uploaded reference workflows and unverified external assets are not included.
