import asyncio

from aiogram.client.session import aiohttp

from tg_bot.commands import register_commands
from tg_bot.handlers import register_handlers
from tg_bot.state import State
from tg_bot.bot import dp, bot


async def main_async():
    http_session = aiohttp.ClientSession()

    state = State(http_session=http_session)

    dp["app_state"] = state

    register_commands()
    register_handlers()

    await dp.start_polling(bot)


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
