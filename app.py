import os, json, uuid, secrets, hashlib, hmac, base64
from datetime import datetime,timezone
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import create_engine,Column,String,Integer,DateTime,Text,Boolean,ForeignKey
from sqlalchemy.orm import declarative_base,sessionmaker
from cryptography.fernet import Fernet
import httpx

url=os.getenv('DATABASE_URL','sqlite:///./studio.sqlite3')
url=url.replace('postgres://','postgresql+psycopg://',1).replace('postgresql://','postgresql+psycopg://',1)
engine=create_engine(url,pool_pre_ping=True,connect_args={'check_same_thread':False} if url.startswith('sqlite') else {})
Session=sessionmaker(bind=engine)
Base=declarative_base()
def uid():return str(uuid.uuid4())
def now():return datetime.now(timezone.utc)
class User(Base):
 __tablename__='users'
 id=Column(String,primary_key=True,default=uid)
 email=Column(String,unique=True,nullable=False)
 password=Column(String,nullable=False)
class Campaign(Base):
 __tablename__='campaigns'
 id=Column(String,primary_key=True,default=uid)
 user_id=Column(String,index=True,nullable=False)
 name=Column(String,nullable=False)
 topic=Column(Text,default='')
 audience=Column(Text,default='')
 style=Column(String,default='cutout')
 channels=Column(Text,default='["linkedin"]')
 length=Column(Integer,default=45)
 frequency=Column(String,default='weekly')
 mode=Column(String,default='review')
 active=Column(Boolean,default=True)
class Video(Base):
 __tablename__='videos'
 id=Column(String,primary_key=True,default=uid)
 user_id=Column(String,index=True,nullable=False)
 campaign_id=Column(String)
 title=Column(String)
 script=Column(Text,default='')
 style=Column(String,default='cutout')
 length=Column(Integer,default=45)
 status=Column(String,default='draft')
 channels=Column(Text,default='[]')
 scheduled_for=Column(DateTime(timezone=True))
 created=Column(DateTime(timezone=True),default=now)
 post_url=Column(String)
class Token(Base):
 __tablename__='tokens'
 digest=Column(String,primary_key=True)
 user_id=Column(String,index=True)
class Credential(Base):
 __tablename__='credentials'
 id=Column(String,primary_key=True,default=uid)
 user_id=Column(String,index=True)
 base_url=Column(String)
 secret=Column(Text)
Base.metadata.create_all(engine)
app=FastAPI(title='Studio API')

@app.middleware('http')
async def stage_guard(request: Request, call_next):
    # A private ChatGPT plugin does not make its Render HTTPS host private.
    # For the connectivity test, disable all account, docs, and write APIs.
    if os.getenv('STUDIO_SMOKE_ONLY') == '1':
        if request.url.path.startswith('/api/') or request.url.path in ('/openapi.json', '/docs', '/redoc'):
            return JSONResponse({'detail': 'Studio staging exposes MCP connectivity only'}, status_code=403)
    return await call_next(request)

@app.get('/health')
def health():return {'status':'ok','storage':'postgres' if not engine.url.get_backend_name().startswith('sqlite') else 'temporary_sqlite','renderer':'available'}
def hash_pw(s):
 salt=secrets.token_bytes(16)
 return base64.urlsafe_b64encode(salt+hashlib.scrypt(s.encode(),salt=salt,n=16384,r=8,p=1)).decode()
def check_pw(s,hash):
 try:
  raw=base64.urlsafe_b64decode(hash)
  return hmac.compare_digest(raw[16:],hashlib.scrypt(s.encode(),salt=raw[:16],n=16384,r=8,p=1))
 except Exception:return False
def digest(t):return hashlib.sha256(t.encode()).hexdigest()
def auth(request:Request):
 token=request.headers.get('authorization','').removeprefix('Bearer ').strip()
 with Session() as db:
  row=db.get(Token,digest(token))
  if not token or not row:raise HTTPException(401,'Sign in first')
  return row.user_id
class Login(BaseModel):
 email:str
 password:str
class CampaignData(BaseModel):
 name:str=Field(min_length=1)
 topic:str=''
 audience:str=''
 style:str='cutout'
 channels:list[str]=['youtube']
 length:int=45
 frequency:str='weekly'
 mode:str='review'
 active:bool=True
class VideoData(BaseModel):
 title:str=Field(min_length=1)
 script:str=''
 style:str='cutout'
 length:int=45
 campaign_id:str|None=None
 channels:list[str]=['youtube']
class ScheduleData(BaseModel):
 scheduled_for:datetime
class PostizData(BaseModel):
 base_url:str
 api_key:str
def sign_in(u,db):
 t=secrets.token_urlsafe(48)
 db.add(Token(digest=digest(t),user_id=u.id));db.commit()
 return {'token':t,'email':u.email}
@app.post('/api/auth/register')
def register(data:Login):
 if len(data.password)<12:raise HTTPException(400,'Use at least 12 characters')
 with Session() as db:
  if db.query(User).filter_by(email=data.email.lower()).first():raise HTTPException(409,'Account exists')
  u=User(email=data.email.lower(),password=hash_pw(data.password))
  db.add(u);db.commit();return sign_in(u,db)
@app.post('/api/auth/login')
def login(data:Login):
 with Session() as db:
  u=db.query(User).filter_by(email=data.email.lower()).first()
  if not u or not check_pw(data.password,u.password):raise HTTPException(401,'Invalid login')
  return sign_in(u,db)
@app.get('/api/me')
def me(id=Depends(auth)):
 with Session() as db:
  u=db.get(User,id);return {'email':u.email,'id':u.id}
