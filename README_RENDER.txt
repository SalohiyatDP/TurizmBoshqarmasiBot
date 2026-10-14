NAMANGAN TURIZM TELEGRAM BOT — RENDER FIX

The original repository's Render build failed because requirements.txt was missing.
The bot uses only Python's standard library, so requirements.txt is intentionally
comment-only. render_start.py opens the HTTP port required by Render Web Services
and runs the existing Telegram polling bot.

Add BOTH files to the root of the existing GitHub repository:
  - requirements.txt
  - render_start.py

In Render > your service > Settings / Build & Deploy, use:
  Build Command: pip install -r requirements.txt
  Start Command: python render_start.py

In Render > Environment, add:
  BOT_TOKEN = your token from @BotFather
  ADMIN_CHAT_ID = the destination Telegram group ID (e.g. -1001234567890)

Do not commit the real bot token or a .env file to GitHub.

IMPORTANT LIMITATIONS:
- Render Free Web Services sleep after 15 minutes without inbound HTTP/WebSocket
  traffic, and the service filesystem is ephemeral. Local SQLite data in data/
  can be lost on spin-down/restart/redeploy. This setup is suitable for testing,
  not guaranteed 24/7 production use or durable storage.
- Test the deployment logs after pushing these files to GitHub.
