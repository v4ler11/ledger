import os

from pathlib import Path


TG_BOT_TOKEN = os.environ.get("LEDGER_TG_BOT_TOKEN")
TG_TARGET_USER_ID = int(os.environ["LEDGER_TG_TARGET_USER_ID"])
API_KEY  = os.environ.get("LEDGER_OPENROUTER_API_KEY")
LEDGER_PATH = Path(os.environ["LEDGER_PATH"])
MCP_HOST = os.environ.get("LEDGER_MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.environ.get("LEDGER_MCP_PORT", "8000"))

MODEL = "openrouter/google/gemini-3.7-flash"
