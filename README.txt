ANONYMOUS™ MINI — UPDATE PACKAGE

Files:
- bot.py: refreshed Telegram bot code with menu buttons and fun commands
- requirements.txt: Python dependencies

IMPORTANT BEFORE UPLOADING
1. This is a replacement bot.py, not a patch against your original source. It includes the main menu, Help, Commands, About, Add to Group, fun commands, optional AI, basic reply-based group moderation, /ping, and a Render health endpoint.
2. Your existing Render environment variable TOKEN must remain set.
3. To enable AI, set OPENAI_API_KEY in Render → Environment. Optional OPENAI_MODEL can be set to a model available to your OpenAI account.
4. Keep the Render start command as: python bot.py
5. The bot needs admin rights in groups for ban/mute/kick commands. Promote it to admin and grant the relevant permissions.
6. Warning counts are stored in memory and reset when the service restarts. This package does not include persistent coin balances or your previous advanced games/leaderboard features.
7. Render Free can still sleep/restart; a health endpoint and external monitor do not guarantee 24/7 uptime.

SAFE DEPLOYMENT
- First download/keep a copy of your current bot.py in GitHub so you can restore it.
- Replace bot.py and requirements.txt in the same GitHub repository.
- Commit the changes and wait for Render to redeploy.
- Check Render Logs, then test /start, /ping, /commands, /joke, /quiz and /ai.

Main menu buttons:
Help | Commands
About | Add to Group
