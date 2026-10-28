"""Studio video renderer, durable media storage and YouTube upload queue.
The renderer is an original, portable implementation; no third-party source ZIPs
or nonfree audio are copied into this service.
"""
import base64
import hashlib
import io
import json
import math
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import textwrap
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from cryptography.fernet import Fernet
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import Column, DateTime, ForeignKey, Integer, LargeBinary, String, Text

from app import Base, Campaign, Session, Video, engine, now

PUBLIC_URL = os.getenv("PUBLIC_BASE_URL", "https://studio-web-hz4l.onrender.com").rstrip("/")
SCOPES = "https://www.googleapis.com/auth/youtube.upload"
MAX_VIDEO_BYTES = int(os.getenv("MAX_VIDEO_BYTES", "20000000"))


class VideoAsset(Base):
    __tablename__ = "studio_video_assets"
    video_id = Column(String, primary_key=True)
    data = Column(LargeBinary, nullable=False)
    created = Column(DateTime(timezone=True), default=now)


class YouTubeAccount(Base):
    __tablename__ = "studio_youtube_accounts"
    user_id = Column(String, primary_key=True)
    encrypted_refresh_token = Column(Text, nullable=False)
    connected_at = Column(DateTime(timezone=True), default=now)


class YouTubeState(Base):
    __tablename__ = "studio_youtube_oauth_states"
    state_hash = Column(String, primary_key=True)
    user_id = Column(String, nullable=False)
    verifier = Column(String, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)


class VideoJob(Base):
    __tablename__ = "studio_video_jobs"
    id = Column(String, primary_key=True)
    video_id = Column(String, nullable=False, index=True)
    kind = Column(String, nullable=False)
    status = Column(String, default="queued")
    due_at = Column(DateTime(timezone=True), default=now)
    attempts = Column(Integer, default=0)
    error = Column(Text)
    created = Column(DateTime(timezone=True), default=now)


class CampaignClock(Base):
    __tablename__ = "studio_campaign_clocks"
    campaign_id = Column(String, primary_key=True)
    last_generated = Column(DateTime(timezone=True))


Base.metadata.create_all(engine)


