# Studio: go-live setup

Studio's code and private MCP plugin are deployed. **A real YouTube video cannot be published until you finish the Render database and Google OAuth setup below.** Keep your credentials in the provider dashboards, never in GitHub or ChatGPT messages.

## 1. Attach Studio PostgreSQL to Studio web

Studio's existing Render resources (Frankfurt; same workspace as Trialy, but separate):
- Web service: [studio-web](https://dashboard.render.com/web/srv-dbh0pgnlk1mc739l0mb0)
- PostgreSQL: [studio-db](https://dashboard.render.com/d/dpg-dbh0km7f3r2c73aqo92g-a)
- Public Studio URL: https://studio-web-hz4l.onrender.com

In Render:
1. Open **studio-db → Connect → Internal** and copy its *internal connection URL* (including credentials). It should point to the Studio database, **not** Trialy's database. Do not share the URL in chat.
2. Open **studio-web → Environment**, set **DATABASE_URL** to this internal connection URL, save and deploy. The application converts standard `postgresql://` URL prefixes to SQLAlchemy's Psycopg driver and creates the necessary tables.
3. Confirm **PUBLIC_BASE_URL** is `https://studio-web-hz4l.onrender.com`, **STUDIO_SMOKE_ONLY** is `0`, and **ENCRYPTION_KEY** is already configured. Do not change encryption keys after connecting YouTube unless encrypted credentials have been migrated.
4. Open `https://studio-web-hz4l.onrender.com/health` and verify `{"status":"ok"}`. Create your Studio account with an email and password; no invitation code is needed.

Do not start storing real videos or Google refresh tokens before `DATABASE_URL` is set. With no database connection, temporary local SQLite data is erased on a web service replacement.

The checked-in `render.yaml` illustrates the intended `fromDatabase` wiring, but because the resources already exist, manually setting the web service's `DATABASE_URL` is safer than applying a second Blueprint without reviewing its resource adoption.

## 2. Configure YouTube Data API credentials

In [Google Cloud Console](https://console.cloud.google.com/):
1. Create/select a Google Cloud project controlled by you. Enable **YouTube Data API v3**.
2. Configure the OAuth consent screen, requested scope `https://www.googleapis.com/auth/youtube.upload`, and any testing users required by Google's publishing/verification status.
3. Create an OAuth 2.0 **Web application** client. Register this exact authorized redirect URI:

   `https://studio-web-hz4l.onrender.com/api/youtube/callback`

4. In **studio-web → Environment**, add `YOUTUBE_CLIENT_ID` and `YOUTUBE_CLIENT_SECRET` as secrets. Save and let Render redeploy.
5. In the Studio dashboard, sign in, open **Settings → Connect YouTube**, and select the YouTube channel you wish to authorize. This consent must happen interactively; ChatGPT cannot perform it on your behalf.
6. Verify **Settings** reports YouTube connected. Publishing and scheduling require approved MP4s and the authorized Google account. Google may impose API quotas or verification/audit requirements.

## 3. Test the complete user journey

1. Create a new campaign: target audience, topic, style, duration, frequency, channel YouTube.
2. Create a draft video; Studio queues MP4 rendering.
3. Check Videos for `ready`. Preview it in the dashboard and click **Approve**.
4. Click **Publish on YouTube** (or set a future date). This queues the external upload; the video is only confirmed published/scheduled after YouTube returns a video ID and Studio stores its link.
5. Open YouTube Studio to verify the uploaded video's privacy, scheduled date and caption.

## 4. ChatGPT plugin

Private plugin: https://chatgpt.com/plugins/Plugin_00a7733291ac8191bdacb834c8dcb700

Select **Connect** and authorize with the same Studio account through Studio's PKCE OAuth page. Check the `studio_connection_status` tool first, then list campaigns, create a draft, render it, approve it, and publish with explicit instruction.

The plugin is intentionally private. Neither saving the plugin nor enabling OAuth creates a YouTube connection automatically.

## Limitations of this first release

- Renderer: original 9:16 sliding text-card first version with cutout and duotone palettes, not exact ports of the two original video-animation packages.
- Voice: system TTS. Natural-sounding paid voices and licensed music have not been added.
- Video files: stored in PostgreSQL as bounded binary attachments, capped at 20 MB/video. Production scale should move to S3-compatible object storage.
- Queue: a background task within the Render web service, not a separate high-availability Render worker. Stuck jobs can require manual recovery after a crash. Do not use it yet for high-volume unsupervised publishing.
- The OpenAI writer uses `OPENAI_API_KEY` if configured. If unavailable, Studio uses a deterministic template and **does not** invent company-specific facts.
- YouTube upload logic follows the official resumable upload API but cannot be verified end-to-end until your Google credentials and channel consent are configured.
- The dashboard and API are currently invitation-only and suitable for testing, not yet an audited multi-tenant commercial launch.

## ChatGPT-first rendering (Studio plugin v0.2.0)

The fastest path is to install the private Studio plugin, create a Studio account with email/password (no invite), and authorize the plugin using Studio OAuth. Ask ChatGPT for a specific B2B video. ChatGPT will write the narration, on-screen scenes, layout/accent preferences and YouTube metadata, then call `create_and_render_video` to queue Studio's MP4 renderer. `get_video_generation_status` returns an expiring preview link after completion. The signed MP4 preview expires after 15 minutes but can be refreshed from the plugin or opened in Studio.

YouTube account authorization is **not required** for generation or preview. Upload/scheduling still requires Google OAuth and an explicitly approved video. Studio does not independently generate its own weekly scripts: cadence is a campaign preference, not a scheduled ChatGPT invocation. Do not mistake a queued render or upload for a completed publication.

The public health check `/health` reports `storage: postgres` when the service is using its separate Render PostgreSQL database. If it says `temporary_sqlite`, data and MP4s may be lost on redeployment; set the Studio web service's `DATABASE_URL` from the existing Studio database's internal connection string (never share it in chat).
