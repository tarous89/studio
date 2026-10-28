"""Database-backed OAuth authorization provider for ChatGPT MCP.
Uses the official MCP SDK's authorization-code, PKCE, DCR and resource auth handlers.
"""
import hashlib
import html
import json
import os
import secrets
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from mcp.server.auth.provider import (
    AccessToken, AuthorizationCode, AuthorizationParams,
    OAuthAuthorizationServerProvider, RefreshToken, construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl
from sqlalchemy import Column, DateTime, String, Text

from app import Base, Session, User, check_pw, engine, now

BASE = os.getenv("PUBLIC_BASE_URL","https://studio-web-hz4l.onrender.com").rstrip("/")
MCP_RESOURCE = BASE + "/mcp/"
ISSUER = BASE + "/mcp"


class OAuthRecord(Base):
    __tablename__ = "studio_mcp_oauth_records"
    key = Column(String, primary_key=True)
    kind = Column(String, nullable=False)
    value = Column(Text, nullable=False)
    expires_at = Column(DateTime(timezone=True))


Base.metadata.create_all(engine)


def hashed(value):
    return hashlib.sha256(value.encode()).hexdigest()


def write_record(kind, key, data, ttl_seconds=86400):
    with Session() as db:
        record=db.get(OAuthRecord,kind+":"+hashed(key))
        if not record:
            record=OAuthRecord(key=kind+":"+hashed(key),kind=kind)
            db.add(record)
        record.value=json.dumps(data)
        record.expires_at=now()+timedelta(seconds=ttl_seconds) if ttl_seconds else None
        db.commit()


def read_record(kind, key, consume=False):
    with Session() as db:
        record=db.get(OAuthRecord,kind+":"+hashed(key))
        if not record:
            return None
        if record.expires_at:
            exp=record.expires_at
            if exp.tzinfo is None:exp=exp.replace(tzinfo=timezone.utc)
            if exp<now():
                db.delete(record);db.commit()
                return None
        value=json.loads(record.value)
        if consume:
            db.delete(record);db.commit()
        return value


def delete_record(kind,key):
    with Session() as db:
        rec=db.get(OAuthRecord,kind+":"+hashed(key))
        if rec:db.delete(rec);db.commit()


class StudioOAuthProvider(OAuthAuthorizationServerProvider[AuthorizationCode,RefreshToken,AccessToken]):
    async def get_client(self,client_id:str):
        data=read_record("client",client_id)
        return OAuthClientInformationFull.model_validate(data) if data else None

    async def register_client(self,client_info:OAuthClientInformationFull):
        if not client_info.client_id:
            raise ValueError("Client ID required")
        write_record("client",client_info.client_id,client_info.model_dump(mode="json"),60*60*24*365)

    async def authorize(self,client:OAuthClientInformationFull,params:AuthorizationParams)->str:
        request_id=secrets.token_urlsafe(32)
        write_record("pending",request_id,{
            "client_id":client.client_id,
            "client_name":str(client.client_name or "ChatGPT"),
            "state":params.state,
            "redirect_uri":str(params.redirect_uri),
            "redirect_uri_provided_explicitly":params.redirect_uri_provided_explicitly,
            "code_challenge":params.code_challenge,
            "resource":params.resource or MCP_RESOURCE,
            "scopes":params.scopes or ["studio"],
        },600)
        return BASE+"/mcp/consent?request="+urllib.parse.quote(request_id)

    async def load_authorization_code(self,client:OAuthClientInformationFull,authorization_code:str):
        data=read_record("code",authorization_code)
        if not data:return None
        code=AuthorizationCode.model_validate(data)
        return code if code.client_id==client.client_id else None

    async def exchange_authorization_code(self,client:OAuthClientInformationFull,authorization_code:AuthorizationCode):
        data=read_record("code",authorization_code.code,consume=True)
        if not data:raise ValueError("Authorization code expired or already used")
        return self._tokens(authorization_code.subject or "",client.client_id,
                            authorization_code.resource or MCP_RESOURCE,authorization_code.scopes)

    def _tokens(self,subject,client_id,resource,scopes):
        access=secrets.token_urlsafe(48)
        refresh=secrets.token_urlsafe(48)
        now_seconds=int(time.time())
        write_record("access",access,AccessToken(
            token=access,client_id=client_id,scopes=scopes,
            expires_at=now_seconds+3600,resource=resource,subject=subject,
        ).model_dump(mode="json"),3600)
        write_record("refresh",refresh,RefreshToken(
            token=refresh,client_id=client_id,scopes=scopes,
            expires_at=now_seconds+60*60*24*30,resource=resource,subject=subject,
        ).model_dump(mode="json"),60*60*24*30)
        return OAuthToken(access_token=access,token_type="Bearer",
                          expires_in=3600,refresh_token=refresh,scope=" ".join(scopes))

    async def load_access_token(self,token:str):
        data=read_record("access",token)
        return AccessToken.model_validate(data) if data else None

    async def load_refresh_token(self,client:OAuthClientInformationFull,refresh_token:str):
        data=read_record("refresh",refresh_token)
        if not data:return None
        obj=RefreshToken.model_validate(data)
        return obj if obj.client_id==client.client_id else None

    async def exchange_refresh_token(self,client:OAuthClientInformationFull,
                                     refresh_token:RefreshToken,scopes:list[str]):
        data=read_record("refresh",refresh_token.token,consume=True)
        if not data:raise ValueError("Expired refresh token")
        allowed=set(refresh_token.scopes)
        return self._tokens(refresh_token.subject or "",client.client_id,
                            refresh_token.resource or MCP_RESOURCE,
                            [s for s in (scopes or refresh_token.scopes) if s in allowed])

    async def revoke_token(self,token:str,token_type_hint:str|None=None):
        for kind in ("access","refresh"):
            delete_record(kind,token)


provider=StudioOAuthProvider()


async def consent_get(request:Request):
    request_id=request.query_params.get("request","")
    data=read_record("pending",request_id)
    if not data:
        return HTMLResponse("<h2>Authorization request expired</h2>",status_code=400)
    name=html.escape(data.get("client_name","ChatGPT"))
    resource=html.escape(MCP_RESOURCE)
    body=f"""<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"/>
    <title>Connect Studio to ChatGPT</title><style>
    body{{font:15px system-ui;background:linear-gradient(115deg,#e8e4fa,#f7e8eb);margin:0;padding:10vh 20px}}
    .panel{{max-width:410px;margin:auto;background:#ffffffdd;backdrop-filter:blur(24px);border-radius:24px;padding:32px;box-shadow:0 20px 50px #57487522}}
    h1{{font-size:26px;color:#29263d}}p{{color:#656273;line-height:1.6}}input{{padding:12px;margin-top:6px;border:1px solid #d9d3e7;border-radius:10px;width:100%;box-sizing:border-box}}
    label{{display:block;margin:16px 0;font-weight:600}}button{{background:#8e7acb;border:0;color:white;border-radius:12px;padding:14px 20px;width:100%;font-weight:700}}
    small{{color:#868191}}</style></head><body><div class="panel">
    <h1>Connect to Studio ✦</h1><p><b>{name}</b> requests access to your Studio campaigns and videos.</p>
    <p><small>MCP resource: {resource}</small></p>
    <form action="/mcp/consent" method="post">
    <input type="hidden" name="request" value="{html.escape(request_id,quote=True)}"/>
    <label>Studio email<input name="email" autocomplete="username" type="email" required/></label>
    <label>Studio password<input name="password" autocomplete="current-password" type="password" required/></label>
    <button type="submit">Authorize Studio access</button></form>
    <p><small>Only approve if you initiated the connection in ChatGPT.</small></p>
    </div></body></html>"""
    return HTMLResponse(body,headers={"Cache-Control":"no-store"})


async def consent_post(request:Request):
    # Parse an application/x-www-form-urlencoded body without logging passwords.
    raw=(await request.body())
    if len(raw)>8192:
        return HTMLResponse("Request too large",status_code=413)
    fields=urllib.parse.parse_qs(raw.decode("utf-8","replace"))
    request_id=fields.get("request",[""])[0]
    email=fields.get("email",[""])[0].strip().lower()
    password=fields.get("password",[""])[0]
    pending=read_record("pending",request_id)
    if not pending:
        return HTMLResponse("Authorization request expired",status_code=400)
    with Session() as db:
        user=db.query(User).filter_by(email=email).first()
        if not user or not check_pw(password,user.password):
            return HTMLResponse("<h2>Invalid Studio login</h2><p>Return to ChatGPT and retry authorization.</p>",status_code=401)
        subject=user.id
    # Single-use pending request; the SDK validates the original redirect_uri.
    consumed=read_record("pending",request_id,consume=True)
    if not consumed:
        return HTMLResponse("Already authorized",status_code=409)
    code_value=secrets.token_urlsafe(36)
    code=AuthorizationCode(
        code=code_value,client_id=pending["client_id"],
        redirect_uri=AnyHttpUrl(pending["redirect_uri"]),
        redirect_uri_provided_explicitly=bool(pending["redirect_uri_provided_explicitly"]),
        code_challenge=pending["code_challenge"],
        resource=pending["resource"],scopes=pending["scopes"],
        expires_at=time.time()+300,subject=subject)
    write_record("code",code_value,code.model_dump(mode="json"),300)
    url=construct_redirect_uri(pending["redirect_uri"],code=code_value,state=pending.get("state"))
    return RedirectResponse(url,status_code=303)
