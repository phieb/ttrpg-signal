import os
from dotenv import load_dotenv

load_dotenv()

# Claude API
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Signal
SIGNAL_CLI_URL = os.getenv("SIGNAL_CLI_URL", "http://signal-cli:8080")
SIGNAL_PHONE_NUMBER = os.getenv("SIGNAL_PHONE_NUMBER", "")

# NAS
TTRPG_PATH = os.getenv("TTRPG_PATH", "/mnt/ttrpg")

# Bot Verhalten
MAX_CONTEXT_TOKENS = int(os.getenv("MAX_CONTEXT_TOKENS", "3000"))
HISTORY_MESSAGES = int(os.getenv("HISTORY_MESSAGES", "10"))
RESPONSE_DELAY_SECONDS = float(os.getenv("RESPONSE_DELAY_SECONDS", "2"))
