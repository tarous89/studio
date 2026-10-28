# Studio ✦

A B2B video studio with a pastel glass-lens dashboard, Python video renderer, scheduling database and private ChatGPT MCP plugin.

**Deployed staging:** https://studio-web-hz4l.onrender.com  
**Private plugin:** https://chatgpt.com/plugins/Plugin_00a7733291ac8191bdacb834c8dcb700  
**MCP endpoint:** https://studio-web-hz4l.onrender.com/mcp/

**Current status:** The code deploys on Render and CI tests syntax, OAuth/MCP discovery, and generation of a playable MP4. It is an **invitation-only first release**. Real YouTube posting has not been tested or authorized because the Render PostgreSQL connection and Google OAuth client credentials still require owner-side configuration. Read **[the go-live checklist](docs/GO_LIVE.md)** before using the live service.

## Implemented

- Topic and audience-specific campaigns, style, duration, manual/daily/weekly/monthly generation and opt-in automatic publishing.
- Script generation via optional OpenAI Responses API, with a safe, factual-template fallback when no model key is configured.
- Original portrait (9:16) Pillow+FFmpeg renderer with audio via `espeak-ng`. Two palettes: cutout and duotone. These are visual alternatives, **not** full ports of the user-provided reference animation archives.
- Video queue, approval, MP4 preview/download, and upload/scheduling flow for a connected YouTube channel via Google's OAuth API.
- Database-backed private OAuth for MCP tools: `studio_connection_status`, `list_campaigns`, `create_campaign`, `list_videos`, `create_video_draft`, `render_video_draft`, `approve_video_draft`, `check_youtube_connection`, `publish_approved_video_to_youtube`, and `run_campaign_now`.
- Invite-only registration, password hashing, Fernet-encrypted YouTube refresh tokens, OAuth client registration, token lifecycle and PKCE.
- GitHub Actions CI covering Python syntax, browser JS syntax, MP4 rendering and MCP authorization routing.

## Setup

```bash
pip install -r requirements.txt
STUDIO_SMOKE_ONLY=0 uvicorn run:app --reload
```

For the rendering engine install `ffmpeg`, `espeak-ng` and DejaVu Sans fonts. The Dockerfile includes those packages.

Environment settings are documented in `.env.example`. Do not commit credentials, database URLs, YouTube tokens or encryption keys.

**Read [docs/GO_LIVE.md](docs/GO_LIVE.md)** to attach the existing `studio-db` PostgreSQL instance to `studio-web`, create a Google Cloud OAuth web client, authorize your YouTube channel, and test real publishing.

## Deployment and limitations

The Docker web service runs an in-process background queue loop. Generated assets currently use capped PostgreSQL binary storage, not dedicated object storage. A dedicated worker, managed queue, S3-compatible storage, high-availability job recovery, additional platforms, polished voice actors and production-grade monitoring are future improvements.

The repo does not vendor or modify AGPL-licensed Postiz source. For the first YouTube release Studio calls the YouTube API directly; optional Postiz integration remains in the prototype but is not part of the verified production flow.

This is suitable for owner-supervised staging after configuration; it is not yet a fully audited multi-tenant commercial product.
