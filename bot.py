import asyncio
import logging
import os
import random
import time
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from html import escape
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ChatMember, BotCommand, ChatPermissions
)
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.utils.chat_action import ChatActionSender

# Anonymous™ Mini — refreshed menu, fun commands, and optional AI.
# Required Render environment variable: TOKEN
# Optional: GEMINI_API_KEY
# This app uses long polling plus a small HTTP health endpoint for Render.

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
log = logging.getLogger("anonymous-mini")

TOKEN = os.getenv("TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY", "").strip()
PORT = int(os.getenv("PORT", "10000"))


# Founder identity: the displayed name is clickable and opens the Telegram profile.
FOUNDER_NAME = "𝐋 𝐎 𝐑 𝐃 ♰ 𝐀𝐍𝐎𝐍𝐘𝐌𝐎𝐔𝐒™"
FOUNDER_URL = "https://t.me/i_amanonymous"
FOUNDER_REPLY = (
    f'My founder is <a href="{FOUNDER_URL}">'
    f'{FOUNDER_NAME}</a> 👑🔥'
)

if not TOKEN:
    raise RuntimeError("Missing TOKEN environment variable. Add your Telegram bot token in Render → Environment.")

bot = Bot(token=TOKEN)
dp = Dispatcher()
started_at = time.time()
bot_username = ""
ai_client = None

if GEMINI_API_KEY:
    try:
        from google import genai
        ai_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception:
        log.exception("Gemini client could not be initialized. AI commands will be unavailable.")

# Active riddles are kept per chat and user while this process is running.
ACTIVE_RIDDLES = {}
# Short-lived conversational context, kept separately for each user in each chat.
AI_HISTORY = {}  # compatibility cache; persistent SQLite is the source of truth.
AI_HISTORY_TURNS = 12
MEMORY_DB_PATH = os.getenv(
    "MEMORY_DB_PATH",
    "/var/data/anonymous_memory.sqlite3" if os.path.isdir("/var/data") else "anonymous_memory.sqlite3",
)


