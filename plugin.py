"""Studio MCP server: authenticated campaign, video, rendering and YouTube tools."""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, Field

from fastapi.responses import JSONResponse
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.fastmcp import FastMCP
from pydantic import AnyHttpUrl

from app import Campaign, Session, Video, app, now, video_data, campaign_data
from studio_oauth import BASE, ISSUER, MCP_RESOURCE, consent_get, consent_post, provider
from studio_pipeline import (
    VideoAsset, VideoJob, create_chatgpt_video, create_preview_link, get_creative_plan,
    queue_video, tick, youtube_configured, youtube_connected
)
from studio_routes import mount as mount_api

mount_api(app)
logger = logging.getLogger("studio")
SMOKE = os.getenv("STUDIO_SMOKE_ONLY","1")=="1"

if SMOKE:
    mcp = FastMCP("Studio",stateless_http=True,json_response=True,
                  streamable_http_path="/",host="0.0.0.0")
else:
    mcp = FastMCP(
        "Studio",
        instructions="ChatGPT is Studio's creative director. Write the complete narration and exact sequence of on-screen scenes yourself, then call create_and_render_video to send that plan to Studio's renderer. Do not call any app-side story generator as a substitute. The renderer supports headline, quote, stat and list layouts with coral, lilac, mint and gold accents. Check get_video_generation_status for an expiring MP4 preview URL. Never claim queued means complete. Upload to YouTube only after approval and an explicit user request, unless an opted-in auto-publishing campaign is already authorized.",
        stateless_http=True,json_response=True,streamable_http_path="/",host="0.0.0.0",
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(ISSUER),
            resource_server_url=AnyHttpUrl(MCP_RESOURCE),
            validate_token_resource=True,
            required_scopes=["studio"],
            client_registration_options=ClientRegistrationOptions(
                enabled=True,valid_scopes=["studio"],default_scopes=["studio"],
            ),
        ),
    )


def user_id():
    access = get_access_token()
    if not access or not access.subject:
        raise ValueError("Connect your Studio account using the OAuth sign-in flow")
    return access.subject


@mcp.tool()
def studio_connection_status()->dict:
    """Report the status of the Studio server and available integration configuration."""
    return {
        "service":"Studio",
        "connected":True,
        "mode":"connectivity-only" if SMOKE else "oauth-account-tools",
        "video_renderer":"configured",
        "storage":"postgres" if os.getenv("DATABASE_URL") else "temporary_sqlite",
        "creative_direction":"ChatGPT supplies narration, scenes and publishing metadata",
        "youtube_oauth_configured":youtube_configured(),
        "actual_youtube_channel_connected":"check_youtube_connection after login",
    }


class SceneInstruction(BaseModel):
    """A single renderable 9:16 scene authored by ChatGPT."""
    text: str = Field(min_length=1, max_length=180, description="Exact on-screen wording")
    layout: Literal["headline", "quote", "stat", "list"] = Field(
        default="headline", description="Card composition implemented by Studio"
    )
    accent: Literal["coral", "lilac", "mint", "gold"] = Field(
        default="lilac", description="Accent color implemented by Studio"
    )


