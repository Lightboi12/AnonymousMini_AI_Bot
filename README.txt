ANONYMOUS™ MINI — ADVANCED MEMORY + GROUP ADMIN UPDATE

FILES
- bot.py: updated bot with persistent AI history/memories and expanded group admin commands.
- requirements.txt: dependencies.

RENDER ENVIRONMENT
Required:
- TOKEN = your Telegram bot token
- GEMINI_API_KEY = your Gemini API key

Optional:
- GEMINI_MODEL (default: gemini-2.5-flash)
- GEMINI_FALLBACK_MODEL (default: gemini-2.5-flash-lite)
- MEMORY_DB_PATH (optional custom SQLite database path)

IMPORTANT — PERSISTENT MEMORY ON RENDER
The bot uses SQLite for conversation history, remembered facts, and warning counts.
To keep this data through redeploys, attach a Render Persistent Disk to the service and set its mount path to:
  /var/data
The bot will then use /var/data/anonymous_memory.sqlite3 automatically. Without a persistent disk, local files may be lost on redeploy/replacement.

AI MEMORY COMMANDS
- /memory — view facts Anonymous™ Mini saved for you in this chat
- /forgetme — delete your AI history and saved facts for yourself in this chat

GROUP ADMIN COMMANDS
Use a reply to the target's message unless the command says otherwise. The bot must be an admin with the appropriate Telegram permissions.
- /ban — ban a replied-to user
- /unban USER_ID — unban by numeric Telegram user ID
- /kick — remove a replied-to user
- /mute [minutes] — mute a replied-to user; defaults to 10 minutes
- /unmute — unmute a replied-to user
- /warn — warn a user; 3 warnings trigger a kick
- /warnings — check a user's warning count
- /resetwarnings — clear a user's warning count
- /promote — promote a replied-to user with standard moderation rights
- /demote — remove a replied-to user's admin rights
- /pin — pin a replied-to message
- /unpin — unpin the replied-to message, or the current pin if not replying
- /del — delete a replied-to message and the command
- /purge [1-50] — delete a replied-to message and following messages, up to 50
- /close or /lock — prevent members from sending messages
- /open or /unlock — restore common member messaging permissions
- /adminlist — list group admins
- /id — show chat ID and your/replied user's ID

NOTES
- Telegram may reject moderation actions if the bot lacks a required admin right, or if the target has higher/equal admin status.
- Long-term memory is separated by both Telegram chat and user, so users' context is not mixed between chats.
- The AI saves recent chat turns and a limited set of explicit self-descriptions. This is not perfect human-like memory; it recalls what has been stored and supplied to the model.
- Keep your bot token and API keys private. Never paste them into public chats or source repositories.
