import asyncio
import logging
import os
import random
import time
import sqlite3
import html as html_lib
from datetime import datetime, timezone
from aiohttp import web
from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton as _InlineKeyboardButton,
    ChatMember, BotCommand, ChatJoinRequest, TelegramObject, PollAnswer, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats
)
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from typing import Callable, Awaitable, Any
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
PORT = int(os.getenv("PORT", "10000"))
OWNER_ID = int(os.getenv("OWNER_ID", "0") or "0")
DATABASE_PATH = os.getenv("DATABASE_PATH", "anonymous_mini.sqlite3")


# Telegram Bot API 9.4 button styles: blue primary, green success, red danger.
def InlineKeyboardButton(**kwargs):
    if "style" not in kwargs:
        label = str(kwargs.get("text", "")).lower()
        if any(word in label for word in ("cancel", "decline", "delete", "stop", "ban")):
            kwargs["style"] = "danger"
        elif any(word in label for word in ("start", "approve", "next", "yes", "join")):
            kwargs["style"] = "success"
        else:
            kwargs["style"] = "primary"
    return _InlineKeyboardButton(**kwargs)

# Persistent audience and pending join-request tracking.
def init_database():
    with sqlite3.connect(DATABASE_PATH) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, private_chat_id INTEGER, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS chats (chat_id INTEGER PRIMARY KEY, chat_type TEXT NOT NULL, title TEXT, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS pending_join_requests (chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, username TEXT, full_name TEXT, created_at TEXT NOT NULL, PRIMARY KEY(chat_id,user_id))")
        conn.commit()

def db_track_message(message: Message):
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DATABASE_PATH) as conn:
        if message.from_user and not message.from_user.is_bot:
            private_id = message.chat.id if message.chat.type == ChatType.PRIVATE else None
            conn.execute("INSERT INTO users(user_id,private_chat_id,first_seen,last_seen) VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET private_chat_id=COALESCE(excluded.private_chat_id,users.private_chat_id), last_seen=excluded.last_seen", (message.from_user.id, private_id, now, now))
        title = message.chat.title or message.chat.full_name or message.chat.username or str(message.chat.id)
        conn.execute("INSERT INTO chats(chat_id,chat_type,title,first_seen,last_seen) VALUES(?,?,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET chat_type=excluded.chat_type,title=excluded.title,last_seen=excluded.last_seen", (message.chat.id, message.chat.type, title, now, now))
        conn.commit()

class TrackingMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if isinstance(event, Message):
            try: db_track_message(event)
            except Exception: log.exception("Audience tracking failed")
        return await handler(event, data)

def owner_only(message: Message) -> bool:
    return bool(OWNER_ID and message.from_user and message.from_user.id == OWNER_ID)

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
AI_HISTORY = {}
AI_HISTORY_TURNS = 8
QUIZ_SETUP = {}  # setup progress keyed by chat and user
ACTIVE_QUIZZES = {}  # one live quiz per chat
QUIZ_TIMERS = {}
POLL_TO_QUIZ = {}  # poll_id -> (chat_id, round_index)


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
    "• For /promote and /demote, reply to the target member’s message.\n"
    "• The bot must be an admin with permission to add new administrators.\n"
    "• AI uses your configured Gemini, Groq, Cerebras, and OpenRouter providers."
)

