import asyncio
import logging
import os
import random
import time
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ChatMember, BotCommand
)
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

# Anonymous™ Mini — refreshed menu, fun commands, and optional AI.
# Required Render environment variable: TOKEN
# Optional: OPENAI_API_KEY
# This app uses long polling plus a small HTTP health endpoint for Render.

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
log = logging.getLogger("anonymous-mini")

TOKEN = os.getenv("TOKEN", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
PORT = int(os.getenv("PORT", "10000"))

if not TOKEN:
    raise RuntimeError("Missing TOKEN environment variable. Add your Telegram bot token in Render → Environment.")

bot = Bot(token=TOKEN)
dp = Dispatcher()
started_at = time.time()
bot_username = ""
ai_client = None

if OPENAI_API_KEY:
    try:
        from openai import AsyncOpenAI
        ai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    except Exception:
        log.exception("OpenAI client could not be initialized. AI commands will be unavailable.")


def main_menu() -> InlineKeyboardMarkup:
    add_group_url = f"https://t.me/{bot_username}?startgroup=true" if bot_username else "https://t.me/"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="❓ Help", callback_data="menu:help"),
            InlineKeyboardButton(text="📚 Commands", callback_data="menu:commands"),
        ],
        [
            InlineKeyboardButton(text="👑 About", callback_data="menu:about"),
            InlineKeyboardButton(text="➕ Add to Group", url=add_group_url),
        ],
    ])


def back_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Main Menu", callback_data="menu:home")],
        [
            InlineKeyboardButton(text="📚 Commands", callback_data="menu:commands"),
            InlineKeyboardButton(text="➕ Add to Group",
                                 url=f"https://t.me/{bot_username}?startgroup=true" if bot_username else "https://t.me/"),
        ],
    ])


HELP_TEXT = (
    "👋 <b>Welcome to Anonymous™ Mini Help!</b>\n\n"
    "I can help with fun commands, quick games, group moderation, and AI chat (when enabled).\n\n"
    "• Use the buttons below to explore.\n"
    "• In groups, reply to a message when using moderation commands.\n"
    "• Group moderation commands generally require admin permissions.\n"
    "• AI requires the OPENAI_API_KEY environment variable."
)

COMMANDS_TEXT = (
    "📚 <b>ANONYMOUS™ MINI — COMMAND LIST</b>\n\n"
    "⚡ <b>Basic</b>\n"
    "/start — Open the main menu\n"
    "/help — Help and usage\n"
    "/commands — Show commands\n"
    "/about — About this bot\n"
    "/ping — Check bot response\n\n"
    "😂 <b>Fun</b>\n"
    "/joke — Random joke\n"
    "/fact — Random fact\n"
    "/quote — Motivational quote\n"
    "/roll — Roll a dice\n"
    "/coinflip — Flip a coin\n"
    "/8ball — Ask the magic 8-ball\n"
    "/compliment — Get a compliment\n"
    "/truth — Random truth question\n"
    "/dare — Random dare\n"
    "/riddle — Get a riddle\n"
    "/quiz — Quick multiple-choice quiz\n"
    "/ship @user @user — Fun compatibility score\n\n"
    "🤖 <b>AI</b>\n"
    "/ai your question — Ask AI (requires API key)\n"
    "In groups, mention <code>Anonymous</code> followed by your question.\n\n"
    "🛡️ <b>Group admins</b>\n"
    "/ban, /unban, /kick, /mute, /unmute, /warn, /warnings\n"
    "Use these in a group and reply to the person’s message."
)

ABOUT_TEXT = (
    "👑 <b>Anonymous™ Mini</b>\n\n"
    "Your personal mini bot — fast, simple, and easy to use.\n\n"
    "✨ Fun commands • Group tools • AI chat\n"
    "🛠️ Created by: @i_amanonymous\n\n"
    "Use the menu buttons to explore, or add me to your group."
)