if not SMOKE:
    @mcp.tool()
    def create_and_render_video(
        title: str,
        narration: str,
        scenes: list[SceneInstruction],
        style: Literal["cutout", "duotone"] = "duotone",
        duration_seconds: int = 30,
        campaign_id: str = "",
        youtube_title: str = "",
        youtube_description: str = "",
    ) -> dict:
        """Create and queue a real MP4 from ChatGPT-authored narration and 2–12 scenes.

        ChatGPT MUST write the spoken script, exact on-screen text, scene order,
        layout and accent. Studio renders the MP4 and persists the YouTube title
        and description. DOES NOT upload or publish the video. Needs Studio OAuth.
        """
        uid = user_id()
        with Session() as db:
            video = create_chatgpt_video(
                db, uid, title, narration,
                [scene.model_dump() for scene in scenes],
                style=style, duration_seconds=duration_seconds,
                campaign_id=campaign_id or None,
                youtube_title=youtube_title,
                youtube_description=youtube_description,
            )
            return {
                "video_id": video.id,
                "status": "queued",
                "rendered": False,
                "published": False,
                "source": "chatgpt_scene_plan",
                "message": "Studio has the exact script and scenes. Call get_video_generation_status to check the render.",
            }

    @mcp.tool()
    def get_video_generation_status(video_id: str) -> dict:
        """Check YOUR video's render job, return a temporary playable MP4 URL when ready."""
        with Session() as db:
            video = db.query(Video).filter_by(id=video_id, user_id=user_id()).first()
            if not video:
                raise ValueError("Video not found in your Studio account")
            job = db.query(VideoJob).filter_by(video_id=video.id).order_by(VideoJob.created.desc()).first()
            asset = db.get(VideoAsset, video.id)
            preview = create_preview_link(video) if asset else None
            return {
                "video_id": video.id,
                "title": video.title,
                "status": video.status,
                "job_status": job.status if job else None,
                "render_error": job.error if job and job.status == "failed" else None,
                "mp4_ready": asset is not None,
                "preview_url": preview,
                "preview_expires_in_seconds": 900 if preview else None,
                "published_url": video.post_url,
                "studio_dashboard": BASE + "/",
                "note": "A preview link is temporary. Uploading requires YouTube authorization and an approved video.",
            }

    @mcp.tool()
    def list_campaigns()->list[dict]:
        """List your Studio campaigns, target audiences and video schedules."""
        with Session() as db:
            return [campaign_data(c) for c in db.query(Campaign).filter_by(user_id=user_id())]

    @mcp.tool()
    def create_campaign(name:str,topic:str,audience:str,style:str="cutout",
                        duration_seconds:int=45,frequency:str="weekly",
                        auto_publish:bool=False)->dict:
        """Create a B2B series. Automatic YouTube publishing is opt-in only."""
        uid=user_id()
        if style not in {"cutout","duotone"} or not 10<=duration_seconds<=90:
            raise ValueError("Invalid style or duration")
        if frequency not in {"manual","daily","weekly","monthly"}:
            raise ValueError("Invalid frequency")
        with Session() as db:
            if auto_publish and not youtube_connected(db,uid):
                raise ValueError("Connect your YouTube account before enabling automatic publishing")
            c=Campaign(user_id=uid,name=name[:120],topic=topic[:2000],audience=audience[:2000],
                       style=style,length=duration_seconds,frequency=frequency,
                       mode="auto" if auto_publish else "review",
                       channels='["youtube"]',active=True)
            db.add(c);db.commit()
            return campaign_data(c)

    @mcp.tool()
    def list_videos()->list[dict]:
        """List your draft, rendering, scheduled and published video records."""
        with Session() as db:
            return [video_data(v) for v in db.query(Video).filter_by(user_id=user_id())
                    .order_by(Video.created.desc()).limit(100)]

    @mcp.tool()
    def create_video_draft(title:str,script:str,style:str="cutout",
                           duration_seconds:int=45,topic:str="",audience:str="")->dict:
        """Legacy: save a ChatGPT-written script without rendering. Prefer create_and_render_video."""
        uid=user_id()
        if style not in ("cutout","duotone") or not 10<=duration_seconds<=90:
            raise ValueError("Style must be cutout/duotone and duration 10–90 seconds")
        if not script.strip():
            raise ValueError("ChatGPT must supply the entire narration")
        with Session() as db:
            v=Video(user_id=uid,title=title[:160],
                    script=script.strip(),
                    style=style,length=duration_seconds,channels='["youtube"]',
                    status="draft")
            db.add(v);db.commit()
            return video_data(v)

    @mcp.tool()
    def render_video_draft(video_id:str)->dict:
        """Queue rendering an MP4 for an owned video draft, without publishing."""
        uid=user_id()
        with Session() as db:
            v=db.query(Video).filter_by(id=video_id,user_id=uid).first()
            if not v:raise ValueError("Video not found")
            if v.status in ("rendering","queued","publishing","scheduled","published"):
                raise ValueError("Video is already being processed or published")
            v.status="queued";queue_video(db,v.id,"render");db.commit()
            return {"video_id":v.id,"status":"queued","note":"Rendering happens asynchronously; check list_videos"}

    @mcp.tool()
    def approve_video_draft(video_id:str)->dict:
        """Approve an already rendered video; does not publish."""
        with Session() as db:
            v=db.query(Video).filter_by(id=video_id,user_id=user_id()).first()
            if not v or v.status!="ready" or not db.get(VideoAsset,video_id):
                raise ValueError("Video not found or not rendered yet")
            v.status="approved";db.commit()
            return {"video_id":v.id,"status":"approved"}

    @mcp.tool()
    def check_youtube_connection()->dict:
        """Check whether your YouTube channel is connected; do not reveal secrets."""
        with Session() as db:
            return {"oauth_client_configured":youtube_configured(),
                    "youtube_connected":youtube_connected(db,user_id()),
                    "connect_in_studio":BASE+"/"}

    @mcp.tool()
    def publish_approved_video_to_youtube(video_id:str,publish_at_iso:str="")->dict:
        """Publish an approved video to the user's authorized YouTube channel, or schedule it with an ISO timezone date. Only call on explicit user request."""
        uid=user_id()
        with Session() as db:
            v=db.query(Video).filter_by(id=video_id,user_id=uid).first()
            if not v or v.status!="approved" or not db.get(VideoAsset,video_id):
                raise ValueError("Only approved, rendered videos can be published")
            if not youtube_connected(db,uid):
                raise ValueError("Connect YouTube in Studio settings first")
            when=None
            if publish_at_iso:
                when=datetime.fromisoformat(publish_at_iso.replace("Z","+00:00"))
                if not when.tzinfo or when.astimezone(timezone.utc)<=now():
                    raise ValueError("Scheduled time must be in the future with timezone")
            v.scheduled_for=when
            queue_video(db,v.id,"youtube_publish")
            v.status="queued_for_upload"
            db.commit()
            return {"video_id":v.id,"status":"queued_for_youtube","scheduled_for":str(when) if when else None}

    @mcp.tool()
    def run_campaign_now(campaign_id:str, narration:str, scenes:list[SceneInstruction],
                         title:str="",youtube_description:str="")->dict:
        """Run a campaign using a narration and scenes created by ChatGPT in this conversation."""
        uid = user_id()
        with Session() as db:
            c=db.query(Campaign).filter_by(id=campaign_id,user_id=uid).first()
            if not c:raise ValueError("Campaign not found")
            v=create_chatgpt_video(
                db, uid, title or c.name, narration,
                [scene.model_dump() for scene in scenes],
                style=c.style, duration_seconds=c.length,
                campaign_id=c.id, youtube_description=youtube_description,
            )
            return {"video_id":v.id,"status":"queued","published":False}