def as_utc(dt):
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def encrypted(value):
    key = os.getenv("ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("ENCRYPTION_KEY must be configured")
    return Fernet(key.encode()).encrypt(value.encode()).decode()


def decrypted(value):
    key = os.getenv("ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("ENCRYPTION_KEY must be configured")
    return Fernet(key.encode()).decrypt(value.encode()).decode()


def youtube_configured():
    return bool(os.getenv("DATABASE_URL") and os.getenv("YOUTUBE_CLIENT_ID") and os.getenv("YOUTUBE_CLIENT_SECRET") and os.getenv("ENCRYPTION_KEY"))


def youtube_connected(db, user_id):
    return db.get(YouTubeAccount, user_id) is not None


def start_youtube_connect(user_id):
    if not youtube_configured():
        raise ValueError("Set YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET and ENCRYPTION_KEY on Render first")
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    with Session() as db:
        db.add(YouTubeState(
            state_hash=hashlib.sha256(state.encode()).hexdigest(),
            user_id=user_id,
            verifier=verifier,
            expires_at=now() + timedelta(minutes=10),
        ))
        db.commit()
    params = {
        "client_id": os.environ["YOUTUBE_CLIENT_ID"],
        "redirect_uri": PUBLIC_URL + "/api/youtube/callback",
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)


def complete_youtube_connect(state, code):
    with Session() as db:
        row = db.get(YouTubeState, hashlib.sha256(state.encode()).hexdigest())
        if not row or as_utc(row.expires_at) < now():
            raise ValueError("Invalid or expired YouTube authorization state")
        user_id, verifier = row.user_id, row.verifier
        db.delete(row)
        db.commit()
    with httpx.Client(timeout=25) as client:
        response = client.post("https://oauth2.googleapis.com/token", data={
            "client_id": os.environ["YOUTUBE_CLIENT_ID"],
            "client_secret": os.environ["YOUTUBE_CLIENT_SECRET"],
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": PUBLIC_URL + "/api/youtube/callback",
            "code_verifier": verifier,
        })
        response.raise_for_status()
        payload = response.json()
    refresh_token = payload.get("refresh_token")
    if not refresh_token:
        raise ValueError("Google did not provide a refresh token; revoke and reconnect the application")
    with Session() as db:
        account = db.get(YouTubeAccount, user_id)
        if not account:
            account = YouTubeAccount(user_id=user_id, encrypted_refresh_token=encrypted(refresh_token))
            db.add(account)
        else:
            account.encrypted_refresh_token = encrypted(refresh_token)
            account.connected_at = now()
        db.commit()


def youtube_access_token(user_id):
    with Session() as db:
        row = db.get(YouTubeAccount, user_id)
        if not row:
            raise RuntimeError("Connect your YouTube account in Studio settings")
        refresh_token = decrypted(row.encrypted_refresh_token)
    with httpx.Client(timeout=25) as client:
        response = client.post("https://oauth2.googleapis.com/token", data={
            "client_id": os.environ["YOUTUBE_CLIENT_ID"],
            "client_secret": os.environ["YOUTUBE_CLIENT_SECRET"],
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        })
        response.raise_for_status()
        return response.json()["access_token"]


def upload_to_youtube(video, content, scheduled_for=None):
    """Upload directly to YouTube. A future date uses native private scheduled publishing."""
    token = youtube_access_token(video.user_id)
    future = scheduled_for and as_utc(scheduled_for) > now() + timedelta(minutes=2)
    status = {"privacyStatus": "private" if future else "public",
              "selfDeclaredMadeForKids": False}
    if future:
        status["publishAt"] = as_utc(scheduled_for).isoformat().replace("+00:00", "Z")
    caption = f"{video.script[:1500]}\n\n#Shorts"
    metadata = {
        "snippet": {"title": video.title[:100], "description": caption, "categoryId": "28"},
        "status": status,
    }
    with httpx.Client(timeout=120) as client:
        init = client.post(
            "https://www.googleapis.com/upload/youtube/v3/videos",
            params={"uploadType": "resumable", "part": "snippet,status"},
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json",
                     "X-Upload-Content-Type": "video/mp4",
                     "X-Upload-Content-Length": str(len(content))},
            json=metadata,
        )
        init.raise_for_status()
        location = init.headers.get("Location")
        if not location or not location.startswith("https://www.googleapis.com/"):
            raise RuntimeError("YouTube did not return a valid resumable upload location")
        offset = 0
        for attempt in range(5):
            result = client.put(
                location,
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "video/mp4",
                         "Content-Length": str(len(content) - offset),
                         "Content-Range": f"bytes {offset}-{len(content)-1}/{len(content)}"},
                content=content[offset:],
            )
            if result.status_code in (200, 201):
                video_id = result.json().get("id")
                if not video_id:
                    raise RuntimeError("YouTube upload returned no video id")
                return {"id": video_id, "url": "https://www.youtube.com/watch?v=" + video_id, "scheduled": bool(future)}
            if result.status_code == 308 or result.status_code >= 500:
                probe = client.put(location, headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Range": f"bytes */{len(content)}",
                    "Content-Length": "0",
                })
                if probe.status_code in (200, 201):
                    video_id = probe.json().get("id")
                    return {"id": video_id, "url": "https://www.youtube.com/watch?v=" + video_id, "scheduled": bool(future)}
                if probe.status_code != 308:
                    probe.raise_for_status()
                m = re.search(r"bytes=0-(\d+)", probe.headers.get("Range", ""))
                offset = int(m.group(1)) + 1 if m else 0
                time.sleep(min(2 ** attempt, 8))
                continue
            result.raise_for_status()
        raise RuntimeError("YouTube upload interrupted after retries")


def _font(size, bold=True):
    path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    return ImageFont.truetype(path, size)


def _wrap(text, max_chars=21):
    return textwrap.wrap(" ".join(str(text).split()), max_chars, break_long_words=False) or [""]


