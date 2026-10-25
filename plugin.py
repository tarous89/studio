"""ChatGPT MCP bridge for Studio. User supplies Studio bearer token explicitly."""
from mcp.server.fastmcp import FastMCP
from app import Session,Campaign,Video,Token,digest,app
from sqlalchemy import select
import json
mcp=FastMCP('Studio',stateless_http=True,json_response=True)
def owner(token):
 with Session() as db:
  row=db.get(Token,digest(token))
  if not row:raise ValueError('Invalid Studio token')
  return row.user_id
@mcp.tool()
def list_campaigns(token:str)->list[dict]:
 """List campaigns owned by the authenticated Studio user."""
 uid=owner(token)
 with Session() as db:return [{'id':c.id,'name':c.name,'topic':c.topic,'audience':c.audience,'frequency':c.frequency} for c in db.query(Campaign).filter_by(user_id=uid)]
@mcp.tool()
def list_videos(token:str)->list[dict]:
 """Show own draft and scheduled video records."""
 uid=owner(token)
 with Session() as db:return [{'id':v.id,'title':v.title,'status':v.status,'published_url':v.post_url} for v in db.query(Video).filter_by(user_id=uid).limit(100)]
@mcp.tool()
def create_draft(token:str,title:str,script:str,style:str='cutout',length:int=45)->dict:
 """Create an unpublished B2B video draft. Do not claim it has been rendered or posted."""
 uid=owner(token)
 if style not in ('cutout','duotone') or not 15<=length<=90:raise ValueError('Invalid parameters')
 with Session() as db:
  v=Video(user_id=uid,title=title[:160],script=script,style=style,length=length,status='draft',channels='[]')
  db.add(v);db.commit();return {'id':v.id,'status':'draft'}
app.mount('/mcp',mcp.streamable_http_app())
