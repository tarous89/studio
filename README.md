# Studio ✦

A B2B video studio with a pastel glass-lens dashboard, Python video renderer, scheduling database and private ChatGPT MCP plugin.

**Deployed staging:** https://studio-web-hz4l.onrender.com  
**Private plugin:** https://chatgpt.com/plugins/Plugin_00a7733291ac8191bdacb834c8dcb700  
**MCP endpoint:** https://studio-web-hz4l.onrender.com/mcp/

**Current status:** The code deploys on Render and CI tests syntax, OAuth/MCP discovery, ChatGPT-authored scenes, signed MP4 previews, and actual rendering. It is an **authenticated first release without an invitation code**. Real YouTube posting has not been tested or authorized because the Render PostgreSQL connection and Google OAuth client credentials still require owner-side configuration. Read **[the go-live checklist](docs/GO_LIVE.md)** before using the live service.

## Implemented

- Topic and audience-specific campaigns, style, duration, manual/daily/weekly/monthly generation and opt-in automatic publishing.
- Script generation via optional OpenAI Responses API, with a safe, factual-template fallback when no model key is configured.
- Original portrait (9:16) Pillow+FFmpeg renderer with audio via `espeak-ng`. Two palettes: cutout and duotone. These are visual alternatives, **not** full ports of the user-provided reference animation archives.
- Video queue, approval, MP4 preview/download, and upload/scheduling flow for a connected YouTube channel via Google's OAuth API.
- Database-backed private OAuth for MCP tools: `studio_connection_status`, `list_campaigns`, `create_campaign`, `list_videos`, `create_video_draft`, `render_video_draft`, `approve_video_draft`, `check_youtube_connection`, `publish_approved_video_to_youtube`, and `run_campaign_now`.
- Email/password registration without an invite code, password hashing, Fernet-encrypted YouTube refresh tokens, OAuth client registration, token lifecycle and PKCE.
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

## ChatGPT-first workflow

Sign up on the Studio Render URL with an email and password. No invite code is required. Install the private Studio plugin and connect it using Studio OAuth. Tell ChatGPT the intended topic, audience and style. ChatGPT authors a narration and 2-12 on-screen scenes with headline, quote, stat or list layout and coral, mint, lilac or gold accent, and sends the plan with `create_and_render_video`. Studio renders a 9:16 MP4, not a fabricated result. The plugin's `get_video_generation_status` tool returns a temporary signed preview URL (15-minute lifetime) after a successful render. Publishing is a separate, authorized action through YouTube OAuth. The app does not silently replace ChatGPT content with template scripts on recurring schedules.

Each Studio account has a limit of 30 ChatGPT-submitted videos in a rolling 24-hour window. This is a protective staging limit, not a billing plan. A separate Render PostgreSQL connection is strongly recommended for durable video storage.