def campaign_data(c):return dict(id=c.id,name=c.name,topic=c.topic,audience=c.audience,style=c.style,channels=json.loads(c.channels),length=c.length,frequency=c.frequency,mode=c.mode,active=c.active)
def video_data(v):return dict(id=v.id,title=v.title,script=v.script,status=v.status,style=v.style,length=v.length,channels=json.loads(v.channels),campaign_id=v.campaign_id,scheduled_for=v.scheduled_for,created=v.created,post_url=v.post_url)
@app.get('/api/campaigns')
def campaigns(id=Depends(auth)):
 with Session() as db:return [campaign_data(c) for c in db.query(Campaign).filter_by(user_id=id)]
@app.post('/api/campaigns')
def create_campaign(data:CampaignData,id=Depends(auth)):
 with Session() as db:
  if data.style not in ('cutout','duotone') or not 10<=data.length<=90:raise HTTPException(400,'Invalid video format')
  if data.mode=='auto':
   from studio_pipeline import youtube_connected
   if not youtube_connected(db,id):raise HTTPException(422,'Connect YouTube before enabling automatic publishing')
  c=Campaign(user_id=id,**data.model_dump(exclude={'channels'}),channels=json.dumps(data.channels))
  db.add(c);db.commit();return campaign_data(c)
@app.put('/api/campaigns/{cid}')
def edit_campaign(cid:str,data:CampaignData,id=Depends(auth)):
 with Session() as db:
  c=db.query(Campaign).filter_by(id=cid,user_id=id).first()
  if not c:raise HTTPException(404)
  if data.style not in ('cutout','duotone') or not 10<=data.length<=90:raise HTTPException(400,'Invalid video format')
  if data.mode=='auto':
   from studio_pipeline import youtube_connected
   if not youtube_connected(db,id):raise HTTPException(422,'Connect YouTube before enabling automatic publishing')
  for k,v in data.model_dump().items():setattr(c,k,json.dumps(v) if k=='channels' else v)
  db.commit();return campaign_data(c)
@app.get('/api/videos')
def videos(id=Depends(auth)):
 with Session() as db:return [video_data(v) for v in db.query(Video).filter_by(user_id=id).order_by(Video.created.desc()).limit(500)]
@app.post('/api/videos')
def create_video(data:VideoData,id=Depends(auth)):
 with Session() as db:
  if data.campaign_id and not db.query(Campaign).filter_by(user_id=id,id=data.campaign_id).first():raise HTTPException(404,'Campaign not found')
  if data.style not in ('cutout','duotone') or not 10<=data.length<=90:raise HTTPException(400,'Invalid video format')
  v=Video(user_id=id,**data.model_dump(exclude={'channels'}),channels=json.dumps(data.channels))
  db.add(v);db.commit();return video_data(v)
@app.post('/api/videos/{vid}/schedule')
def schedule(vid:str,data:ScheduleData,id=Depends(auth)):
 with Session() as db:
  v=db.query(Video).filter_by(user_id=id,id=vid).first()
  if not v:raise HTTPException(404)
  if data.scheduled_for.tzinfo is None or data.scheduled_for.astimezone(timezone.utc)<=now():raise HTTPException(400,'Future time with timezone required')
  from studio_pipeline import VideoAsset,queue_video,youtube_connected
  if v.status!='approved' or not db.get(VideoAsset,v.id):raise HTTPException(409,'Approve a rendered MP4 first')
  if not youtube_connected(db,id):raise HTTPException(422,'Connect YouTube before scheduling')
  v.scheduled_for=data.scheduled_for;v.status='queued_for_upload';queue_video(db,v.id,'youtube_publish');db.commit();return video_data(v)
@app.get('/api/stats')
def stats(id=Depends(auth)):
 with Session() as db:
  items=db.query(Video).filter_by(user_id=id).all()
  return dict(total=len(items),published=sum(v.status=='published' for v in items),scheduled=sum(v.status=='scheduled' for v in items),drafts=sum(v.status=='draft' for v in items),campaigns=db.query(Campaign).filter_by(user_id=id,active=True).count())
@app.put('/api/settings/postiz')
def postiz(data:PostizData,id=Depends(auth)):
 if not data.base_url.startswith('https://'):raise HTTPException(400,'HTTPS required')
 key=os.getenv('ENCRYPTION_KEY')
 if not key:raise HTTPException(503,'Set ENCRYPTION_KEY')
 with Session() as db:
  c=db.query(Credential).filter_by(user_id=id).first()
  if not c:c=Credential(user_id=id,base_url='',secret='');db.add(c)
  c.base_url=data.base_url.rstrip('/');c.secret=Fernet(key.encode()).encrypt(data.api_key.encode()).decode()
  db.commit()
 return {'connected':True}
@app.get('/api/channels')
async def channels(id=Depends(auth)):
 with Session() as db:
  c=db.query(Credential).filter_by(user_id=id).first()
  url=c.base_url if c else os.getenv('POSTIZ_URL','')
  key=Fernet(os.environ['ENCRYPTION_KEY'].encode()).decrypt(c.secret.encode()).decode() if c else os.getenv('POSTIZ_API_KEY','')
 if not url or not key:raise HTTPException(422,'Connect Postiz in Settings')
 async with httpx.AsyncClient(timeout=25) as client:
  resp=await client.get(url+'/public/v1/integrations',headers={'Authorization':key})
  resp.raise_for_status();return resp.json()
@app.get('/')
def home():
    if os.getenv('STUDIO_SMOKE_ONLY') == '1':
        return HTMLResponse('<html><head><title>Studio MCP test</title></head><body style="font:16px system-ui;max-width:680px;margin:10vh auto;padding:24px"><h1>Studio MCP staging</h1><p>This is a connectivity-only test. Account APIs, video generation, and Postiz publishing are disabled.</p><p>Health: <a href="/health">/health</a></p></body></html>')
    return HTMLResponse(Path('index.html').read_text())
