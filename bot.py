import asyncio
import logging
import os
import random
import re
import time
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BotCommand
)
from openai import AsyncOpenAI

# Anonymous™ Mini — multi-AI fallback, fun commands, and group moderation.
# Required Render environment variable: TOKEN
# Optional AI environment variables:
# GEMINI_API_KEY, GROQ_API_KEY, OPENROUTER_API_KEY, CEREBRAS_API_KEY
# This app uses long polling plus a small HTTP health endpoint for Render.

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
log = logging.getLogger("anonymous-mini")

TOKEN = os.getenv("TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
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
    raise RuntimeError(
        "Missing TOKEN environment variable. Add your Telegram bot token in Render → Environment."
    )

bot = Bot(token=TOKEN)
dp = Dispatcher()
started_at = time.time()
bot_username = ""

# AI providers are optional. Add any provider API key in Render Environment.
AI_PROVIDERS = []

if GEMINI_API_KEY:
    try:
        from google import genai
        gemini_client = genai.Client(api_key=GEMINI_API_KEY)
        AI_PROVIDERS.append(("Gemini", "gemini", gemini_client))
    except Exception:
        log.exception("Gemini client could not be initialized.")

if GROQ_API_KEY:
    AI_PROVIDERS.append((
        "Groq", "openai",
        AsyncOpenAI(
            api_key=GROQ_API_KEY,
            base_url="https://api.groq.com/openai/v1",
            timeout=35.0,
            max_retries=0,
        )
    ))

if OPENROUTER_API_KEY:
    AI_PROVIDERS.append((
        "OpenRouter", "openai",
        AsyncOpenAI(
            api_key=OPENROUTER_API_KEY,
            base_url="https://openrouter.ai/api/v1",
            timeout=35.0,
            max_retries=0,
            default_headers={"X-OpenRouter-Title": "Anonymous Mini"},
        )
    ))

if CEREBRAS_API_KEY:
    AI_PROVIDERS.append((
        "Cerebras", "openai",
        AsyncOpenAI(
            api_key=CEREBRAS_API_KEY,
            base_url="https://api.cerebras.ai/v1",
            timeout=35.0,
            max_retries=0,
        )
    ))

# Active riddles are kept per chat and user while this process is running.
ACTIVE_RIDDLES = {}

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
            InlineKeyboardButton(
                text="➕ Add to Group",
                url=f"https://t.me/{bot_username}?startgroup=true" if bot_username else "https://t.me/"
            ),
        ],
    ])


HELP_TEXT = (
    "👋 Welcome to Anonymous™ Mini Help!\n\n"
    "I can help with fun commands, quick games, group moderation, and AI chat (when enabled).\n\n"
    "• Use the buttons below to explore.\n"
    "• In groups, reply to a message when using moderation commands.\n"
    "• Group moderation commands generally require admin permissions.\n"
    "• AI works when at least one provider API key is added in Render Environment.\n"
    "• If one AI provider reaches its limit, I try the next configured provider."
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
    "/ai your question — Ask AI (requires at least one API key)\n"
    "In groups, mention Anonymous followed by your question.\n\n"
    "🛡️ Group admins\n"
    "/ban, /unban, /kick, /mute, /unmute, /warn, /warnings\n"
    "Use these in a group and reply to the person's message."
)