def db_connect():
    connection = sqlite3.connect(MEMORY_DB_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    return connection


@contextmanager
def db_session():
    connection = db_connect()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_database():
    parent = os.path.dirname(os.path.abspath(MEMORY_DB_PATH))
    os.makedirs(parent, exist_ok=True)
    with db_session() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            role TEXT NOT NULL, content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_history_chat_user ON chat_history(chat_id, user_id, id)")
        conn.execute("""CREATE TABLE IF NOT EXISTS user_memories (
            chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            fact TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(chat_id, user_id, fact)
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS warnings (
            chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(chat_id, user_id)
        )""")


def load_history(chat_id: int, user_id: int, limit: int = 24):
    with db_session() as conn:
        rows = conn.execute(
            "SELECT role, content FROM chat_history WHERE chat_id=? AND user_id=? ORDER BY id DESC LIMIT ?",
            (chat_id, user_id, limit),
        ).fetchall()
    return [(row["role"], row["content"]) for row in reversed(rows)]


def save_history_turn(chat_id: int, user_id: int, prompt: str, answer: str):
    with db_session() as conn:
        conn.executemany(
            "INSERT INTO chat_history(chat_id,user_id,role,content) VALUES(?,?,?,?)",
            [(chat_id, user_id, "user", prompt[:6000]), (chat_id, user_id, "assistant", answer[:6000])],
        )
        # Keep the most recent 200 messages per chat/user; long-term facts are stored separately.
        conn.execute("""DELETE FROM chat_history WHERE chat_id=? AND user_id=? AND id NOT IN
            (SELECT id FROM chat_history WHERE chat_id=? AND user_id=? ORDER BY id DESC LIMIT 200)""",
            (chat_id, user_id, chat_id, user_id))


def extract_memory_facts(chat_id: int, user_id: int, text: str):
    """Store a few explicit, low-risk self-descriptions without making guesses."""
    import re
    patterns = [
        r"\bmy name is ([^.!?\n]{1,60})",
        r"\bcall me ([^.!?\n]{1,60})",
        r"\bi am from ([^.!?\n]{1,80})",
        r"\bi'm from ([^.!?\n]{1,80})",
        r"\bi study ([^.!?\n]{1,100})",
        r"\bi am learning ([^.!?\n]{1,100})",
        r"\bi'm learning ([^.!?\n]{1,100})",
        r"\bi work as ([^.!?\n]{1,100})",
        r"\bi like ([^.!?\n]{1,100})",
        r"\bi love ([^.!?\n]{1,100})",
        r"\bmy project is ([^.!?\n]{1,100})",
    ]
    lower = text.strip()
    facts = []
    for pattern in patterns:
        match = re.search(pattern, lower, flags=re.IGNORECASE)
        if match:
            value = match.group(1).strip(" ,;:")
            if value and len(value) <= 100:
                facts.append(text.strip()[:160])
                break
    if facts:
        with db_session() as conn:
            for fact in facts:
                conn.execute(
                    "INSERT OR IGNORE INTO user_memories(chat_id,user_id,fact) VALUES(?,?,?)",
                    (chat_id, user_id, fact),
                )
            # Limit saved facts so stale context cannot grow forever.
            conn.execute("""DELETE FROM user_memories WHERE rowid IN (
                SELECT rowid FROM user_memories WHERE chat_id=? AND user_id=?
                ORDER BY rowid DESC LIMIT -1 OFFSET 40)""", (chat_id, user_id))


def load_memories(chat_id: int, user_id: int):
    with db_session() as conn:
        rows = conn.execute(
            "SELECT fact FROM user_memories WHERE chat_id=? AND user_id=? ORDER BY rowid DESC LIMIT 40",
            (chat_id, user_id),
        ).fetchall()
    return [row["fact"] for row in reversed(rows)]


def warning_count(chat_id: int, user_id: int) -> int:
    with db_session() as conn:
        row = conn.execute("SELECT count FROM warnings WHERE chat_id=? AND user_id=?", (chat_id, user_id)).fetchone()
    return int(row["count"]) if row else 0


def set_warning_count(chat_id: int, user_id: int, count: int):
    with db_session() as conn:
        if count <= 0:
            conn.execute("DELETE FROM warnings WHERE chat_id=? AND user_id=?", (chat_id, user_id))
        else:
            conn.execute("INSERT INTO warnings(chat_id,user_id,count) VALUES(?,?,?) ON CONFLICT(chat_id,user_id) DO UPDATE SET count=excluded.count", (chat_id, user_id, count))


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
    "👋 Welcome to Anonymous™ Mini Help!\n\n"
    "I can help with fun commands, quick games, group moderation, and AI chat (when enabled).\n\n"
    "• Use the buttons below to explore.\n"
    "• In groups, reply to a message when using moderation commands.\n"
    "• Group moderation commands generally require admin permissions.\n"
    "• AI works with any configured OpenRouter, Groq, Gemini, or Cerebras API key.\n• Conversation history and saved facts are stored separately per user and chat."
)

COMMANDS_TEXT = (
    "📚 ANONYMOUS™ MINI — COMMAND LIST\n\n"
    "⚡ Basic\n"
    "/start — Open the main menu\n"
    "/help — Help and usage\n"
    "/commands — Show commands\n"
    "/about — About this bot\n"
    "/ping — Check bot response\n\n"
    "😂 Fun\n"
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
    "🤖 AI\n"
    "/ai your question — Ask AI (requires API key)\n"
    "/memory — View saved facts for this chat\n"
    "/forgetme — Delete your AI history and memories here\n\n"
    "🛡️ Group admins (reply to a user's message unless stated)\n"
    "/ban, /unban USER_ID, /kick, /mute [minutes], /unmute\n"
    "/warn, /warnings, /resetwarnings, /promote, /demote\n"
    "/pin, /unpin, /del, /purge [1-50], /close, /open, /adminlist, /id\n"
    "Bot must be an admin with the required permissions."
)