if not SMOKE:
    @app.get("/.well-known/oauth-protected-resource/mcp")
    @app.get("/.well-known/oauth-protected-resource/mcp/")
    async def resource_metadata():
        return {"resource":MCP_RESOURCE,"authorization_servers":[ISSUER],
                "scopes_supported":["studio"],"bearer_methods_supported":["header"]}

    @app.get("/.well-known/oauth-authorization-server")
    @app.get("/.well-known/oauth-authorization-server/mcp")
    async def auth_metadata():
        return {"issuer":ISSUER,"authorization_endpoint":ISSUER+"/authorize",
                "token_endpoint":ISSUER+"/token","registration_endpoint":ISSUER+"/register",
                "revocation_endpoint":ISSUER+"/revoke",
                "response_types_supported":["code"],
                "grant_types_supported":["authorization_code","refresh_token"],
                "token_endpoint_auth_methods_supported":["client_secret_post","client_secret_basic"],
                "code_challenge_methods_supported":["S256"],
                "scopes_supported":["studio"]}

    # These route registrations MUST precede app.mount("/mcp",...), which would
    # otherwise intercept the consent page and prevent OAuth logins.
    app.add_api_route("/mcp/consent",consent_get,methods=["GET"])
    app.add_api_route("/mcp/consent",consent_post,methods=["POST"])


mcp_asgi_app=mcp.streamable_http_app()
original_lifespan=app.router.lifespan_context


async def job_loop():
    while True:
        try:
            await asyncio.to_thread(tick)
        except Exception:
            logger.exception("Studio background job tick failed")
        await asyncio.sleep(18)


@asynccontextmanager
async def lifespan_with_mcp(app_instance):
    async with original_lifespan(app_instance):
        async with mcp.session_manager.run():
            task=asyncio.create_task(job_loop()) if not SMOKE else None
            try:
                yield
            finally:
                if task:
                    task.cancel()
                    try:await task
                    except asyncio.CancelledError:pass


app.router.lifespan_context=lifespan_with_mcp
app.mount("/mcp",mcp_asgi_app)