ABOUT_TEXT = (
    "👑 Anonymous™ Mini\n\n"
    "Your personal mini bot — fast, simple, and easy to use.\n\n"
    "✨ Fun commands • Group tools • Multi-AI chat\n"
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

# No-repeat decks: each item is used once before that category starts a new round.
# This prevents jokes, riddles, and quotes from repeating until their lists cycle through.
_NO_REPEAT_DECKS = {}

def pick_without_repeat(category: str, items: list):
    if not items:
        raise ValueError(f"No items available for category: {category}")

    deck = _NO_REPEAT_DECKS.get(category, [])
    # Rebuild when the deck is empty or the source list has changed.
    if not deck:
        deck = list(items)
        random.shuffle(deck)

        # Avoid repeating the previous round's last item as the first item of a new round.
        previous = _NO_REPEAT_DECKS.get(f"{category}:last")
        if len(deck) > 1 and deck[-1] == previous:
            deck[0], deck[-1] = deck[-1], deck[0]

    chosen = deck.pop()
    _NO_REPEAT_DECKS[category] = deck
    _NO_REPEAT_DECKS[f"{category}:last"] = chosen
    return chosen

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
    await message.answer(pick_without_repeat("jokes", JOKES))


@dp.message(Command("fact"))
async def fact(message: Message):
    await message.answer(random.choice(FACTS))


@dp.message(Command("quote"))
async def quote(message: Message):
    await message.answer(pick_without_repeat("quotes", QUOTES))


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
    question, answer = pick_without_repeat("riddles", RIDDLES)
    ACTIVE_RIDDLES[(message.chat.id, message.from_user.id)] = (question, answer)
    await message.answer(
        f"🧩 RIDDLE: {question}\n\nReply with your guess! I'll tell you whether you're right. 🤔"
    )


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
            await callback.message.edit_text(
                f"✅ Correct! {option} — you earned 10,000 fun coins in spirit! 🪙"
            )
        else:
            await callback.message.edit_text(
                f"❌ Not quite! You chose {option}. Try another /quiz."
            )
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
    await message.answer(
        f"💘 Compatibility for {names[0]} + {names[1]}: {score}% {emoji}\n(Just for fun!)"
    )


@dp.message(Command("ai"))
async def ai_command(message: Message):
    prompt = (message.text or "").partition(" ")[2].strip()
    if not prompt:
        await message.answer("🤖 Ask me something: /ai explain black holes simply")
        return
    if not AI_PROVIDERS:
        await message.answer(
            "⚠️ No AI provider is configured. Add at least one API key in Render → Environment."
        )
        return
    await ask_ai(message, prompt)


async def ask_ai(message: Message, prompt: str):
    """Try configured AI providers one by one until one returns a usable answer."""
    system_prompt = (
        "You are Anonymous™ Mini, a friendly, concise Telegram assistant. "
        "Answer helpfully and naturally. Do not output HTML tags."
    )
    failures = []

    for provider_name, provider_type, client in AI_PROVIDERS:
        try:
            log.info("Trying AI provider: %s", provider_name)

            if provider_type == "gemini":
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        client.models.generate_content,
                        model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
                        contents=system_prompt + "\n\nUser question: " + prompt,
                    ),
                    timeout=35,
                )
                answer = (getattr(response, "text", None) or "").strip()
            else:
                if provider_name == "Groq":
                    model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
                elif provider_name == "OpenRouter":
                    model = os.getenv("OPENROUTER_MODEL", "openrouter/free")
                else:
                    model = os.getenv("CEREBRAS_MODEL", "gpt-oss-120b")

                response = await asyncio.wait_for(
                    client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": prompt},
                        ],
                        max_tokens=900,
                    ),
                    timeout=40,
                )
                answer = (response.choices[0].message.content or "").strip()

            if not answer:
                raise RuntimeError("Provider returned an empty answer.")

            if len(answer) > 4000:
                answer = answer[:3900] + "\n\n…(message shortened)"

            log.info("AI response succeeded with provider: %s", provider_name)
            await message.reply(answer)
            return

        except Exception as exc:
            log.warning(
                "AI provider %s failed (%s): %s",
                provider_name, type(exc).__name__, str(exc)[:250]
            )
            failures.append(provider_name)

    log.error("All configured AI providers failed: %s", ", ".join(failures))
    await message.reply(
        "⚠️ All configured AI providers are busy or unavailable right now. "
        "Please try again in a little while. If this keeps happening, check the API keys, "
        "model names, usage limits, and latest Render logs."
    )


# Reply-based group moderation commands.
@dp.message(Command("ban"))
async def ban_command(message: Message):
    if not await require_admin(message):
        return
    target = await replied_target(message)
    if not target:
        return
    try:
        await bot.ban_chat_member(message.chat.id, target.id)
        await message.answer(
            f'🔨 Banned <a href="tg://user?id={target.id}">{target.full_name}</a>.',
            parse_mode="HTML"
        )
    except Exception as e:
        await message.answer(
            f"❌ I couldn't ban that user. Check my admin permissions.\n{type(e).__name__}"
        )


@dp.message(Command("unban"))
async def unban_command(message: Message):
    if not await require_admin(message):
        return
    target = await replied_target(message)
    if not target:
        return
    try:
        await bot.unban_chat_member(message.chat.id, target.id, only_if_banned=True)
        await message.answer(f"✅ Unbanned {target.full_name} (if they were banned).")
    except Exception:
        await message.answer("❌ I couldn't unban that user. Check my admin permissions.")


@dp.message(Command("kick"))
async def kick_command(message: Message):
    if not await require_admin(message):
        return
    target = await replied_target(message)
    if not target:
        return
    try:
        await bot.ban_chat_member(message.chat.id, target.id)
        await bot.unban_chat_member(message.chat.id, target.id, only_if_banned=True)
        await message.answer(f"👢 Kicked {target.full_name}.")
    except Exception:
        await message.answer("❌ I couldn't kick that user. Check my admin permissions.")


