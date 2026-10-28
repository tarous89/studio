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

        # DCR must be supported for ChatGPT's private OAuth client.
        registered=client.post("/mcp/register",json={
            "client_name":"Studio CI",
            "redirect_uris":["https://chatgpt.com/connector/callback"],
            "token_endpoint_auth_method":"none",
            "grant_types":["authorization_code","refresh_token"],
            "response_types":["code"],
            "scope":"studio",
        })
        assert registered.status_code in (200,201),registered.text
        client_id=registered.json()["client_id"]
        assert client_id
        from hashlib import sha256
        import base64
        verifier="testing-verifier-for-studio-auth-012345678901234567890"
        challenge=base64.urlsafe_b64encode(sha256(verifier.encode()).digest()).decode().rstrip("=")
        auth=client.get("/mcp/authorize",params={
            "client_id":client_id,
            "response_type":"code",
            "redirect_uri":"https://chatgpt.com/connector/callback",
            "code_challenge":challenge,
            "code_challenge_method":"S256",
            "scope":"studio",
            "state":"test-state",
            "resource":"https://studio-web-hz4l.onrender.com/mcp/",
        },follow_redirects=False)
        assert auth.status_code in (302,303,307),auth.text
        assert "/mcp/consent" in auth.headers["location"]