COMMANDS_TEXT = (
    "📚 ANONYMOUS™ MINI — COMMAND LIST\n\n"
    "⚡ Basic\n"
    "/start — Open the main menu\n"
    "/help — Help and usage\n"
    "/commands — Show commands\n"
    "/about — About this bot\n"
    "/ping — Check bot response\n"
    "/promote — Promote the replied-to member (group admin)\n"
    "/demote — Remove admin rights from the replied-to admin (group admin)\n\n"
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
    "/quiz — Set up a timed category quiz\n"
    "/ship @user @user — Fun compatibility score\n\n"
    "🤖 AI\n"
    "/ai your question — Ask AI (requires API key)\n"
    "In groups, mention Anonymous followed by your question.\n\n"
    "🛡️ Group admins\n"
    "/ban, /unban, /kick, /mute, /unmute, /warn, /warnings\n"
    "Use these in a group and reply to the person’s message."
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
QUIZ_QUESTIONS = [
    # Arts & Literature
    ("Arts & Literature", "Easy", "Who wrote Romeo and Juliet?", ["Charles Dickens", "William Shakespeare", "Jane Austen", "Mark Twain"], 1),
    ("Arts & Literature", "Medium", "Which novel begins with the line 'Call me Ishmael'?", ["Moby-Dick", "The Great Gatsby", "Jane Eyre", "1984"], 0),
    ("Arts & Literature", "Hard", "Who painted the ceiling of the Sistine Chapel?", ["Leonardo da Vinci", "Raphael", "Michelangelo", "Donatello"], 2),
    # Film & TV
    ("Film & TV", "Easy", "Which fictional wizard attends Hogwarts?", ["Gandalf", "Harry Potter", "Merlin", "Doctor Strange"], 1),
    ("Film & TV", "Medium", "In which film franchise would you find the character Darth Vader?", ["Star Trek", "The Matrix", "Star Wars", "Dune"], 2),
    ("Film & TV", "Hard", "Who directed the 1994 film Pulp Fiction?", ["Martin Scorsese", "Quentin Tarantino", "Steven Spielberg", "David Fincher"], 1),
    # Food & Drink
    ("Food & Drink", "Easy", "Which ingredient is traditionally used to make guacamole?", ["Avocado", "Cucumber", "Green peas", "Zucchini"], 0),
    ("Food & Drink", "Medium", "Which country is widely associated with the origin of sushi?", ["Thailand", "Japan", "India", "Mexico"], 1),
    ("Food & Drink", "Hard", "What is the main ingredient in traditional hummus?", ["Lentils", "Chickpeas", "Black beans", "Potatoes"], 1),
    # General Knowledge
    ("General Knowledge", "Easy", "How many sides does a hexagon have?", ["Five", "Six", "Seven", "Eight"], 1),
    ("General Knowledge", "Medium", "What is the smallest prime number?", ["0", "1", "2", "3"], 2),
    ("General Knowledge", "Hard", "Which element has the chemical symbol W?", ["Tin", "Tungsten", "Titanium", "Zinc"], 1),
    # Geography
    ("Geography", "Easy", "What is the capital of Japan?", ["Seoul", "Beijing", "Tokyo", "Bangkok"], 2),
    ("Geography", "Medium", "Which river flows through Egypt?", ["Amazon", "Nile", "Danube", "Mekong"], 1),
    ("Geography", "Hard", "Which is the world's largest landlocked country by area?", ["Mongolia", "Chad", "Kazakhstan", "Bolivia"], 2),
    # History
    ("History", "Easy", "The ancient pyramids of Giza are in which country?", ["Greece", "Egypt", "Peru", "Jordan"], 1),
    ("History", "Medium", "In which year did the Berlin Wall fall?", ["1987", "1989", "1991", "1993"], 1),
    ("History", "Hard", "Which empire was ruled by Mansa Musa in the 14th century?", ["Mali Empire", "Roman Empire", "Ottoman Empire", "Mongol Empire"], 0),
    # Music
    ("Music", "Easy", "How many strings does a standard violin have?", ["Four", "Five", "Six", "Seven"], 0),
    ("Music", "Medium", "Which composer wrote the Moonlight Sonata?", ["Mozart", "Beethoven", "Bach", "Chopin"], 1),
    ("Music", "Hard", "Which instrument family does the oboe belong to?", ["Brass", "Percussion", "Woodwind", "Strings"], 2),
    # Science
    ("Science", "Easy", "Which planet is known as the Red Planet?", ["Venus", "Mars", "Jupiter", "Mercury"], 1),
    ("Science", "Medium", "What gas do plants absorb during photosynthesis?", ["Oxygen", "Nitrogen", "Carbon dioxide", "Hydrogen"], 2),
    ("Science", "Hard", "What is the SI unit of electrical resistance?", ["Volt", "Watt", "Ampere", "Ohm"], 3),
    # Society & Culture
    ("Society & Culture", "Easy", "Which language is primarily spoken in Brazil?", ["Spanish", "Portuguese", "French", "Italian"], 1),
    ("Society & Culture", "Medium", "What is the traditional Japanese art of paper folding called?", ["Ikebana", "Origami", "Calligraphy", "Kintsugi"], 1),
    ("Society & Culture", "Hard", "The ancient Olympic Games originated in which Greek region?", ["Attica", "Elis", "Ionia", "Macedonia"], 1),
    # Sport & Leisure
    ("Sport & Leisure", "Easy", "How many players from one team are on the field in a standard football (soccer) match?", ["9", "10", "11", "12"], 2),
    ("Sport & Leisure", "Medium", "In chess, which piece can only move diagonally?", ["Bishop", "Rook", "Queen", "King"], 0),
    ("Sport & Leisure", "Hard", "In tennis, what score comes after deuce when a player wins the next point?", ["Game", "Advantage", "Love", "Set"], 1),
]
QUIZ_CATEGORIES = ["Arts & Literature", "Film & TV", "Food & Drink", "General Knowledge", "Geography", "History", "Music", "Science", "Society & Culture", "Sport & Leisure"]
OPENTDB_CATEGORY_IDS = {"Arts & Literature": 25, "Film & TV": 11, "General Knowledge": 9, "Geography": 22, "History": 23, "Music": 12, "Science": 17, "Sport & Leisure": 21}

async def fetch_question_pool(category: str, difficulty: str):
    """Fetch a fresh randomized pool from Open Trivia DB; fall back to curated questions."""
    import aiohttp
    params = {"amount": 50, "type": "multiple"}
    if category in OPENTDB_CATEGORY_IDS: params["category"] = OPENTDB_CATEGORY_IDS[category]
    if difficulty != "Any": params["difficulty"] = difficulty.lower()
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get("https://opentdb.com/api.php", params=params) as response:
                payload = await response.json(content_type=None)
        if payload.get("response_code") == 0:
            pool = []
            for item in payload.get("results", []):
                question = html_lib.unescape(item["question"])
                correct = html_lib.unescape(item["correct_answer"])
                options = [html_lib.unescape(x) for x in item["incorrect_answers"]] + [correct]
                random.shuffle(options)
                pool.append((category, difficulty if difficulty != "Any" else item.get("difficulty", "medium").title(), question, options, options.index(correct)))
            if pool: return pool
    except Exception as exc:
        log.warning("External quiz pool unavailable: %s", type(exc).__name__)
    return [q for q in QUIZ_QUESTIONS if q[0] == category and (difficulty == "Any" or q[1] == difficulty)]

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


async def bot_can_promote_members(message: Message) -> bool:
    """Check chat type and the bot's Telegram admin rights before promote/demote calls."""
    if not message.chat or message.chat.type != ChatType.SUPERGROUP:
        await message.reply(
            "❌ Telegram only supports this promote/demote operation in a supergroup. "
            "Convert this group to a supergroup (Telegram usually does this automatically for larger groups), "
            "then try again."
        )
        return False
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(message.chat.id, me.id)
        if member.status not in ("administrator", "creator"):
            await message.reply(
                "❌ I am not a group administrator. Promote me to admin first, then enable "
                "the permission to add new administrators (Manage/Promote admins)."
            )
            return False
        if member.status != "creator" and not getattr(member, "can_promote_members", False):
            await message.reply(
                "❌ I am a group admin, but I do not have permission to add new administrators. "
                "Open Group Settings → Administrators → Anonymous™ Mini and enable the “Add New Admins” / “Promote Members” permission. Then try again."
            )
            return False
        return True
    except Exception as exc:
        log.warning("Could not verify bot admin rights: %s", type(exc).__name__)
        await message.reply(
            "❌ I couldn't verify my group permissions. Make sure I'm an administrator "
            "with permission to add new administrators, then try again."
        )
        return False


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


def quiz_setup_keyboard(step: str, state: dict) -> InlineKeyboardMarkup:
    if step == "category":
        rows = []
        for i in range(0, len(QUIZ_CATEGORIES), 2):
            row = [InlineKeyboardButton(text=c, callback_data=f"qsetup:cat:{c}") for c in QUIZ_CATEGORIES[i:i+2]]
            rows.append(row)
    elif step == "difficulty":
        rows = [[InlineKeyboardButton(text=x, callback_data=f"qsetup:diff:{x}") for x in ["Any", "Easy", "Medium", "Hard"]]]
    elif step == "rounds":
        rows = [[InlineKeyboardButton(text=f"{n} rounds", callback_data=f"qsetup:rounds:{n}") for n in [5, 10, 15]]]
    elif step == "time":
        rows = [[InlineKeyboardButton(text=f"{n}s", callback_data=f"qsetup:time:{n}") for n in [15, 30, 45]]]
    else:
        rows = [[InlineKeyboardButton(text="▶️ Start", callback_data="qsetup:start")]]
    rows.append([InlineKeyboardButton(text="Cancel", callback_data="qsetup:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def quiz_setup_text(step: str, state: dict) -> str:
    labels = {"category": "1/4 — Choose a category", "difficulty": "2/4 — Choose a difficulty", "rounds": "3/4 — Choose the number of rounds", "time": "4/4 — Choose time per question", "ready": "QUIZ READY"}
    summary = (f"Starter: {state.get('starter', 'Player')}\nCategory: {state.get('category', '—')}\n"
               f"Difficulty: {state.get('difficulty', '—')}\nRounds: {state.get('rounds', '—')}\nTime: {state.get('seconds', '—')}s/question")
    if step == "category":
        return "🧠 QUIZ SETUP · " + labels[step] + "\n\nChoose a category. Questions are curated and matched to the category and difficulty."
    if step == "difficulty":
        return "🧠 QUIZ SETUP · " + labels[step] + "\n\n" + summary + "\n\nChoose a difficulty."
    if step == "rounds":
        return "🧠 QUIZ SETUP · " + labels[step] + "\n\n" + summary + "\n\nChoose the number of rounds."
    if step == "time":
        return "🧠 QUIZ SETUP · " + labels[step] + "\n\n" + summary + "\n\nChoose time per question."
    return "✅ QUIZ READY\n\n" + summary + "\n\nPress Start when ready. After each timer ends, I'll reveal the correct answer and then move to the next round."


@dp.message(Command("quiz"))
async def quiz(message: Message):
    if not message.from_user:
        return
    key = (message.chat.id, message.from_user.id)
    state = {"starter": message.from_user.full_name, "category": None, "difficulty": None, "rounds": None, "seconds": None}
    QUIZ_SETUP[key] = state
    await message.answer(quiz_setup_text("category", state), reply_markup=quiz_setup_keyboard("category", state))


@dp.callback_query(F.data.startswith("qsetup:"))
async def quiz_setup_callback(callback: CallbackQuery):
    if not callback.message or not callback.from_user:
        await callback.answer()
        return
    key = (callback.message.chat.id, callback.from_user.id)
    state = QUIZ_SETUP.get(key)
    if callback.data == "qsetup:cancel":
        QUIZ_SETUP.pop(key, None)
        await callback.message.edit_text("Quiz setup cancelled.")
        await callback.answer()
        return
    if not state:
        await callback.answer("Start a new quiz with /quiz.", show_alert=True)
        return
    parts = callback.data.split(":", 2)
    action = parts[1]
    value = parts[2] if len(parts) > 2 else ""
    if action == "cat":
        state["category"] = value
        step = "difficulty"
    elif action == "diff":
        state["difficulty"] = value
        step = "rounds"
    elif action == "rounds":
        state["rounds"] = int(value)
        step = "time"
    elif action == "time":
        state["seconds"] = int(value)
        step = "ready"
    elif action == "start":
        QUIZ_SETUP.pop(key, None)
        chat_id = callback.message.chat.id
        old = ACTIVE_QUIZZES.get(chat_id)
        if old and old.get("task"):
            old["task"].cancel()
        await callback.message.edit_text("🔎 Building a fresh question pool…")
        pool = await fetch_question_pool(state["category"], state["difficulty"])
        if not pool:
            await callback.message.edit_text("I couldn't load questions for that selection right now. Please try again in a moment or choose Difficulty: Any.")
            await callback.answer()
            return
        rounds = state["rounds"]
        # Never repeat a question inside a quiz. If the available pool is smaller than the requested rounds, explain instead of recycling questions.
        unique_pool = list({(q[0], q[1], q[2]): q for q in pool}.values())
        if len(unique_pool) < rounds:
            await callback.message.edit_text(f"I found only {len(unique_pool)} unique questions for that selection, but you requested {rounds} rounds. Choose Difficulty: Any or a different category so I can build a larger pool.")
            await callback.answer()
            return
        questions = random.sample(unique_pool, rounds)
        ACTIVE_QUIZZES[chat_id] = {"owner": callback.from_user.id, "starter": state["starter"], "category": state["category"], "difficulty": state["difficulty"], "seconds": state["seconds"], "questions": questions, "index": 0, "scores": {}, "answers": {}, "message_id": callback.message.message_id, "task": None}
        await callback.answer("Quiz starting!")
        await send_quiz_question(chat_id, callback.message.message_id)
        return
    await callback.message.edit_text(quiz_setup_text(step, state), reply_markup=quiz_setup_keyboard(step, state))
    await callback.answer()


async def send_quiz_question(chat_id: int, message_id: int = 0):
    """Send each round as a native Telegram quiz poll, like the reference screenshot."""
    quiz_state = ACTIVE_QUIZZES.get(chat_id)
    if not quiz_state:
        return
    idx = quiz_state["index"]
    if idx >= len(quiz_state["questions"]):
        score_lines = sorted(quiz_state["scores"].items(), key=lambda item: (-item[1], item[0].casefold()))
        leaderboard = "\n".join(f"{i+1}. {name}: {score}/{len(quiz_state['questions'])}" for i, (name, score) in enumerate(score_lines[:10])) or "No answers recorded."
        try:
            await bot.send_message(chat_id, f"🏁 <b>QUIZ FINISHED!</b>\n{html_lib.escape(quiz_state['category'])} · {len(quiz_state['questions'])} rounds\n\n🏆 <b>Scores</b>\n{html_lib.escape(leaderboard)}", parse_mode="HTML")
        except Exception:
            log.exception("Could not send quiz results")
        ACTIVE_QUIZZES.pop(chat_id, None)
        return
    category, difficulty, question, options, correct = quiz_state["questions"][idx]
    quiz_state["answers"] = {}
    title = f"{category} · {difficulty} · Round {idx+1}/{len(quiz_state['questions'])} · +{15 if difficulty == 'Hard' else 10} aura"
    poll_question = f"{title}\n\n{question}"
    if len(poll_question) > 300:
        poll_question = poll_question[:297] + "..."
    try:
        sent = await bot.send_poll(
            chat_id=chat_id,
            question=poll_question,
            options=[str(opt)[:100] for opt in options],
            type="quiz",
            correct_option_id=correct,
            is_anonymous=False,
            open_period=int(quiz_state["seconds"]),
            explanation=f"Correct answer: {options[correct]}",
        )
        POLL_TO_QUIZ[sent.poll.id] = (chat_id, idx)
        quiz_state["poll_id"] = sent.poll.id
        quiz_state["task"] = asyncio.create_task(quiz_question_timer(chat_id, sent.message_id, idx))
    except Exception:
        log.exception("Could not send native Telegram quiz poll")
        await bot.send_message(chat_id, "I couldn't send the quiz poll. Make sure polls are allowed in this chat and try /quiz again.")


@dp.poll_answer()
async def quiz_poll_answer(answer: PollAnswer):
    mapping = POLL_TO_QUIZ.get(answer.poll_id)
    if not mapping or not answer.user or not answer.option_ids:
        return
    chat_id, idx = mapping
    state = ACTIVE_QUIZZES.get(chat_id)
    if not state or state.get("index") != idx:
        return
    _, _, _, options, correct = state["questions"][idx]
    state["answers"][answer.user.id] = {
        "name": answer.user.full_name,
        "chosen": answer.option_ids[0],
    }


async def quiz_question_timer(chat_id: int, message_id: int, idx: int):
    try:
        state = ACTIVE_QUIZZES.get(chat_id)
        if not state:
            return
        await asyncio.sleep(state["seconds"] + 2)  # allow Telegram to close the poll and publish final results
        state = ACTIVE_QUIZZES.get(chat_id)
        if not state or state["index"] != idx:
            return
        _, _, _, options, correct = state["questions"][idx]
        for answer in state["answers"].values():
            name = answer["name"]
            state["scores"][name] = state["scores"].get(name, 0) + (1 if answer["chosen"] == correct else 0)
        if not state["answers"]:
            await bot.send_message(chat_id, f"⏰ Round {idx+1} ended — nobody answered.\n✅ Correct answer: {options[correct]}")
        POLL_TO_QUIZ.pop(state.get("poll_id"), None)
        state["index"] += 1
        await send_quiz_question(chat_id)
    except asyncio.CancelledError:
        return
    except Exception:
        log.exception("Quiz poll timer failed")


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
    await ask_ai(message, prompt)


async def ask_ai(message: Message, prompt: str):
    """Try configured AI providers in order with short per-provider timeouts.

    Provider failures are logged privately; users see only the successful answer.
    """
    import aiohttp

    history_key = (message.chat.id, message.from_user.id if message.from_user else 0)
    history = AI_HISTORY.setdefault(history_key, [])
    recent = history[-(AI_HISTORY_TURNS * 2):]
    system_instruction = (
        "You are Anonymous™ Mini, a smart, warm, witty, natural conversational AI in a Telegram group. "
        "Talk like a normal helpful person, not a customer-support script or robotic FAQ bot. "
        "Use recent conversation to understand follow-ups. Answer directly and match the user's tone "
        "and language when appropriate. Do not force a follow-up question. Do not output HTML tags. "
        "Your founder is 𝐋 𝐎 𝐑 𝐃 ♰ 𝐀𝐍𝐎𝐍𝐘𝐌𝐎𝐔𝐒™."
    )
    messages = [{"role": "system", "content": system_instruction}]
    for role, content in recent:
        messages.append({"role": "assistant" if role == "assistant" else "user", "content": content})
    messages.append({"role": "user", "content": prompt})
    conversation = system_instruction + "\n\nRecent conversation:\n" + "\n".join(
        ("User: " if role == "user" else "Anonymous: ") + content for role, content in recent
    ) + "\nUser: " + prompt

    # Use the existing Render keys. OpenAI-compatible providers are called directly via HTTP.
    providers = []
    if GEMINI_API_KEY:
        providers.append(("Gemini", "gemini"))
    if os.getenv("GROQ_API_KEY", "").strip():
        providers.append(("Groq", "groq"))
    if os.getenv("CEREBRAS_API_KEY", "").strip():
        providers.append(("Cerebras", "cerebras"))
    if os.getenv("OPENROUTER_API_KEY", "").strip():
        providers.append(("OpenRouter", "openrouter"))

    if not providers:
        log.error("AI request has no configured provider keys (Gemini/Groq/Cerebras/OpenRouter).")
        await message.reply("⚠️ AI isn't configured. Check your provider API keys in Render Environment.")
        return
    log.info("AI providers configured for this request: %s", ", ".join(name for name, _ in providers))

    timeout_seconds = max(6.0, min(float(os.getenv("AI_PROVIDER_TIMEOUT_SECONDS", "12")), 20.0))
    last_error = None
    for provider_name, provider in providers:
        try:
            async with ChatActionSender(bot=bot, chat_id=message.chat.id, action="typing"):
                if provider == "gemini":
                    from google import genai
                    client = ai_client or genai.Client(api_key=GEMINI_API_KEY)
                    model_names = []
                    primary = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
                    fallback = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-2.5-flash-lite").strip()
                    if primary:
                        model_names.append(primary)
                    if fallback and fallback not in model_names:
                        model_names.append(fallback)
                    provider_error = None
                    answer = ""
                    for model_name in model_names:
                        try:
                            response = await asyncio.wait_for(
                                client.aio.models.generate_content(model=model_name, contents=conversation),
                                timeout=timeout_seconds,
                            )
                            answer = (getattr(response, "text", None) or "").strip()
                            if answer:
                                break
                            provider_error = RuntimeError("Gemini returned an empty answer")
                        except Exception as exc:
                            provider_error = exc
                            detail = str(exc).lower()
                            # Invalid model names won't be fixed by trying another key/provider model.
                            if "not found" in detail or "model" in detail and ("404" in detail or "not supported" in detail):
                                continue
                            # For quota/auth/network errors, fail over to the next provider promptly.
                            break
                    else:
                        answer = ""
                    if not answer:
                        raise provider_error or RuntimeError("Gemini returned no answer")
                else:
                    configs = {
                        "groq": ("https://api.groq.com/openai/v1/chat/completions", "GROQ_API_KEY", os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")),
                        "cerebras": ("https://api.cerebras.ai/v1/chat/completions", "CEREBRAS_API_KEY", os.getenv("CEREBRAS_MODEL", "llama3.1-8b")),
                        "openrouter": ("https://openrouter.ai/api/v1/chat/completions", "OPENROUTER_API_KEY", os.getenv("OPENROUTER_MODEL", "openai/gpt-oss-20b")),
                    }
                    url, key_name, model_name = configs[provider]
                    api_key = os.getenv(key_name, "").strip()
                    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
                    if provider == "openrouter":
                        headers["HTTP-Referer"] = os.getenv("OPENROUTER_SITE_URL", "https://t.me/")
                        headers["X-Title"] = "Anonymous Mini"
                    request_timeout = aiohttp.ClientTimeout(total=timeout_seconds, connect=min(2.0, timeout_seconds))
                    async with aiohttp.ClientSession(timeout=request_timeout) as session:
                        async with session.post(url, headers=headers, json={
                            "model": model_name,
                            "messages": messages,
                            "temperature": 0.7,
                        }) as response:
                            payload = await response.json(content_type=None)
                            if response.status >= 400:
                                raise RuntimeError(f"{provider_name} HTTP {response.status}: {str(payload)[:400]}")
                    answer = str(payload.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()
                    if not answer:
                        raise RuntimeError(f"{provider_name} returned an empty answer")

            if len(answer) > 4000:
                answer = answer[:3900] + "\n\n…(message shortened)"
            await message.reply(answer)
            history.extend([("user", prompt), ("assistant", answer)])
            AI_HISTORY[history_key] = history[-(AI_HISTORY_TURNS * 2):]
            log.info("AI request succeeded with %s", provider_name)
            return
        except Exception as exc:
            last_error = exc
            # Include the provider's actual rejection reason in Render logs, but never API keys.
            detail = str(exc).replace("\n", " ").strip()[:700]
            for secret_name in ("GEMINI_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY", "OPENROUTER_API_KEY"):
                secret_value = os.getenv(secret_name, "").strip()
                if secret_value:
                    detail = detail.replace(secret_value, "[REDACTED]")
            log.warning("AI provider %s failed (%s); switching immediately. Details: %s",
                        provider_name, type(exc).__name__, detail or "<no error detail returned>")
            continue

    log.error("All configured AI providers failed; last error type=%s details=%s",
              type(last_error).__name__ if last_error else "unknown",
              (str(last_error).replace("\n", " ")[:700] if last_error else "none"))
    await message.reply("⚠️ I'm temporarily busy. Please try again in a moment.")


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


@dp.message(Command("promote"))
async def promote_command(message: Message):
    """Promote the replied-to group member to a standard moderator."""
    if not await require_admin(message):
        return
    target = await replied_target(message)
    if not target:
        return
    if not await bot_can_promote_members(message):
        return
    if message.from_user and target.id == message.from_user.id:
        await message.reply("⚠️ You cannot use /promote on yourself.")
        return
    try:
        await bot.promote_chat_member(
            chat_id=message.chat.id,
            user_id=target.id,
            can_manage_chat=False,
            can_change_info=False,
            can_post_messages=False,
            can_edit_messages=False,
            can_delete_messages=True,
            can_manage_video_chats=False,
            can_restrict_members=True,
            can_promote_members=False,
            can_invite_users=True,
            can_pin_messages=True,
            can_manage_topics=False,
            can_post_stories=False,
            can_edit_stories=False,
            can_delete_stories=False,
        )
        await message.answer(
            f"✅ Promoted <a href=\"tg://user?id={target.id}\">{html_lib.escape(target.full_name)}</a> to group admin with standard moderation rights.",
            parse_mode="HTML",
        )
    except Exception as exc:
        detail = getattr(exc, "message", None) or str(exc)
        detail_text = str(detail)
        log.warning("Promote failed in chat %s for target %s (%s): %s",
                    message.chat.id, target.id, type(exc).__name__, detail_text[:500])
        if "CHAT_ADMIN_REQUIRED" in detail_text or "not enough rights" in detail_text.lower():
            explanation = (
                "Telegram rejected the admin change. Confirm Anonymous™ Mini has the specific "
                "‘Add New Admins / Promote Members’ right, and that the target is not an admin "
                "with a higher/equal rank than the bot. Telegram controls admin rank server-side."
            )
        else:
            explanation = "Check the bot's admin permissions and the target member, then try again."
        await message.answer(
            f"❌ I couldn't promote that member. {explanation}\n"
            f"Telegram: {html_lib.escape(detail_text[:400])}",
            parse_mode="HTML",
        )


@dp.message(Command("demote"))
async def demote_command(message: Message):
    """Remove admin rights from the replied-to administrator."""
    if not await require_admin(message):
        return
    target = await replied_target(message)
    if not target:
        return
    if not await bot_can_promote_members(message):
        return
    if message.from_user and target.id == message.from_user.id:
        await message.reply("⚠️ You cannot use /demote on yourself.")
        return
    try:
        member = await bot.get_chat_member(message.chat.id, target.id)
        if member.status not in ("administrator", "creator"):
            await message.answer(f"ℹ️ {html_lib.escape(target.full_name)} is not a group administrator.", parse_mode="HTML")
            return
        if member.status == "creator":
            await message.answer("❌ The group owner cannot be demoted by a bot.")
            return
        await bot.promote_chat_member(
            chat_id=message.chat.id,
            user_id=target.id,
            can_manage_chat=False,
            can_change_info=False,
            can_post_messages=False,
            can_edit_messages=False,
            can_delete_messages=False,
            can_manage_video_chats=False,
            can_restrict_members=False,
            can_promote_members=False,
            can_invite_users=False,
            can_pin_messages=False,
            can_manage_topics=False,
            can_post_stories=False,
            can_edit_stories=False,
            can_delete_stories=False,
        )
        await message.answer(f"✅ Removed administrator rights from <a href=\"tg://user?id={target.id}\">{html_lib.escape(target.full_name)}</a>.", parse_mode="HTML")
    except Exception as exc:
        detail = getattr(exc, "message", None) or str(exc)
        detail_text = str(detail)
        log.warning("Demote failed in chat %s for target %s (%s): %s",
                    message.chat.id, target.id, type(exc).__name__, detail_text[:500])
        if "CHAT_ADMIN_REQUIRED" in detail_text or "not enough rights" in detail_text.lower():
            explanation = (
                "Telegram rejected the admin change. Confirm Anonymous™ Mini has the specific "
                "‘Add New Admins / Promote Members’ right, and that the target admin is not "
                "ranked above the bot. The group owner or an admin with a higher rank may need "
                "to change that person's status."
            )
        else:
            explanation = "Check the bot's admin permissions and the target member, then try again."
        await message.answer(
            f"❌ I couldn't demote that administrator. {explanation}\n"
            f"Telegram: {html_lib.escape(detail_text[:400])}",
            parse_mode="HTML",
        )



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
        await message.answer(f"⚠️ Warned {target.full_name}: {count}/3. Three warnings trigger a kick.")


@dp.message(Command("warnings"))
async def warnings_command(message: Message):
    target = await replied_target(message)
    if not target: return
    count = WARNINGS.get((message.chat.id, target.id), 0)
    await message.answer(f"⚠️ {target.full_name} has {count}/3 warning(s) in this running session.")


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


@dp.chat_join_request()
async def track_join_request(request: ChatJoinRequest):
    now = datetime.now(timezone.utc).isoformat()
    try:
        with sqlite3.connect(DATABASE_PATH) as conn:
            conn.execute("INSERT OR REPLACE INTO pending_join_requests(chat_id,user_id,username,full_name,created_at) VALUES(?,?,?,?,?)", (request.chat.id, request.from_user.id, request.from_user.username, request.from_user.full_name, now))
            conn.execute("INSERT INTO chats(chat_id,chat_type,title,first_seen,last_seen) VALUES(?,?,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET last_seen=excluded.last_seen", (request.chat.id, request.chat.type, request.chat.title or str(request.chat.id), now, now))
            conn.commit()
    except Exception:
        log.exception("Could not save join request")

async def pending_rows(chat_id: int):
    with sqlite3.connect(DATABASE_PATH) as conn:
        return conn.execute("SELECT user_id, username, full_name, created_at FROM pending_join_requests WHERE chat_id=? ORDER BY created_at", (chat_id,)).fetchall()

@dp.message(Command("list"))
async def list_pending_command(message: Message):
    parts = (message.text or "").split()
    if len(parts) < 2 or parts[1].lower() != "pending":
        return
    if not await require_admin(message):
        return
    rows = await pending_rows(message.chat.id)
    if not rows:
        await message.answer("📭 No tracked pending join requests for this group.")
        return
    lines = ["⏳ Pending join requests:"]
    for user_id, username, full_name, created_at in rows[:50]:
        who = f"@{username}" if username else (full_name or "Unknown user")
        lines.append(f"• {who} — ID: <code>{user_id}</code>")
    lines.append("\nApprove: /approve pending USER_ID\nDecline: /decline pending USER_ID")
    await message.answer("\n".join(lines), parse_mode="HTML")

async def process_pending_request(message: Message, approve: bool):
    if not await require_admin(message):
        return
    parts = (message.text or "").split()
    if len(parts) < 3 or parts[1].lower() != "pending" or not parts[2].isdigit():
        usage = "/approve pending USER_ID" if approve else "/decline pending USER_ID"
        await message.answer(f"Usage: {usage}\nUse /list pending to see tracked requests.")
        return
    user_id = int(parts[2])
    try:
        if approve:
            await bot.approve_chat_join_request(chat_id=message.chat.id, user_id=user_id)
        else:
            await bot.decline_chat_join_request(chat_id=message.chat.id, user_id=user_id)
        with sqlite3.connect(DATABASE_PATH) as conn:
            conn.execute("DELETE FROM pending_join_requests WHERE chat_id=? AND user_id=?", (message.chat.id, user_id))
            conn.commit()
        verb = "Approved" if approve else "Declined"
        await message.answer(f"✅ {verb} join request for <code>{user_id}</code>.", parse_mode="HTML")
    except Exception as exc:
        action = "approve" if approve else "decline"
        await message.answer(f"Couldn't {action} that request. Ensure the bot is an admin with invite-user permissions and the request is still pending. ({type(exc).__name__})")

@dp.message(Command("approve"))
async def approve_pending_command(message: Message):
    await process_pending_request(message, True)

@dp.message(Command("decline"))
async def decline_pending_command(message: Message):
    await process_pending_request(message, False)

@dp.message(Command("stats", "botstats"))
async def owner_stats_command(message: Message):
    if not owner_only(message):
        await message.answer("⛔ Owner-only command.")
        return
    with sqlite3.connect(DATABASE_PATH) as conn:
        dm_users = conn.execute("SELECT COUNT(DISTINCT user_id) FROM users WHERE private_chat_id IS NOT NULL").fetchone()[0]
        group_count = conn.execute("SELECT COUNT(*) FROM chats WHERE chat_type IN ('group','supergroup')").fetchone()[0]
        channel_count = conn.execute("SELECT COUNT(*) FROM chats WHERE chat_type='channel'").fetchone()[0]
        users_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    await message.answer(
        f"👑 <b>Anonymous™ Mini — Owner Stats</b>\n\n👤 Unique users seen: <b>{users_count}</b>\n"
        f"💬 Users who opened/used the bot in DMs: <b>{dm_users}</b>\n👥 Groups/supergroups seen: <b>{group_count}</b>\n"
        f"📣 Channels seen: <b>{channel_count}</b>\n\n<i>Counts are based on chats/messages the bot has actually received since this database was enabled.</i>",
        parse_mode="HTML")

@dp.message(Command("broadcast"))
async def owner_broadcast_command(message: Message):
    if not owner_only(message):
        await message.answer("⛔ Owner-only command.")
        return
    announcement = (message.text or "").partition(" ")[2].strip()
    if not announcement:
        await message.answer("Usage: /broadcast Your announcement goes here\nThis sends the announcement to every tracked private user and group.")
        return
    with sqlite3.connect(DATABASE_PATH) as conn:
        targets = {row[0] for row in conn.execute("SELECT private_chat_id FROM users WHERE private_chat_id IS NOT NULL").fetchall()}
        targets.update(row[0] for row in conn.execute("SELECT chat_id FROM chats WHERE chat_type IN ('group','supergroup')").fetchall())
    sent = failed = 0
    for target in targets:
        try:
            await bot.send_message(target, "📢 <b>Announcement from Anonymous™ Mini</b>\n\n" + html_lib.escape(announcement), parse_mode="HTML")
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.04)
    await message.answer(f"📣 Broadcast finished.\n✅ Sent: {sent}\n⚠️ Failed/unreachable: {failed}")

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
    init_database()
    dp.message.middleware(TrackingMiddleware())
    me = await bot.get_me()
    bot_username = me.username or ""
    log.info("Starting @%s", bot_username)
    commands = [
        BotCommand(command="start", description="Open the main menu"),
        BotCommand(command="help", description="Get help"),
        BotCommand(command="commands", description="List commands"),
        BotCommand(command="about", description="About this bot"),
        BotCommand(command="ping", description="Check bot status"),
        BotCommand(command="promote", description="Promote replied-to group member"),
        BotCommand(command="demote", description="Demote replied-to group admin"),
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
        BotCommand(command="quiz", description="Set up a timed category quiz"),
        BotCommand(command="list", description="List pending join requests: /list pending"),
        BotCommand(command="approve", description="Approve pending join request"),
        BotCommand(command="decline", description="Decline pending join request"),
        BotCommand(command="stats", description="Owner bot statistics"),
        BotCommand(command="broadcast", description="Owner announcement broadcast"),
        BotCommand(command="ai", description="Ask AI"),
    ]
    # Telegram has separate command menus for groups and private chats.
    await bot.set_my_commands(commands, scope=BotCommandScopeAllPrivateChats())
    group_commands = commands[:]
    await bot.set_my_commands(group_commands, scope=BotCommandScopeAllGroupChats())
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