JOKES = [
    "😂 Why did the computer go to the doctor? It had a virus!",
    "🤣 I told my Wi-Fi we needed space. Now it won't connect.",
    "😅 Why don't programmers like nature? Too many bugs.",
    "💀 My bank account is like an onion. Looking at it makes me cry.",
    "😂 I asked my phone for a joke. It showed me my screen time."
]
FACTS = [
    "🐙 Octopuses have three hearts.",
    "🍯 Honey can remain edible for a very long time when properly sealed.",
    "🦈 Sharks existed before trees.",
    "🌌 A day on Venus is longer than its year.",
    "🐝 Bees communicate the location of food through a waggle dance."
]
QUOTES = [
    "🔥 Small progress is still progress. Keep going.",
    "👑 Be consistent. Your future self will thank you.",
    "💯 Don't wait for motivation; build a habit.",
    "✨ Your beginning doesn't determine your ending.",
    "🚀 Learn, improve, repeat."
]
COMPLIMENTS = [
    "✨ You bring good energy wherever you go.",
    "👑 You are more capable than you sometimes realize.",
    "🔥 Your effort matters, even when nobody notices.",
    "💯 You have your own kind of greatness."
]
TRUTHS = [
    "What is one thing you wish people understood about you?",
    "What is the funniest thing you've done to impress someone?",
    "Who was your first crush?",
    "What is one secret talent you have?",
    "What is something you are proud of but rarely talk about?"
]
DARES = [
    "Send the group your best (clean) joke.",
    "Describe your mood using only three emojis.",
    "Give someone in the group a genuine compliment.",
    "Write a sentence without using the letter 'e'.",
    "Share one goal you want to achieve this month."
]
RIDDLES = [
    ("What has keys but can't open locks?", "A piano 🎹"),
    ("What gets wetter the more it dries?", "A towel 🧻"),
    ("What has a face and two hands but no arms or legs?", "A clock 🕒"),
    ("What has to be broken before you can use it?", "An egg 🥚"),
    ("What goes up but never comes down?", "Your age 🎂")
]
QUIZZES = [
    ("Which planet is known as the Red Planet?", ["Venus", "Mars", "Jupiter"], 1),
    ("How many sides does a hexagon have?", ["5", "6", "8"], 1),
    ("Which animal is the largest mammal?", ["Blue whale", "Elephant", "Giraffe"], 0),
    ("What is 9 × 7?", ["56", "63", "72"], 1),
    ("Which ocean is the largest?", ["Atlantic", "Indian", "Pacific"], 2),
]
EIGHT_BALL = [
    "🎱 Yes, definitely.", "🎱 No doubt about it.", "🎱 Ask again later.",
    "🎱 The signs point to yes.", "🎱 Better not tell you now.",
    "🎱 Very doubtful.", "🎱 It is possible.", "🎱 Focus and try again."
]

WARNINGS = {}  # In-memory warning counts; they reset if the service restarts.
MUTE_MINUTES_DEFAULT = 10


