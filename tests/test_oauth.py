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

        # Complete the Studio OAuth consent and token exchange, not just discovery.
        signup=client.post("/api/auth/register",json={
            "email":"oauth-chatgpt-ci@example.invalid",
            "password":"studio-auth-test-password-2026"
        })
        assert signup.status_code==200,signup.text
        from urllib.parse import urlparse,parse_qs
        request_url=urlparse(auth.headers["location"])
        request_id=parse_qs(request_url.query)["request"][0]
        consent=client.post("/mcp/consent",data={
            "request":request_id,
            "email":"oauth-chatgpt-ci@example.invalid",
            "password":"studio-auth-test-password-2026",
        },follow_redirects=False)
        assert consent.status_code in (302,303,307),consent.text
        redirect=urlparse(consent.headers["location"])
        assert redirect.netloc=="chatgpt.com"
        auth_code=parse_qs(redirect.query)["code"][0]
        exchange=client.post("/mcp/token",data={
            "grant_type":"authorization_code",
            "code":auth_code,
            "client_id":client_id,
            "code_verifier":verifier,
            "redirect_uri":"https://chatgpt.com/connector/callback",
            "resource":"https://studio-web-hz4l.onrender.com/mcp/",
        })
        assert exchange.status_code==200,exchange.text
        bearer=exchange.json()["access_token"]
        rpc_headers={
            "Authorization":"Bearer "+bearer,
            "Accept":"application/json, text/event-stream",
            "Content-Type":"application/json"
        }
        initialized=client.post("/mcp/",headers=rpc_headers,json={
            "jsonrpc":"2.0","id":11,"method":"initialize",
            "params":{"protocolVersion":"2025-06-18",
                      "capabilities":{},
                      "clientInfo":{"name":"ChatGPT CI","version":"0.2"}}
        })
        assert initialized.status_code==200,initialized.text
        tools_list=client.post("/mcp/",headers=rpc_headers,json={
            "jsonrpc":"2.0","id":12,"method":"tools/list","params":{}
        })
        assert tools_list.status_code==200,tools_list.text
        names={tool["name"] for tool in tools_list.json()["result"]["tools"]}
        assert {"create_and_render_video","get_video_generation_status"}.issubset(names)

        created=client.post("/mcp/",headers=rpc_headers,json={
            "jsonrpc":"2.0","id":13,"method":"tools/call",
            "params":{"name":"create_and_render_video","arguments":{
                "title":"Clinical trial decision framework",
                "narration":"Start with the key question. Review the evidence. Plan your next decision.",
                "scenes":[
                  {"text":"Start with the question","layout":"headline","accent":"lilac"},
                  {"text":"Review the evidence","layout":"quote","accent":"mint"},
                  {"text":"Plan the next step","layout":"headline","accent":"coral"}
                ],
                "style":"duotone","duration_seconds":15
            }}
        })
        assert created.status_code==200,created.text
        result=created.json()["result"]
        assert not result.get("isError"),result
        import json
        payload=result.get("structuredContent") or json.loads(result["content"][0]["text"])
        video_id=payload["video_id"]
        assert payload["status"]=="queued"
        from studio_pipeline import get_creative_plan
        plan=get_creative_plan(video_id)
        assert plan and plan["scenes"][1]["text"]=="Review the evidence"
