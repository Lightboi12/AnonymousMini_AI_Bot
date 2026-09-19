import os
import asyncio

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message


# Get the bot token from an environment variable
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set!")


bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


@dp.message(Command("start"))
async def start_command(message: Message):
    await message.answer(
        "👋 Welcome to Anonymous™ Mini!\n\n"
        "🤖 Your personal mini bot is ready.\n"
        "⚡ Fast • Simple • Easy to use\n\n"
        "👤 Created by: @i_amanonymous\n\n"
        "💬 Use the buttons below to get started!"
    )


async def main():
    print("🤖 Anonymous™ Mini is running...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