ABOUT_TEXT = (
    "👑 Anonymous™ Mini\n\n"
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
        "👋 Welcome to Anonymous™ Mini!\n\n"
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
            "👋 Welcome to Anonymous™ Mini!\n\n"
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
    await message.answer(f"🎲 You rolled: {random.randint(1, 6)}")


@dp.message(Command("coinflip"))
async def coinflip(message: Message):
    await message.answer(f"🪙 It's {random.choice(['HEADS', 'TAILS'])}!")


@dp.message(Command("magic8", "8ball"))
async def eightball(message: Message):
    question = message.text.partition(" ")[2].strip() if message.text else ""
    if not question:
        await message.answer("🎱 Ask a question, e.g. /8ball Will I win?")
        return
    await message.answer(random.choice(EIGHT_BALL))


@dp.message(Command("compliment"))
async def compliment(message: Message):
    await message.answer(random.choice(COMPLIMENTS))


@dp.message(Command("truth"))
async def truth(message: Message):
    await message.answer("🫣 TRUTH: " + random.choice(TRUTHS))


@dp.message(Command("dare"))
async def dare(message: Message):
    await message.answer("🔥 DARE: " + random.choice(DARES))


@dp.message(Command("riddle"))
async def riddle(message: Message):
    question, answer = random.choice(RIDDLES)
    ACTIVE_RIDDLES[(message.chat.id, message.from_user.id)] = (question, answer)
    await message.answer(f"🧩 RIDDLE: {question}\n\nReply with your guess! I'll tell you whether you're right. 🤔")


@dp.message(Command("quiz"))
async def quiz(message: Message):
    question, options, correct = random.choice(QUIZZES)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=option, callback_data=f"quiz:{correct}:{i}:{option}")]
        for i, option in enumerate(options)
    ])
    await message.answer("🧠 QUICK QUIZ\n\n" + question, reply_markup=keyboard)


@dp.callback_query(F.data.startswith("quiz:"))
async def quiz_answer(callback: CallbackQuery):
    try:
        _, correct_s, chosen_s, option = callback.data.split(":", 3)
        correct, chosen = int(correct_s), int(chosen_s)
        if chosen == correct:
            await callback.message.edit_text(f"✅ Correct! {option} — you earned 10,000 fun coins in spirit! 🪙")
        else:
            await callback.message.edit_text(f"❌ Not quite! You chose {option}. Try another /quiz.")
    except Exception:
        await callback.answer("This quiz has expired.", show_alert=True)
        return
    await callback.answer()


@dp.message(Command("ship"))
async def ship(message: Message):
    parts = (message.text or "").split()
    names = [p for p in parts[1:] if p.startswith("@")]
    if len(names) < 2:
        await message.answer("💞 Usage: /ship @person1 @person2")
        return
    score = random.randint(0, 100)
    emoji = "💖" if score >= 75 else ("💛" if score >= 40 else "💔")
    await message.answer(f"💘 Compatibility for {names[0]} + {names[1]}: {score}% {emoji}\n(Just for fun!)")


@dp.message(Command("ai"))
async def ai_command(message: Message):
    prompt = (message.text or "").partition(" ")[2].strip()
    if not prompt:
        await message.answer("🤖 Ask me something: /ai explain black holes simply")
        return
    if not any([GEMINI_API_KEY, OPENROUTER_API_KEY, GROQ_API_KEY, CEREBRAS_API_KEY]):
        await message.answer("⚠️ No AI provider is configured. Add at least one AI API key in Render → Environment.")
        return
    await ask_ai(message, prompt)