def _slide(title, text, index, total, style, path):
    w, h = 720, 1280
    if style == "duotone":
        bg, paper, ink, accent, pale = "#131c35", "#283f65", "#f8f4e7", "#f5ba91", "#4ed5b0"
    else:
        bg, paper, ink, accent, pale = "#f2e9dd", "#fff9ed", "#243446", "#ed9b81", "#8b8ace"
    im = Image.new("RGB", (w,h), bg)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((36,80,684,1195),radius=38,fill=paper)
    d.ellipse((455,-120,875,300),fill=pale)
    d.ellipse((-180,870,240,1280),fill=accent)
    d.rounded_rectangle((66,112,368,167),radius=23,fill=ink)
    d.text((84,125),"STUDIO  /  B2B STORIES",font=_font(19),fill=paper)
    lines = _wrap(text,18)[:8]
    size = 56 if len(lines) <= 5 else 46
    y = max(380, 635-len(lines)*42)
    for line in lines:
        d.text((83,y),line,font=_font(size),fill=ink,stroke_width=0)
        y += size+18
    d.text((75,1010),title[:37],font=_font(27),fill=ink)
    d.text((75,1090),f"{index+1:02d} / {total:02d}",font=_font(25),fill=ink)
    d.rounded_rectangle((75,1145,640,1155),radius=5,fill="#d4cfcc")
    d.rounded_rectangle((75,1145,75+int(565*(index+1)/total),1155),radius=5,fill=accent)
    im.save(path)


def _segments(video):
    script = (video.script or video.title).replace("\n"," ")
    parts = [x.strip() for x in re.split(r"(?<=[.!?])\s+",script) if x.strip()]
    if len(parts) < 2:
        parts = _wrap(script,65)
    if len(parts) > 12:
        parts = parts[:12]
    return parts


def render_video(video):
    """Create an original, portrait MP4 with paper-like cards and spoken narration."""
    seconds = max(10,min(90,int(video.length or 45)))
    segments = _segments(video)
    with tempfile.TemporaryDirectory(prefix="studio-render-") as folder:
        paths=[]
        for idx,part in enumerate(segments):
            path=Path(folder)/f"{idx:03}.png"
            _slide(video.title,part,idx,len(segments),video.style,str(path))
            paths.append(path)
        manifest=Path(folder)/"slides.txt"
        dur=seconds/len(paths)
        with manifest.open("w") as f:
            for path in paths:
                f.write(f"file '{path.as_posix()}'\nduration {dur:.3f}\n")
            f.write(f"file '{paths[-1].as_posix()}'\n")
        narration=Path(folder)/"voice.wav"
        voice_script=" ".join(segments)
        has_voice=shutil.which("espeak-ng") is not None
        if has_voice:
            subprocess.run(["espeak-ng","-v","en-us","-s","160","-w",str(narration),voice_script],
                           check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=40)
        output=Path(folder)/"video.mp4"
        cmd=["ffmpeg","-hide_banner","-loglevel","error","-nostdin","-y",
             "-f","concat","-safe","0","-i",str(manifest)]
        if has_voice:
            cmd += ["-i",str(narration)]
        else:
            cmd += ["-f","lavfi","-i","anullsrc=channel_layout=stereo:sample_rate=44100"]
        cmd += ["-vf","fps=18,format=yuv420p","-c:v","libx264","-preset","ultrafast",
                "-crf","28","-c:a","aac","-b:a","96k","-af","apad",
                "-t",str(seconds),"-movflags","+faststart",str(output)]
        subprocess.run(cmd,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=max(150,seconds*8))
        content=output.read_bytes()
        if len(content)>MAX_VIDEO_BYTES:
            raise RuntimeError("Video exceeds safe size limit; shorten duration")
        return content


def draft_script(title, audience, topic):
    """Safe deterministic fallback; factual narratives require a supplied script or AI key."""
    if os.getenv("OPENAI_API_KEY"):
        prompt = ("Write a concise B2B 9:16 video narration, 80-110 words, as plain text. "
                  "Do not invent dates, companies, clinical results, efficacy, or statistics. "
                  "Write a useful, grounded conceptual insight with a clear hook and a final question. "
                  f"Title: {title}. Topic: {topic}. Target audience: {audience}.")
        try:
            with httpx.Client(timeout=35) as client:
                r=client.post("https://api.openai.com/v1/responses",
                              headers={"Authorization":"Bearer "+os.environ["OPENAI_API_KEY"]},
                              json={"model":os.getenv("STUDIO_WRITER_MODEL","gpt-4.1-mini"),
                                    "input":prompt,"max_output_tokens":260})
                r.raise_for_status()
                parts=[x.get("text","") for o in r.json().get("output",[])
                       for x in o.get("content",[]) if x.get("type")=="output_text"]
                if parts and " ".join(parts).strip():
                    return " ".join(parts).strip()
        except Exception:
            pass
    topic=(topic or title).strip()
    audience=(audience or "decision-makers").strip()
    return (f"{topic}. What should {audience} know? "
            f"Start with the problem that matters most. "
            "Compare available approaches and their trade-offs. "
            "Look for evidence you can verify, rather than promises. "
            "Ask what changes for your team, your customers, and your next decision. "
            f"Which question about {topic} deserves a closer look?")


