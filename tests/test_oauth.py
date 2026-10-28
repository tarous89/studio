"""OAuth route and MCP protection smoke tests, run in a fresh Python interpreter."""
import os
os.environ["STUDIO_SMOKE_ONLY"] = "0"
os.environ["PUBLIC_BASE_URL"] = "https://studio-web-hz4l.onrender.com"
from fastapi.testclient import TestClient
from run import app


def test_oauth_metadata_and_auth_wall():
    with TestClient(app) as client:
        meta=client.get("/.well-known/oauth-authorization-server/mcp")
        assert meta.status_code==200,meta.text
        assert meta.json()["issuer"].endswith("/mcp")
        resource=client.get("/.well-known/oauth-protected-resource/mcp/")
        assert resource.status_code==200,resource.text
        assert resource.json()["authorization_servers"]
        response=client.post("/mcp/",json={
            "jsonrpc":"2.0","id":1,"method":"initialize",
            "params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"test","version":"0.1"}}
        },headers={"Accept":"application/json, text/event-stream"})
        assert response.status_code in (401,403),response.text
