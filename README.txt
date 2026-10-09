Anonymous™ Mini update (Gemini AI + riddles + group replies)

Files:
- bot.py: replacement bot code
- requirements.txt: required Python packages

Before deploying:
1. Replace bot.py in your GitHub repository with this bot.py.
2. Replace requirements.txt with the included requirements.txt.
3. In Render > Environment, keep TOKEN (your Telegram bot token).
4. Add GEMINI_API_KEY with your Google AI Studio key.
5. Optional: set GEMINI_MODEL to a Gemini model available to your account. Default is gemini-2.5-flash.
6. Remove OPENAI_API_KEY if you no longer use it (not required).
7. Commit changes and redeploy on Render.

Safety: never publish TOKEN or GEMINI_API_KEY in GitHub.

Notes:
- /riddle now hides the answer and checks the next message from the same user in the same chat.
- In groups, AI replies when someone mentions Anonymous or replies to the bot's message.
- Riddle state is in memory and resets when the service restarts.


FOUNDER FEATURE
- The founder name is shown as a clickable link to https://t.me/i_amanonymous.
- Founder questions are answered directly before the general group AI handler.
- Keep TOKEN and GEMINI_API_KEY in Render Environment variables; never commit secrets.
- This package is based on the Gemini-enabled Anonymous Mini update.