@dp.message(Command("mute"))
async def mute_command(message: Message):
    if not await require_admin(message):
        return
    target = await replied_target(message)
    if not target:
        return
    try:
        from datetime import datetime, timedelta, timezone
        from aiogram.types import ChatPermissions
        until = datetime.now(timezone.utc) + timedelta(minutes=MUTE_MINUTES_DEFAULT)
        await bot.restrict_chat_member(
            message.chat.id, target.id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until
        )
        await message.answer(f"🔇 Muted {target.full_name} for {MUTE_MINUTES_DEFAULT} minutes.")
    except Exception:
        await message.answer("❌ I couldn't mute that user. Check my admin permissions.")


@dp.message(Command("unmute"))
async def unmute_command(message: Message):
    if not await require_admin(message):
        return
    target = await replied_target(message)
    if not target:
        return
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
    if not await require_admin(message):
        return
    target = await replied_target(message)
    if not target:
        return
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
            await message.answer(
                f"⚠️ {target.full_name} has {count}/3 warnings. I couldn't kick them; check my admin permissions."
            )
    else:
        await message.answer(
            f"⚠️ Warned {target.full_name}: {count}/3. Three warnings trigger a kick."
        )


@dp.message(Command("warnings"))
async def warnings_command(message: Message):
    target = await replied_target(message)
    if not target:
        return
    count = WARNINGS.get((message.chat.id, target.id), 0)
    await message.answer(
        f"⚠️ {target.full_name} has {count}/3 warning(s) in this running session."
    )


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
        answer_words = answer.lower().split()
        accepted = [
            "".join(ch for ch in word.lower() if ch.isalnum())
            for word in answer_words
        ]
        accepted_text = "".join(ch for ch in answer.lower() if ch.isalnum())
        if normalized == accepted_text or normalized in accepted:
            ACTIVE_RIDDLES.pop(key, None)
            await message.reply("✅ Correct! Well done! 🎉")
        else:
            ACTIVE_RIDDLES.pop(key, None)
            await message.reply(
                f"❌ Not quite! The correct answer was {answer}. Try /riddle for another one. 🧩"
            )
        return

    # Group AI responds when Anonymous is mentioned or someone replies to the bot.
    if message.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        return

    is_mention = "anonymous" in text.lower()
    replied_to_bot = False
    if message.reply_to_message and message.reply_to_message.from_user:
        if message.reply_to_message.from_user.is_bot:
            me = await bot.get_me()
            replied_to_bot = message.reply_to_message.from_user.id == me.id

    if not is_mention and not replied_to_bot:
        return

    prompt = text
    if is_mention:
        prompt = re.sub(r"(?i)anonymous(?:™)?\s*", "", prompt, count=1).strip(" ,:!?-\n")
    if not prompt and replied_to_bot:
        prompt = text
    if not prompt:
        await message.reply("🤖 Yes? Ask me anything!")
        return
    if not AI_PROVIDERS:
        await message.reply(
            "🤖 AI isn't configured yet. An admin needs to add at least one AI provider API key in Render Environment."
        )
        return
    await ask_ai(message, prompt)


async def health_handler(request):
    return web.json_response({
        "status": "ok",
        "service": "Anonymous Mini",
        "uptime_seconds": int(time.time() - started_at),
        "telegram_bot": "initialized",
        "ai_providers_configured": [provider[0] for provider in AI_PROVIDERS],
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
    log.info("Configured AI providers: %s", [provider[0] for provider in AI_PROVIDERS])

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
        BotCommand(command="ship", description="Check fun compatibility"),
        BotCommand(command="ai", description="Ask AI"),
        BotCommand(command="ban", description="Ban a replied-to user"),
        BotCommand(command="unban", description="Unban a replied-to user"),
        BotCommand(command="kick", description="Kick a replied-to user"),
        BotCommand(command="mute", description="Mute a replied-to user"),
        BotCommand(command="unmute", description="Unmute a replied-to user"),
        BotCommand(command="warn", description="Warn a replied-to user"),
        BotCommand(command="warnings", description="Check warnings"),
    ]
    await bot.set_my_commands(commands)
    runner = await start_http_server()
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await runner.cleanup()
        await bot.session.close()
        for _, provider_type, client in AI_PROVIDERS:
            if provider_type == "openai":
                try:
                    await client.close()
                except Exception:
                    pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Anonymous™ Mini stopped.")
