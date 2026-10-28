import os
import subprocess
from pathlib import Path

os.environ["STUDIO_SMOKE_ONLY"]="1"

from fastapi.testclient import TestClient
from app import app, Video
from studio_pipeline import render_video


def test_health_and_public_guard():
    # A second FastMCP lifespan cannot start in the same pytest interpreter.
    # Health checks do not need to start another MCP session.
    client=TestClient(app)
    response=client.get("/health")
    assert response.status_code==200
    assert response.json()["status"]=="ok"
    assert client.get("/api/videos").status_code in (401,403)


def test_mp4_rendering(tmp_path):
    if subprocess.run(["which","ffmpeg"],capture_output=True).returncode!=0:
        return
    v=Video(user_id="test",title="Biotech Trial Planning",
            script="Plan your trial clearly. Review each assumption. Ask the right questions.",
            style="duotone",length=10)
    mp4=render_video(v)
    assert mp4[:12].find(b"ftyp")>=0
    assert len(mp4)>1000
