from aiogram.filters import Command
from aiogram import types

from tg_bot.state import State
from tg_bot.bot import dp


@dp.message(Command("start"))
async def command_start(message: types.Message, app_state: State) -> None:
    await message.answer(
        f"Hello! I am a Ledger Bot\n"
        f"Send me a text and I will speak back to you!"
    )