async def _chat_completion(provider: str, api_key: str, model: str, conversation: str) -> str:
    """Call a chat-completions provider using its OpenAI-compatible API."""
    import aiohttp

    endpoints = {
        "OpenRouter": "https://openrouter.ai/api/v1/chat/completions",
        "Groq": "https://api.groq.com/openai/v1/chat/completions",
        "Cerebras": "https://api.cerebras.ai/v1/chat/completions",
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if provider == "OpenRouter":
        headers["HTTP-Referer"] = "https://t.me/"
        headers["X-Title"] = "Anonymous Mini"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": conversation}],
        "temperature": 0.7,
    }
    timeout = aiohttp.ClientTimeout(total=45)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(endpoints[provider], headers=headers, json=payload) as response:
            body = await response.text()
            if response.status >= 400:
                # Include provider response in logs, but never expose API keys.
                raise RuntimeError(f"{provider} HTTP {response.status}: {body[:700]}")
            try:
                data = await response.json(content_type=None)
                return (data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
            except Exception as exc:
                raise RuntimeError(f"{provider} returned an unreadable response: {body[:300]}") from exc


async def ask_ai(message: Message, prompt: str):
    configured = any([GEMINI_API_KEY, OPENROUTER_API_KEY, GROQ_API_KEY, CEREBRAS_API_KEY])
    if not configured:
        await message.reply("⚠️ No AI provider is configured. Add at least one AI API key in Render → Environment.")
        return

    user_id = message.from_user.id if message.from_user else 0
    chat_id = message.chat.id
    extract_memory_facts(chat_id, user_id, prompt)
    history = load_history(chat_id, user_id)
    memories = load_memories(chat_id, user_id)

    system_instruction = (
        "You are Anonymous™ Mini, a smart, warm, witty, natural conversational AI in Telegram. "
        "Talk like a normal person, not a customer-support script. Use the conversation history and saved facts "
        "to understand follow-ups and recall details the user previously shared. Keep each user's context private "
        "to this chat; never claim to remember details that are not in the provided context. Answer directly and "
        "match the user's tone and language. Do not routinely end with 'Is there anything else I can help with?' "
        "Only ask a follow-up when genuinely needed. Do not output HTML tags. "
        "Your founder is 𝐋 𝐎 𝐑 𝐃 ♰ 𝐀𝐍𝐎𝐍𝐘𝐌𝐎𝐔𝐒™."
    )
    context = [system_instruction]
    if memories:
        context.append("Saved facts this user explicitly shared in this chat:\n- " + "\n- ".join(memories))
    if history:
        context.append("Recent conversation:\n" + "\n".join(
            ("User: " if role == "user" else "Anonymous™ Mini: ") + content
            for role, content in history[-(AI_HISTORY_TURNS * 2):]
        ))
    context.append("New user message: " + prompt)
    conversation = "\n\n".join(context)

    # Each configured provider is tried once in sequence. A quota/rate-limit,
    # auth, model, or temporary failure advances to the next provider.
    providers = []
    if OPENROUTER_API_KEY:
        providers.append(("OpenRouter", OPENROUTER_API_KEY,
                          os.getenv("OPENROUTER_MODEL", "openai/gpt-oss-20b").strip()))
    if GROQ_API_KEY:
        providers.append(("Groq", GROQ_API_KEY,
                          os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()))
    if GEMINI_API_KEY and ai_client is not None:
        providers.append(("Gemini", GEMINI_API_KEY,
                          os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()))
    if CEREBRAS_API_KEY:
        providers.append(("Cerebras", CEREBRAS_API_KEY,
                          os.getenv("CEREBRAS_MODEL", "llama-3.3-70b").strip()))

    errors = []
    for provider, api_key, model in providers:
        try:
            log.info("Trying AI provider %s (model %s)", provider, model)
            async with ChatActionSender(bot=bot, chat_id=chat_id, action="typing"):
                if provider == "Gemini":
                    response = await ai_client.aio.models.generate_content(model=model, contents=conversation)
                    answer = (getattr(response, "text", None) or "").strip()
                else:
                    answer = await _chat_completion(provider, api_key, model, conversation)
            if not answer:
                raise RuntimeError(f"{provider} returned an empty answer")
            if len(answer) > 4000:
                answer = answer[:3900] + "\n\n…(message shortened)"
            await message.reply(answer)
            save_history_turn(chat_id, user_id, prompt, answer)
            log.info("AI response succeeded with %s", provider)
            return
        except Exception as exc:
            errors.append(f"{provider}: {exc}")
            log.warning("AI provider %s failed; trying next provider. Error: %s", provider, exc)

    log.error("All configured AI providers failed: %s", " | ".join(errors))
    await message.reply(
        "⚠️ All configured AI providers are temporarily unavailable or have reached their limits. "
        "Please try again later."
    )


@dp.message(Command("memory"))
async def memory_command(message: Message):
    if not message.from_user:
        return
    facts = load_memories(message.chat.id, message.from_user.id)
    if not facts:
        await message.reply("🧠 I haven't saved any personal facts from this chat yet. Keep chatting naturally.")
        return
    await message.reply("🧠 Things I remember from this chat:\n• " + "\n• ".join(facts[-20:]))


@dp.message(Command("forgetme"))
async def forgetme_command(message: Message):
    if not message.from_user:
        return
    with db_session() as conn:
        conn.execute("DELETE FROM chat_history WHERE chat_id=? AND user_id=?", (message.chat.id, message.from_user.id))
        conn.execute("DELETE FROM user_memories WHERE chat_id=? AND user_id=?", (message.chat.id, message.from_user.id))
    AI_HISTORY.pop((message.chat.id, message.from_user.id), None)
    await message.reply("🧹 Done — I've cleared your saved AI conversation and facts for this chat.")


# Group moderation: only actual group admins can invoke these commands.
async def admin_target(message: Message):
    target = await replied_target(message)
    if target and message.from_user and target.id == message.from_user.id:
        await message.reply("⚠️ You can't use that command on yourself.")
        return None
    return target


@dp.message(Command("ban"))
async def ban_command(message: Message):
    if not await require_admin(message): return
    target = await admin_target(message)
    if not target: return
    try:
        await bot.ban_chat_member(message.chat.id, target.id)
        await message.answer(f'🔨 Banned <a href="tg://user?id={target.id}">{escape(target.full_name)}</a>.', parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ Ban failed. Check that I'm an admin with ban rights. ({type(e).__name__})")


@dp.message(Command("unban"))
async def unban_command(message: Message):
    if not await require_admin(message): return
    args = (message.text or "").split()
    target_id = None
    if len(args) > 1 and args[1].lstrip("-").isdigit():
        target_id = int(args[1])
    elif message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
    if target_id is None:
        await message.reply("Usage: /unban USER_ID (or reply to a user's message if available).")
        return
    try:
        await bot.unban_chat_member(message.chat.id, target_id, only_if_banned=True)
        await message.answer(f"✅ Unbanned user ID {target_id} (if they were banned).")
    except Exception:
        await message.answer("❌ Unban failed. Check my admin permissions and the user ID.")


@dp.message(Command("kick"))
async def kick_command(message: Message):
    if not await require_admin(message): return
    target = await admin_target(message)
    if not target: return
    try:
        await bot.ban_chat_member(message.chat.id, target.id)
        await bot.unban_chat_member(message.chat.id, target.id, only_if_banned=True)
        await message.answer(f"👢 Kicked {escape(target.full_name)}.")
    except Exception:
        await message.answer("❌ Kick failed. Check my admin permissions.")


@dp.message(Command("mute"))
async def mute_command(message: Message):
    if not await require_admin(message): return
    target = await admin_target(message)
    if not target: return
    args = (message.text or "").split()
    minutes = MUTE_MINUTES_DEFAULT
    if len(args) > 1:
        try: minutes = max(1, min(10080, int(args[1])))
        except ValueError:
            await message.reply("Usage: reply with /mute [minutes]. Example: /mute 30")
            return
    try:
        until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        await bot.restrict_chat_member(message.chat.id, target.id, permissions=ChatPermissions(can_send_messages=False), until_date=until)
        await message.answer(f"🔇 Muted {escape(target.full_name)} for {minutes} minute(s).")
    except Exception:
        await message.answer("❌ Mute failed. Check that I'm an admin with restrict-member rights.")


@dp.message(Command("unmute"))
async def unmute_command(message: Message):
    if not await require_admin(message): return
    target = await admin_target(message)
    if not target: return
    try:
        await bot.restrict_chat_member(message.chat.id, target.id, permissions=ChatPermissions(
            can_send_messages=True, can_send_audios=True, can_send_documents=True, can_send_photos=True,
            can_send_videos=True, can_send_video_notes=True, can_send_voice_notes=True, can_send_polls=True,
            can_send_other_messages=True, can_add_web_page_previews=True
        ))
        await message.answer(f"🔊 Unmuted {escape(target.full_name)}.")
    except Exception:
        await message.answer("❌ Unmute failed. Check my admin permissions.")


@dp.message(Command("warn"))
async def warn_command(message: Message):
    if not await require_admin(message): return
    target = await admin_target(message)
    if not target: return
    count = warning_count(message.chat.id, target.id) + 1
    set_warning_count(message.chat.id, target.id, count)
    reason = (message.text or "").partition(" ")[2].strip()
    suffix = f" Reason: {escape(reason)}" if reason else ""
    if count >= 3:
        try:
            await bot.ban_chat_member(message.chat.id, target.id)
            await bot.unban_chat_member(message.chat.id, target.id, only_if_banned=True)
            set_warning_count(message.chat.id, target.id, 0)
            await message.answer(f"🚫 {escape(target.full_name)} reached 3 warnings and was kicked.{suffix}")
        except Exception:
            await message.answer(f"⚠️ {escape(target.full_name)} has {count}/3 warnings, but I couldn't kick them. Check my permissions.")
    else:
        await message.answer(f"⚠️ Warned {escape(target.full_name)}: {count}/3.{suffix}")


@dp.message(Command("warnings"))
async def warnings_command(message: Message):
    if not await require_admin(message): return
    target = await admin_target(message)
    if not target: return
    await message.answer(f"⚠️ {escape(target.full_name)} has {warning_count(message.chat.id, target.id)}/3 warning(s).")


@dp.message(Command("resetwarnings"))
async def resetwarnings_command(message: Message):
    if not await require_admin(message): return
    target = await admin_target(message)
    if not target: return
    set_warning_count(message.chat.id, target.id, 0)
    await message.answer(f"✅ Warnings reset for {escape(target.full_name)}.")


@dp.message(Command("promote"))
async def promote_command(message: Message):
    if not await require_admin(message): return
    target = await admin_target(message)
    if not target: return
    try:
        await bot.promote_chat_member(message.chat.id, target.id, can_manage_chat=True, can_delete_messages=True,
            can_manage_video_chats=True, can_restrict_members=True, can_promote_members=False,
            can_change_info=False, can_invite_users=True, can_pin_messages=True, can_post_stories=False,
            can_edit_stories=False, can_delete_stories=False)
        await message.answer(f"👑 Promoted {escape(target.full_name)} with standard moderation rights. Adjust specific rights in Telegram if needed.")
    except Exception:
        await message.answer("❌ Promote failed. I need admin status and promote-members permission; Telegram may restrict which rights I can grant.")


@dp.message(Command("demote"))
async def demote_command(message: Message):
    if not await require_admin(message): return
    target = await admin_target(message)
    if not target: return
    try:
        await bot.promote_chat_member(message.chat.id, target.id, can_manage_chat=False, can_delete_messages=False,
            can_manage_video_chats=False, can_restrict_members=False, can_promote_members=False,
            can_change_info=False, can_invite_users=False, can_pin_messages=False, can_post_stories=False,
            can_edit_stories=False, can_delete_stories=False)
        await message.answer(f"⬇️ Demoted {escape(target.full_name)} if Telegram permits this action.")
    except Exception:
        await message.answer("❌ Demote failed. Check my promote-members permission and target's admin status.")


@dp.message(Command("pin"))
async def pin_command(message: Message):
    if not await require_admin(message): return
    if not message.reply_to_message:
        await message.reply("Reply to a message with /pin."); return
    try:
        await bot.pin_chat_message(message.chat.id, message.reply_to_message.message_id)
        await message.answer("📌 Message pinned.")
    except Exception:
        await message.answer("❌ Pin failed. Check my pin-message permission.")


@dp.message(Command("unpin"))
async def unpin_command(message: Message):
    if not await require_admin(message): return
    try:
        if message.reply_to_message:
            await bot.unpin_chat_message(message.chat.id, message.reply_to_message.message_id)
        else:
            await bot.unpin_chat_message(message.chat.id)
        await message.answer("📌 Message unpinned.")
    except Exception:
        await message.answer("❌ Unpin failed. Check my pin-message permission.")


@dp.message(Command("del", "delete"))
async def delete_command(message: Message):
    if not await require_admin(message): return
    if not message.reply_to_message:
        await message.reply("Reply to a message with /del."); return
    try:
        await bot.delete_message(message.chat.id, message.reply_to_message.message_id)
        try: await message.delete()
        except Exception: pass
    except Exception:
        await message.answer("❌ Delete failed. Check my delete-message permission; old messages may be undeletable.")


@dp.message(Command("purge"))
async def purge_command(message: Message):
    if not await require_admin(message): return
    if not message.reply_to_message:
        await message.reply("Reply to the first message to remove, then use /purge [1-50]."); return
    args = (message.text or "").split()
    try: count = max(1, min(50, int(args[1]))) if len(args) > 1 else 10
    except ValueError:
        await message.reply("Usage: reply to a message and use /purge [1-50]."); return
    first_id = message.reply_to_message.message_id
    deleted = 0
    for mid in range(first_id, min(first_id + count, message.message_id)):
        try:
            await bot.delete_message(message.chat.id, mid); deleted += 1
        except Exception:
            pass
    try: await message.delete()
    except Exception: pass
    status = await message.answer(f"🧹 Purge finished: deleted {deleted} message(s).")
    await asyncio.sleep(4)
    try: await status.delete()
    except Exception: pass


@dp.message(Command("close", "lock"))
async def close_command(message: Message):
    if not await require_admin(message): return
    try:
        await bot.set_chat_permissions(message.chat.id, ChatPermissions(can_send_messages=False))
        await message.answer("🔒 Group chat locked for members.")
    except Exception:
        await message.answer("❌ Lock failed. Check my change-chat-permissions rights.")


@dp.message(Command("open", "unlock"))
async def open_command(message: Message):
    if not await require_admin(message): return
    try:
        await bot.set_chat_permissions(message.chat.id, ChatPermissions(
            can_send_messages=True, can_send_audios=True, can_send_documents=True, can_send_photos=True,
            can_send_videos=True, can_send_video_notes=True, can_send_voice_notes=True, can_send_polls=True,
            can_send_other_messages=True, can_add_web_page_previews=True, can_invite_users=True,
            can_pin_messages=False, can_change_info=False
        ))
        await message.answer("🔓 Group chat unlocked. Note: Telegram applies the permissions you set here as group defaults.")
    except Exception:
        await message.answer("❌ Unlock failed. Check my change-chat-permissions rights.")


@dp.message(Command("adminlist"))
async def adminlist_command(message: Message):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.reply("Use this in a group."); return
    try:
        admins = await bot.get_chat_administrators(message.chat.id)
        lines = [f"• {escape(a.user.full_name)} (@{escape(a.user.username)})" if a.user.username else f"• {escape(a.user.full_name)}" for a in admins]
        await message.answer("👑 Group admins:\n" + "\n".join(lines))
    except Exception:
        await message.answer("❌ Couldn't fetch the admin list.")


@dp.message(Command("id"))
async def id_command(message: Message):
    lines = [f"Chat ID: {message.chat.id}"]
    if message.reply_to_message and message.reply_to_message.from_user:
        u = message.reply_to_message.from_user
        lines.append(f"User ID: {u.id}")
    elif message.from_user:
        lines.append(f"Your user ID: {message.from_user.id}")
    await message.reply("🆔 " + "\n".join(lines))


@dp.message(
    F.text
    & (
        F.text.lower().contains("who is your founder")
        | F.text.lower().contains("who's your founder")
        | F.text.lower().contains("who is your creator")
        | F.text.lower().contains("who created you")
        | F.text.lower().contains("who made you")
        | F.text.lower().contains("who owns you")
        | F.text.lower().contains("who is your owner")
    )
)
async def founder_handler(message: Message):
    await message.answer(
        FOUNDER_REPLY,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@dp.message(F.text)
async def game_and_group_ai_handler(message: Message):
    text = (message.text or "").strip()
    if not text or text.startswith("/") or not message.from_user:
        return

    # Check a user's answer to their active riddle first.
    key = (message.chat.id, message.from_user.id)
    active = ACTIVE_RIDDLES.get(key)
    if active:
        question, answer = active
        normalized = "".join(ch.lower() for ch in text if ch.isalnum())
        expected = "".join(ch.lower() for ch in answer.split("🥚")[0].split("🎹")[0].split("🧻")[0].split("🕒")[0].split("🎂")[0] if ch.isalnum())
        # Compare the main answer words, ignoring punctuation and emoji.
        answer_words = answer.lower().split()
        accepted = ["".join(ch for ch in word.lower() if ch.isalnum()) for word in answer_words]
        accepted_text = "".join(ch for ch in answer.lower() if ch.isalnum())
        if normalized == accepted_text or normalized in accepted or (expected and normalized == expected):
            ACTIVE_RIDDLES.pop(key, None)
            await message.reply("✅ Correct! Well done! 🎉")
        else:
            ACTIVE_RIDDLES.pop(key, None)
            await message.reply(f"❌ Not quite! The correct answer was {answer}. Try /riddle for another one. 🧩")
        return

    # Group AI responds when Anonymous is mentioned or someone replies to the bot.
    if message.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        return
    is_mention = "anonymous" in text.lower()
    replied_to_bot = bool(
        message.reply_to_message
        and message.reply_to_message.from_user
        and message.reply_to_message.from_user.is_bot
        and message.reply_to_message.from_user.id == (await bot.get_me()).id
    )
    if not is_mention and not replied_to_bot:
        return
    prompt = text
    if is_mention:
        import re
        prompt = re.sub(r"(?i)anonymous(?:™)?\s*", "", prompt, count=1).strip(" ,:!?-\n")
    if not prompt and replied_to_bot:
        prompt = text
    if not prompt:
        # Treat a bare name call as the start of a natural chat, not a canned reply.
        prompt = "The user just called your name to get your attention. Reply with a short, natural greeting that invites them to continue, without sounding scripted."
    await ask_ai(message, prompt)


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
        BotCommand(command="ai", description="Chat with AI"),
        BotCommand(command="memory", description="View what I remember in this chat"),
        BotCommand(command="forgetme", description="Clear your AI memory in this chat"),
        BotCommand(command="ban", description="Ban a replied-to user"),
        BotCommand(command="unban", description="Unban by user ID"),
        BotCommand(command="kick", description="Kick a replied-to user"),
        BotCommand(command="mute", description="Mute a user; optional minutes"),
        BotCommand(command="unmute", description="Unmute a user"),
        BotCommand(command="warn", description="Warn a user"),
        BotCommand(command="warnings", description="Check a user's warnings"),
        BotCommand(command="resetwarnings", description="Reset a user's warnings"),
        BotCommand(command="promote", description="Promote a replied-to user"),
        BotCommand(command="demote", description="Demote a replied-to admin"),
        BotCommand(command="pin", description="Pin a replied-to message"),
        BotCommand(command="unpin", description="Unpin a message"),
        BotCommand(command="del", description="Delete a replied-to message"),
        BotCommand(command="purge", description="Delete a batch of messages"),
        BotCommand(command="close", description="Lock group messaging"),
        BotCommand(command="open", description="Unlock group messaging"),
        BotCommand(command="adminlist", description="List group admins"),
        BotCommand(command="id", description="Show chat/user ID"),
    ]
    await bot.set_my_commands(commands)
    init_database()
    log.info("Persistent memory database: %s", MEMORY_DB_PATH)
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
