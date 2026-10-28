"""Studio's private-test MCP entrypoint.

Only the connection status tool is enabled by default. Account-backed actions
require a real OAuth authorization flow before a public HTTPS deployment.
"""

import os
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP
from app import app

mcp = FastMCP(
    "Studio",
    instructions=(
        "Test the Studio connection. The connection-status tool is a transport "
        "check only. Do not claim to render, schedule externally, or publish videos."
    ),
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
    host="0.0.0.0",
)


@mcp.tool()
def studio_connection_status() -> dict[str, str]:
    """Check whether Studio's MCP transport and tools are working."""
    return {
        "status": "connected",
        "service": "Studio",
        "mode": "connectivity-only",
        "video_generation": "not_connected",
        "postiz_publishing": "not_connected",
    }


# This legacy path is deliberately off by default. It exposes tokens as tool
# arguments and MUST NOT be used for a public/internet-accessible MCP service.
# Replace it with OAuth-based request authentication for real user accounts.
if os.getenv("STUDIO_UNSAFE_LEGACY_TOKEN_TOOLS") == "1":
    from app import Campaign, Session, Token, Video, digest

    def legacy_owner(token: str) -> str:
        with Session() as db:
            row = db.get(Token, digest(token))
            if not row:
                raise ValueError("Invalid Studio token")
            return row.user_id

    @mcp.tool()
    def list_campaigns(token: str) -> list[dict]:
        """Legacy, local-test-only listing of campaigns owned by the bearer-token user."""
        user_id = legacy_owner(token)
        with Session() as db:
            return [
                {
                    "id": c.id,
                    "name": c.name,
                    "topic": c.topic,
                    "audience": c.audience,
                    "frequency": c.frequency,
                }
                for c in db.query(Campaign).filter_by(user_id=user_id)
            ]

    @mcp.tool()
    def list_videos(token: str) -> list[dict]:
        """Legacy, local-test-only listing of Studio video records."""
        user_id = legacy_owner(token)
        with Session() as db:
            return [
                {"id": v.id, "title": v.title, "status": v.status, "post_url": v.post_url}
                for v in db.query(Video).filter_by(user_id=user_id).limit(100)
            ]

    @mcp.tool()
    def create_draft(
        token: str,
        title: str,
        script: str,
        style: str = "cutout",
        length: int = 45,
    ) -> dict:
        """Legacy, local-test-only creation of a draft record (not a rendered video)."""
        user_id = legacy_owner(token)
        if style not in {"cutout", "duotone"} or not 15 <= length <= 90:
            raise ValueError("Invalid style or length")
        with Session() as db:
            video = Video(
                user_id=user_id,
                title=title[:160],
                script=script,
                style=style,
                length=length,
                status="draft",
                channels="[]",
            )
            db.add(video)
            db.commit()
            return {"id": video.id, "status": "draft"}


# A mounted ASGI app does not run its own lifespan. The parent FastAPI app must
# start the MCP session manager, otherwise tool discovery can fail with HTTP 500.
mcp_asgi_app = mcp.streamable_http_app()
original_lifespan = app.router.lifespan_context


@asynccontextmanager
async def combined_lifespan(app_instance):
    async with original_lifespan(app_instance):
        async with mcp.session_manager.run():
            yield


app.router.lifespan_context = combined_lifespan
app.mount("/mcp", mcp_asgi_app)