async def is_group_admin(message: Message) -> bool:
    if not message.chat or message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return False
    if not message.from_user:
        return False
    try:
        member = await bot.get_chat_member(message.chat.id, message.from_user.id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False


async def replied_target(message: Message):
    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("⚠️ Reply to the user's message first so I know who you mean.")
        return None
    target = message.reply_to_message.from_user
    if target.is_bot:
        await message.reply("🤖 I can't apply that action to a bot.")
        return None
    return target


async def require_admin(message: Message) -> bool:
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.reply("🛡️ This command only works in groups.")
        return False
    if not await is_group_admin(message):
        await message.reply("⛔ Only group admins can use this command.")
        return False
    return True


@dp.message(CommandStart())
async def start(message: Message):
    text = (
        "👋 <b>Welcome to Anonymous™ Mini!</b>\n\n"
        "🤖 Your personal mini bot is ready.\n"
        "⚡ Fast • Simple • Easy to use\n\n"
        "👤 Created by: @i_amanonymous\n"
        "💬 Use the buttons below to get started!"
    )
    await message.answer(text, reply_markup=main_menu())


@dp.message(Command("help"))
async def help_command(message: Message):
    await message.answer(HELP_TEXT, reply_markup=back_menu())


@dp.message(Command("commands"))
async def commands_command(message: Message):
    await message.answer(COMMANDS_TEXT, reply_markup=back_menu())


@dp.message(Command("about"))
async def about_command(message: Message):
    await message.answer(ABOUT_TEXT, reply_markup=back_menu())


@dp.callback_query(F.data.startswith("menu:"))
async def menu_callbacks(callback: CallbackQuery):
    action = callback.data.split(":", 1)[1]
    if action == "home":
        await callback.message.edit_text(
            "👋 <b>Welcome to Anonymous™ Mini!</b>\n\n"
            "🤖 Your personal mini bot is ready.\n"
            "⚡ Fast • Simple • Easy to use\n\n"
            "👤 Created by: @i_amanonymous\n"
            "💬 Use the buttons below to get started!",
            reply_markup=main_menu()
        )
    elif action == "help":
        await callback.message.edit_text(HELP_TEXT, reply_markup=back_menu())
    elif action == "commands":
        await callback.message.edit_text(COMMANDS_TEXT, reply_markup=back_menu())
    elif action == "about":
        await callback.message.edit_text(ABOUT_TEXT, reply_markup=back_menu())
    await callback.answer()


@dp.message(Command("ping"))
async def ping(message: Message):
    await message.answer("Anonymous™ is active🙂‍↕️")


@dp.message(Command("joke"))
async def joke(message: Message):
    await message.answer(random.choice(JOKES))


@dp.message(Command("fact"))
async def fact(message: Message):
    await message.answer(random.choice(FACTS))


@dp.message(Command("quote"))
async def quote(message: Message):
    await message.answer(random.choice(QUOTES))


@dp.message(Command("roll"))
async def roll(message: Message):
    await message.answer(f"🎲 You rolled: <b>{random.randint(1, 6)}</b>")


@dp.message(Command("coinflip"))
async def coinflip(message: Message):
    await message.answer(f"🪙 It's <b>{random.choice(['HEADS', 'TAILS'])}</b>!")


@dp.message(Command("magic8", "8ball"))
async def eightball(message: Message):
    question = message.text.partition(" ")[2].strip() if message.text else ""
    if not question:
        await message.answer("🎱 Ask a question, e.g. <code>/8ball Will I win?</code>")
        return
    await message.answer(random.choice(EIGHT_BALL))


@dp.message(Command("compliment"))
async def compliment(message: Message):
    await message.answer(random.choice(COMPLIMENTS))


@dp.message(Command("truth"))
async def truth(message: Message):
    await message.answer("🫣 <b>TRUTH:</b> " + random.choice(TRUTHS))


@dp.message(Command("dare"))
async def dare(message: Message):
    await message.answer("🔥 <b>DARE:</b> " + random.choice(DARES))


@dp.message(Command("riddle"))
async def riddle(message: Message):
    question, answer = random.choice(RIDDLES)
    await message.answer(f"🧩 <b>RIDDLE:</b> {question}\n\nReply with your guess! (Answer: <tg-spoiler>{answer}</tg-spoiler>)")


@dp.message(Command("quiz"))
async def quiz(message: Message):
    question, options, correct = random.choice(QUIZZES)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=option, callback_data=f"quiz:{correct}:{i}:{option}")]
        for i, option in enumerate(options)
    ])
    await message.answer("🧠 <b>QUICK QUIZ</b>\n\n" + question, reply_markup=keyboard)


@dp.callback_query(F.data.startswith("quiz:"))
async def quiz_answer(callback: CallbackQuery):
    try:
        _, correct_s, chosen_s, option = callback.data.split(":", 3)
        correct, chosen = int(correct_s), int(chosen_s)
        if chosen == correct:
            await callback.message.edit_text(f"✅ Correct! <b>{option}</b> — you earned 10,000 fun coins in spirit! 🪙")
        else:
            await callback.message.edit_text(f"❌ Not quite! You chose <b>{option}</b>. Try another /quiz.")
    except Exception:
        await callback.answer("This quiz has expired.", show_alert=True)
        return
    await callback.answer()


