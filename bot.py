import os
import asyncio

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message


# Get Telegram bot token from Render Environment Variables
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set!")


bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


@dp.message(Command("start"))
async def start_command(message: Message):
    await message.answer(
        "👋 Welcome to Anonymous™ Mini AI!\n\n"
        "🤖 Your personal mini ai bot is ready.\n"
        "⚡ Fast • Simple • Easy to use\n\n"
        "👤 Created by: @i_amanonymous\n"
        "💬 Use /help to get started!"
    )


# Small web server for Render
async def health(request):
    return web.Response(text="Anonymous™ Mini is running!")


async def start_web_server():
    app = web.Application()

    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.getenv("PORT", "10000"))

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        port
    )

    await site.start()

    print(f"🌐 Web server running on port {port}")


async def main():
    print("🤖 Anonymous™ Mini is running...")

    await start_web_server()

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
