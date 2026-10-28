"""ChatGPT supplies the content; Studio only renders, previews and stores it."""
import os
from urllib.parse import urlparse
from fastapi.testclient import TestClient

from app import Session, Video
from run import app
from studio_pipeline import (
    create_chatgpt_video, create_preview_link, get_creative_plan,
    normalize_scenes, one_job
)


def test_chatgpt_directed_video_with_signed_preview(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", "ci-private-signing-key-not-a-youtube-secret")
    client = TestClient(app)

    with Session() as db:
        video = create_chatgpt_video(
            db, "studio-chatgpt-ci-user",
            title="Three decisions before Phase 1",
            narration=("Before Phase 1, identify the decision you need to make. "
                       "Define the evidence required. Plan your next step clearly."),
            scenes=[
                {"text": "What is the decision?", "layout": "headline", "accent": "lilac"},
                {"text": "Identify the evidence; Check the assumptions; Define success", "layout": "list", "accent": "mint"},
                {"text": "What's the next step?", "layout": "quote", "accent": "coral"},
            ],
            style="duotone", duration_seconds=15,
            youtube_title="Phase 1: three decisions",
            youtube_description="A practical decision framework for biotech teams.",
        )
        video_id = video.id
    plan = get_creative_plan(video_id)
    assert [x["layout"] for x in plan["scenes"]] == ["headline", "list", "quote"]
    assert plan["youtube_title"] == "Phase 1: three decisions"
    assert plan["youtube_description"].startswith("A practical decision")

    for _ in range(8):
        with Session() as db:
            status = db.get(Video, video_id).status
        if status == "ready":
            break
        assert one_job(), "No rendering job found"
    with Session() as db:
        video = db.get(Video, video_id)
        assert video.status == "ready", video.status
        signed_url = create_preview_link(video)
        assert signed_url.startswith("https://")
        assert db.get(Video, video_id).post_url is None

    url = urlparse(signed_url)
    playable = client.get(url.path + "?" + url.query)
    assert playable.status_code == 200, playable.text[:200]
    assert playable.headers["content-type"].startswith("video/mp4")
    assert b"ftyp" in playable.content[:32]

    invalid = client.get(url.path + "?e=9999999999&s=invalid")
    assert invalid.status_code == 404

    try:
        normalize_scenes([{"text": "unsupported", "layout": "3d", "accent": "lilac"}] * 2)
    except ValueError:
        pass
    else:
        raise AssertionError("Unsupported layouts should not be silently ignored")


def test_signup_without_invitation(monkeypatch):
    monkeypatch.setenv("STUDIO_SMOKE_ONLY", "0")
    client = TestClient(app)
    response = client.post("/api/auth/register", json={
        "email": "studio-no-invite-test@example.invalid",
        "password": "studio-no-invite-strong-password"
    })
    assert response.status_code == 200, response.text
    assert response.json()["token"]



def test_chatgpt_video_mcp_tool_schema():
    import asyncio
    from plugin import mcp
    tools = asyncio.run(mcp.list_tools())
    by_name = {tool.name: tool for tool in tools}
    assert "create_and_render_video" in by_name
    assert "get_video_generation_status" in by_name
    schema = by_name["create_and_render_video"].inputSchema
    assert {"narration", "scenes", "title"}.issubset(schema["properties"])
    assert {"narration", "scenes", "title"}.issubset(set(schema.get("required", [])))
