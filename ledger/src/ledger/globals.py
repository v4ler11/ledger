import os

from pathlib import Path


TG_BOT_TOKEN = os.environ.get("LEDGER_TG_BOT_TOKEN")
TG_TARGET_USER_ID = int(os.environ["LEDGER_TG_TARGET_USER_ID"])
API_KEY  = os.environ.get("LEDGER_OPENROUTER_API_KEY")
LEDGER_PATH = Path(os.environ["LEDGER_PATH"])

MODEL = "openrouter/google/gemini-3.7-flash"
