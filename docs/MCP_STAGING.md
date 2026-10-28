# Private ChatGPT MCP test for Studio

This staged connection exercise is not a full video-generation or Postiz publishing test.

## State of the code

- Source: `feat/studio-foundation` branch, GitHub PR #1 (not merged).
- Entry point: `uvicorn run:app --host 0.0.0.0 --port 10000` (or Dockerfile).
- Transport: Streamable HTTP. Because FastMCP is mounted at `/mcp` with `streamable_http_path="/"`, use the **trailing slash** `https://<host>/mcp/`.
- Test tool: `studio_connection_status` returns a harmless, static transport status.
- Normal account tools are intentionally **not enabled** in a publicly reachable test.

## Infrastructure prerequisites

1. Deploy the **feature branch**, not `main` (main currently does not contain app source).
2. Create an isolated staging Render web service and staging database in the intended Render workspace. Do not reuse existing Trialy/TrialAgents databases.
3. If using the Render Blueprint, verify its actual plan names and validate it in Render before applying; no deployment has been performed yet.
4. Set `DATABASE_URL` from staging Postgres. Set `SESSION_SECRET` and `ENCRYPTION_KEY` using Render secrets; never commit them. For the MCP smoke test, Postiz credentials are unnecessary.
5. Do not enable `STUDIO_UNSAFE_LEGACY_TOKEN_TOOLS` on a public Render service. Keep the connection private in ChatGPT and restrict who can access staging services.
6. Check `GET /health` responds with JSON `{"status":"ok"}`. Confirm `/mcp/` handles an MCP initialize request; a simple browser GET is not a valid handshake.

## Verify with an MCP client

Use MCP Inspector (`npx @modelcontextprotocol/inspector@latest`) to connect to the public HTTPS `/mcp/` endpoint or an authorized Secure MCP Tunnel. Confirm the server responds to tools/list with `studio_connection_status` and that calling it returns `status: connected`.

## Connect in ChatGPT

1. In ChatGPT web open **Plugins**.
2. Select **+** > **Add custom MCP server**.
3. Name it `Studio (Private Test)`, enter the exact MCP HTTPS URL ending in `/mcp/` (or select a configured Secure MCP Tunnel).
4. For this static connectivity-only test choose the authentication mode appropriate to the accessible server; do not present an account bearer token as a tool argument. Review the risk warning and select **Create as a plugin**.
5. Install the plugin, start a new chat, select it with `@`, and ask: **Check the Studio MCP connection status.**

## Follow-up to test actual accounts

Implement OAuth 2.1/PCKE with discovery, protected-resource metadata, and per-request user authorization before enabling account-backed read/write tools on a public endpoint. Read-only and write operations must be scoped to the authorized user's Studio account. Next enable `list_campaigns`, `list_videos`, and `create_draft` without model-supplied credentials, then verify them with a dedicated test account.

For technical transport failures, inspect Render logs, FastMCP hostname validation, /mcp/ slash handling, and the parent-app lifespan.

Official docs: https://developers.openai.com/plugins/deploy/connect-chatgpt
