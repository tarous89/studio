"""Authenticated Studio API endpoints for generation, YouTube OAuth and publication."""
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from app import Campaign, Session, Video, auth, now, video_data
from studio_pipeline import (
    VideoAsset, VideoJob, complete_youtube_connect, draft_script, queue_video,
    start_youtube_connect, youtube_configured, youtube_connected, MAX_VIDEO_BYTES
)

router = APIRouter(prefix="/api")


def owned_video(db, user_id, video_id):
    v = db.query(Video).filter_by(id=video_id, user_id=user_id).first()
    if not v:
        raise HTTPException(404, "Video not found")
    return v


@router.get("/studio/config")
def config(user_id=Depends(auth)):
    with Session() as db:
        return {
            "youtube_oauth_configured": youtube_configured(),
            "youtube_connected": youtube_connected(db,user_id),
            "ai_writer_configured": bool(os.getenv("OPENAI_API_KEY")),
            "asset_storage": "postgres_bytea" if os.getenv("DATABASE_URL") else "ephemeral_sqlite",
            "max_video_bytes": MAX_VIDEO_BYTES,
            "auto_publish_available": youtube_configured() and youtube_connected(db,user_id),
        }


@router.get("/youtube/connect")
def connect(user_id=Depends(auth)):
    try:
        return {"authorization_url": start_youtube_connect(user_id)}
    except ValueError as exc:
        raise HTTPException(503,str(exc))


@router.get("/youtube/callback")
def youtube_callback(code: str="", state: str="", error: str=""):
    if error:
        return HTMLResponse(f"<h2>YouTube authorization declined</h2><p>Please return to Studio settings.</p>",status_code=400)
    if not code or not state:
        raise HTTPException(400,"Missing authorization response")
    try:
        complete_youtube_connect(state,code)
    except Exception:
        return HTMLResponse("<h2>YouTube authorization failed</h2><p>Reopen Studio settings and try again. Review Render logs if needed.</p>",status_code=400)
    return HTMLResponse('<!doctype html><html><head><title>Connected - Studio</title></head><body style="font:16px system-ui;max-width:550px;margin:12vh auto"><h1>YouTube connected</h1><p>You can now render and publish to your authorized YouTube account.</p><a href="/">Return to Studio</a></body></html>')


@router.get("/youtube/status")
def youtube_status(user_id=Depends(auth)):
    with Session() as db:
        return {"configured": youtube_configured(),
                "connected": youtube_connected(db,user_id)}


@router.post("/youtube/disconnect")
def disconnect(user_id=Depends(auth)):
    from studio_pipeline import YouTubeAccount
    with Session() as db:
        account=db.get(YouTubeAccount,user_id)
        if account:db.delete(account);db.commit()
    return {"connected":False}


class ScriptRequest(BaseModel):
    title: str=Field(min_length=1,max_length=160)
    audience: str=""
    topic: str=""


@router.post("/studio/script")
def generate_script(data:ScriptRequest,user_id=Depends(auth)):
    return {"script":draft_script(data.title,data.audience,data.topic)}


@router.post("/videos/{video_id}/render")
def queue_render(video_id:str,user_id=Depends(auth)):
    with Session() as db:
        v=owned_video(db,user_id,video_id)
        if v.status in ("queued","rendering","publishing"):
            raise HTTPException(409,"Video is already being processed")
        if v.status in ("published","scheduled"):
            raise HTTPException(409,"Published or scheduled videos cannot be rendered again")
        v.status="queued"
        queue_video(db,v.id,"render")
        db.commit()
        return {"id":v.id,"status":v.status}


@router.get("/videos/{video_id}/media")
def media(video_id:str,user_id=Depends(auth)):
    with Session() as db:
        owned_video(db,user_id,video_id)
        asset=db.get(VideoAsset,video_id)
        if not asset:raise HTTPException(404,"No rendered MP4 is available yet")
        data=asset.data
    return Response(data,media_type="video/mp4",headers={"Cache-Control":"private, no-store",
      "Content-Disposition":f'inline; filename="studio-{video_id}.mp4"'})


@router.post("/videos/{video_id}/approve")
def approve_video(video_id:str,user_id=Depends(auth)):
    with Session() as db:
        v=owned_video(db,user_id,video_id)
        if v.status!="ready":raise HTTPException(409,"Render the video first")
        if not db.get(VideoAsset,video_id):raise HTTPException(404,"MP4 missing")
        v.status="approved";db.commit()
        return {"status":v.status,"id":v.id}


class PublicationRequest(BaseModel):
    publish_at: datetime|None=None


@router.post("/videos/{video_id}/publish")
def publish_video(video_id:str,data:PublicationRequest,user_id=Depends(auth)):
    with Session() as db:
        v=owned_video(db,user_id,video_id)
        if v.status!="approved":
            raise HTTPException(409,"Video must be approved before publishing")
        if not youtube_connected(db,user_id):
            raise HTTPException(422,"Connect YouTube in Settings first")
        if not db.get(VideoAsset,v.id):
            raise HTTPException(404,"Render video first")
        if data.publish_at:
            if not data.publish_at.tzinfo:
                raise HTTPException(400,"publish_at must include a timezone")
            if data.publish_at.astimezone(timezone.utc)<=now():
                raise HTTPException(400,"Scheduled publication time must be in the future")
        v.scheduled_for=data.publish_at
        v.status="queued_for_upload"
        queue_video(db,v.id,"youtube_publish")
        db.commit()
        return {"id":v.id,"status":"queued_for_youtube","publish_at":data.publish_at}


@router.get("/videos/{video_id}/jobs")
def jobs(video_id:str,user_id=Depends(auth)):
    with Session() as db:
        owned_video(db,user_id,video_id)
        return [{"kind":j.kind,"status":j.status,"attempts":j.attempts,"error":j.error}
                for j in db.query(VideoJob).filter_by(video_id=video_id).order_by(VideoJob.created.desc()).limit(10)]


@router.post("/campaigns/{campaign_id}/run")
def run_campaign(campaign_id:str,user_id=Depends(auth)):
    with Session() as db:
        c=db.query(Campaign).filter_by(id=campaign_id,user_id=user_id).first()
        if not c:raise HTTPException(404,"Campaign not found")
        v=Video(user_id=user_id,campaign_id=c.id,style=c.style,length=c.length,
                title=c.topic[:100] or c.name,script=draft_script(c.name,c.audience,c.topic),
                channels=c.channels,status="queued")
        db.add(v);db.flush()
        queue_video(db,v.id,"render")
        db.commit()
        return {"id":v.id,"status":"queued"}


def mount(app):
    app.include_router(router)
