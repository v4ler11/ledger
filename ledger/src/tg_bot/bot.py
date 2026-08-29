from aiogram import Bot, Dispatcher
from ledger.globals import TG_BOT_TOKEN


assert TG_BOT_TOKEN is not None, f"LEDGER_TG_BOT_TOKEN is not set"

bot = Bot(token=TG_BOT_TOKEN)
dp = Dispatcher()