@dp.message(Command("ship"))
async def ship(message: Message):
    parts = (message.text or "").split()
    names = [p for p in parts[1:] if p.startswith("@")]
    if len(names) < 2:
        await message.answer("💞 Usage: <code>/ship @person1 @person2</code>")
        return
    score = random.randint(0, 100)
    emoji = "💖" if score >= 75 else ("💛" if score >= 40 else "💔")
    await message.answer(f"💘 Compatibility for {names[0]} + {names[1]}: <b>{score}%</b> {emoji}\n(Just for fun!)")


@dp.message(Command("ai"))
async def ai_command(message: Message):
    prompt = (message.text or "").partition(" ")[2].strip()
    if not prompt:
        await message.answer("🤖 Ask me something: <code>/ai explain black holes simply</code>")
        return
    if ai_client is None:
        await message.answer("⚠️ AI is not configured yet. Add OPENAI_API_KEY in Render → Environment to enable it.")
        return
    await ask_ai(message, prompt)


async def ask_ai(message: Message, prompt: str):
    if ai_client is None:
        return
    try:
        response = await ai_client.responses.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            input=[
                {"role": "system", "content": "You are Anonymous™ Mini, a friendly, concise Telegram assistant. Be helpful and fun."},
                {"role": "user", "content": prompt}
            ],
            max_output_tokens=500,
        )
        answer = response.output_text.strip() or "I couldn't form an answer just now. Try again."
        if len(answer) > 4000:
            answer = answer[:3900] + "\n\n…(message shortened)"
        await message.reply(answer)
    except Exception:
        log.exception("AI request failed")
        await message.reply("⚠️ AI couldn't answer just now. Please try again later.")


# Reply-based group moderation commands.
@dp.message(Command("ban"))
async def ban_command(message: Message):
    if not await require_admin(message): return
    target = await replied_target(message)
    if not target: return
    try:
        await bot.ban_chat_member(message.chat.id, target.id)
        await message.answer(f"🔨 Banned <a href=\"tg://user?id={target.id}\">{target.full_name}</a>.")
    except Exception as e:
        await message.answer(f"❌ I couldn't ban that user. Check my admin permissions.\n<code>{type(e).__name__}</code>")


@dp.message(Command("unban"))
async def unban_command(message: Message):
    if not await require_admin(message): return
    target = await replied_target(message)
    if not target: return
    try:
        await bot.unban_chat_member(message.chat.id, target.id, only_if_banned=True)
        await message.answer(f"✅ Unbanned {target.full_name} (if they were banned).")
    except Exception:
        await message.answer("❌ I couldn't unban that user. Check my admin permissions.")


@dp.message(Command("kick"))
async def kick_command(message: Message):
    if not await require_admin(message): return
    target = await replied_target(message)
    if not target: return
    try:
        await bot.ban_chat_member(message.chat.id, target.id)
        await bot.unban_chat_member(message.chat.id, target.id, only_if_banned=True)
        await message.answer(f"👢 Kicked {target.full_name}.")
    except Exception:
        await message.answer("❌ I couldn't kick that user. Check my admin permissions.")


@dp.message(Command("mute"))
async def mute_command(message: Message):
    if not await require_admin(message): return
    target = await replied_target(message)
    if not target: return
    try:
        from datetime import datetime, timedelta, timezone
        until = datetime.now(timezone.utc) + timedelta(minutes=MUTE_MINUTES_DEFAULT)
        from aiogram.types import ChatPermissions
        await bot.restrict_chat_member(
            message.chat.id, target.id, permissions=ChatPermissions(can_send_messages=False),
            until_date=until
        )
        await message.answer(f"🔇 Muted {target.full_name} for {MUTE_MINUTES_DEFAULT} minutes.")
    except Exception:
        await message.answer("❌ I couldn't mute that user. Check my admin permissions.")