def queue_video(db, video_id, kind, due_at=None):
    if db.query(VideoJob).filter_by(video_id=video_id,kind=kind,status="queued").first():
        return
    db.add(VideoJob(id=secrets.token_hex(16),video_id=video_id,kind=kind,
                    due_at=due_at or now(),status="queued"))


def periodic_campaigns():
    """Generate draft videos from opted-in recurring campaigns; safe default is review."""
    with Session() as db:
        for c in db.query(Campaign).filter_by(active=True).all():
            interval={"daily":timedelta(days=1),"weekly":timedelta(days=7),
                      "monthly":timedelta(days=30)}.get(c.frequency)
            if not interval:
                continue
            clock=db.get(CampaignClock,c.id)
            if clock and clock.last_generated and (now()-as_utc(clock.last_generated))<interval:
                continue
            video=Video(user_id=c.user_id,campaign_id=c.id,
                        title=(c.topic or c.name)[:100],style=c.style,length=c.length,
                        script=draft_script(c.name,c.audience,c.topic),
                        channels=c.channels,status="queued")
            db.add(video)
            db.flush()
            queue_video(db,video.id,"render")
            if not clock:
                db.add(CampaignClock(campaign_id=c.id,last_generated=now()))
            else:
                clock.last_generated=now()
        db.commit()


def one_job():
    """One job per loop; terminal failure stays visible and is never retried indefinitely."""
    with Session() as db:
        job=(db.query(VideoJob).filter(VideoJob.status=="queued",
                 VideoJob.due_at<=now()).order_by(VideoJob.created)
             .with_for_update(skip_locked=True).first())
        if not job:
            return False
        video=db.get(Video,job.video_id)
        if not video:
            job.status="failed";job.error="Video removed";db.commit();return True
        job.status="running";job.attempts+=1
        job_id=job.id
        video_id=video.id
        kind=job.kind
        db.commit()
    try:
        if kind=="render":
            with Session() as db:
                v=db.get(Video,video_id);v.status="rendering";db.commit()
                content=render_video(v)
                old=db.get(VideoAsset,video_id)
                if old:old.data=content
                else:db.add(VideoAsset(video_id=video_id,data=content))
                v.status="ready"
                c=db.get(Campaign,v.campaign_id) if v.campaign_id else None
                if c and c.mode=="auto" and youtube_connected(db,v.user_id):
                    v.status="approved"
                    queue_video(db,v.id,"youtube_publish",now()+timedelta(minutes=10))
                db.commit()
        elif kind=="youtube_publish":
            with Session() as db:
                v=db.get(Video,video_id)
                if not v or v.status not in ("approved","scheduled","publishing"):
                    raise RuntimeError("Video is not approved for publication")
                data=db.get(VideoAsset,video_id)
                if data is None:
                    raise RuntimeError("Render a video first")
                v.status="publishing";db.commit()
                result=upload_to_youtube(v,data.data,v.scheduled_for)
                v.post_url=result["url"]
                v.status="scheduled" if result["scheduled"] else "published"
                db.commit()
        else:
            raise RuntimeError("Unknown job kind")
        with Session() as db:
            job=db.get(VideoJob,job_id);job.status="complete";job.error=None;db.commit()
    except Exception as exc:
        with Session() as db:
            job=db.get(VideoJob,job_id)
            if job:job.status="failed";job.error=str(exc)[:500]
            v=db.get(Video,video_id)
            if v:v.status="failed"
            db.commit()
    return True


def tick():
    periodic_campaigns()
    one_job()
