"""Full Studio preview flow: registration -> draft -> render -> downloadable MP4.

No YouTube credentials are used. This test runs against an isolated CI SQLite
database and leaves the public channel untouched.
"""
import os
import shutil
from fastapi.testclient import TestClient
from run import app
from studio_pipeline import one_job

def test_authenticated_video_render_to_preview(monkeypatch):
    monkeypatch.setenv("STUDIO_SMOKE_ONLY", "0")
    monkeypatch.setenv("STUDIO_SIGNUP_CODE", "studio-ci-private-code")
    assert shutil.which("ffmpeg"), "ffmpeg must be installed to test real MP4 rendering"
    client = TestClient(app)
    register = client.post("/api/auth/register", json={
        "email": "preview-ci@example.invalid",
        "password": "studio-ci-password-2026",
        "invite_code": "studio-ci-private-code",
    })
    assert register.status_code == 200, register.text
    token = register.json()["token"]
    headers = {"Authorization": "Bearer " + token}
    draft = client.post("/api/videos", json={
        "title": "A better clinical trial starts with clear questions",
        "script": "What is the most important decision in your next clinical trial? "
                  "Start with one useful question. Evaluate the assumptions. "
                  "Then choose the evidence that will change your decision.",
        "style": "duotone",
        "length": 10,
        "channels": ["youtube"],
    }, headers=headers)
    assert draft.status_code == 200, draft.text
    video_id = draft.json()["id"]

    queued = client.post("/api/videos/" + video_id + "/render", headers=headers)
    assert queued.status_code == 200, queued.text
    assert queued.json()["status"] == "queued"

    assert one_job(), "Background worker did not find the queued render job"

    records = client.get("/api/videos", headers=headers)
    assert records.status_code == 200, records.text
    video = next(v for v in records.json() if v["id"] == video_id)
    assert video["status"] == "ready", video

    preview = client.get("/api/videos/" + video_id + "/media", headers=headers)
    assert preview.status_code == 200, preview.text[:200]
    assert preview.headers["content-type"].startswith("video/mp4")
    assert b"ftyp" in preview.content[:32]
    assert len(preview.content) > 1024

    unauthenticated = client.get("/api/videos/" + video_id + "/media")
    assert unauthenticated.status_code == 401

    approve = client.post("/api/videos/" + video_id + "/approve", headers=headers)
    assert approve.status_code == 200, approve.text
    assert approve.json()["status"] == "approved"
    # Publishing is deliberately NOT tested without Google authorization.