@dp.message(Command("unmute"))
async def unmute_command(message: Message):
    if not await require_admin(message): return
    target = await replied_target(message)
    if not target: return
    try:
        from aiogram.types import ChatPermissions
        await bot.restrict_chat_member(
            message.chat.id, target.id,
            permissions=ChatPermissions(
                can_send_messages=True, can_send_audios=True, can_send_documents=True,
                can_send_photos=True, can_send_videos=True, can_send_video_notes=True,
                can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True,
                can_add_web_page_previews=True
            )
        )
        await message.answer(f"🔊 Unmuted {target.full_name}.")
    except Exception:
        await message.answer("❌ I couldn't unmute that user. Check my admin permissions.")


@dp.message(Command("warn"))
async def warn_command(message: Message):
    if not await require_admin(message): return
    target = await replied_target(message)
    if not target: return
    key = (message.chat.id, target.id)
    WARNINGS[key] = WARNINGS.get(key, 0) + 1
    count = WARNINGS[key]
    if count >= 3:
        try:
            await bot.ban_chat_member(message.chat.id, target.id)
            await bot.unban_chat_member(message.chat.id, target.id, only_if_banned=True)
            WARNINGS[key] = 0
            await message.answer(f"🚫 {target.full_name} reached 3 warnings and was kicked.")
        except Exception:
            await message.answer(f"⚠️ {target.full_name} has {count}/3 warnings. I couldn't kick them; check my admin permissions.")
    else:
        await message.answer(f"⚠️ Warned {target.full_name}: <b>{count}/3</b>. Three warnings trigger a kick.")


@dp.message(Command("warnings"))
async def warnings_command(message: Message):
    target = await replied_target(message)
    if not target: return
    count = WARNINGS.get((message.chat.id, target.id), 0)
    await message.answer(f"⚠️ {target.full_name} has <b>{count}/3</b> warning(s) in this running session.")


@dp.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}), F.text)
async def group_ai_mention(message: Message):
    text = message.text or ""
    if "anonymous" not in text.lower():
        return
    # Avoid responding to bot commands such as /commands.
    if text.lstrip().startswith("/"):
        return
    prompt = text
    if "anonymous" in prompt.lower():
        index = prompt.lower().find("anonymous")
        prompt = (prompt[:index] + prompt[index + len("anonymous"):]).strip(" ,:!?-\n")
    if prompt and ai_client is not None:
        await ask_ai(message, prompt)
    elif prompt and ai_client is None:
        await message.reply("🤖 AI is not configured yet. An admin can add OPENAI_API_KEY in Render → Environment.")


async def health_handler(request):
    return web.json_response({
        "status": "ok",
        "service": "Anonymous Mini",
        "uptime_seconds": int(time.time() - started_at),
        "telegram_bot": "initialized"
    })


async def start_http_server():
    app = web.Application()
    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info("Health web server listening on port %s", PORT)
    return runner


async def main():
    global bot_username
    me = await bot.get_me()
    bot_username = me.username or ""
    log.info("Starting @%s", bot_username)
    commands = [
        BotCommand(command="start", description="Open the main menu"),
        BotCommand(command="help", description="Get help"),
        BotCommand(command="commands", description="List commands"),
        BotCommand(command="about", description="About this bot"),
        BotCommand(command="ping", description="Check bot status"),
        BotCommand(command="joke", description="Get a random joke"),
        BotCommand(command="fact", description="Get a random fact"),
        BotCommand(command="quote", description="Get a motivational quote"),
        BotCommand(command="roll", description="Roll a dice"),
        BotCommand(command="coinflip", description="Flip a coin"),
        BotCommand(command="magic8", description="Ask the magic 8-ball"),
        BotCommand(command="compliment", description="Get a compliment"),
        BotCommand(command="truth", description="Truth question"),
        BotCommand(command="dare", description="Get a dare"),
        BotCommand(command="riddle", description="Get a riddle"),
        BotCommand(command="quiz", description="Play a quick quiz"),
        BotCommand(command="ai", description="Ask AI"),
    ]
    await bot.set_my_commands(commands)
    runner = await start_http_server()
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Anonymous™ Mini stopped.")
