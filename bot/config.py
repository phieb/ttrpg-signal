import os
from dotenv import load_dotenv

load_dotenv()

# ── AI Provider ───────────────────────────────────────────────────────────────
# DM_PROVIDER: "openai" | "anthropic" | "gemini"
DM_PROVIDER = os.getenv("DM_PROVIDER", "openai")

# Claude API (used for character extraction / compression regardless of DM_PROVIDER)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_DM_MODEL = os.getenv("ANTHROPIC_DM_MODEL", "claude-sonnet-4-6")

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_DM_MODEL = os.getenv("OPENAI_DM_MODEL", "gpt-4o")

# Gemini (via google-generativeai)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_DM_MODEL = os.getenv("GEMINI_DM_MODEL", "gemini-2.0-flash")

# Google Vertex AI (Imagen)
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
GCP_PROJECT = os.getenv("GCP_PROJECT", "imagegenerator-491808")
GCP_LOCATION = os.getenv("GCP_LOCATION", "us-central1")

# Signal
SIGNAL_CLI_URL = os.getenv("SIGNAL_CLI_URL", "http://signal-cli:8080")
SIGNAL_PHONE_NUMBER = os.getenv("SIGNAL_PHONE_NUMBER", "")
ADMIN_PHONE_NUMBER = os.getenv("ADMIN_PHONE_NUMBER", "")  # darf !kommandos schicken

# Receive mode: "poll" (default, bot pulls from signal-cli) or "webhook"
# (external dispatcher POSTs envelopes to /receive)
RECEIVE_MODE = os.getenv("RECEIVE_MODE", "poll").lower()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "0.0.0.0")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8090"))

# NAS
TTRPG_PATH = os.getenv("TTRPG_PATH", "/mnt/ttrpg")

# Bot Verhalten
MAX_CONTEXT_TOKENS = int(os.getenv("MAX_CONTEXT_TOKENS", "3000"))
HISTORY_MESSAGES = int(os.getenv("HISTORY_MESSAGES", "6"))
RESPONSE_DELAY_SECONDS = float(os.getenv("RESPONSE_DELAY_SECONDS", "2"))

# Rate Limiting (pro Absender)
RATE_LIMIT_MESSAGES = int(os.getenv("RATE_LIMIT_MESSAGES", "5"))   # max Nachrichten…
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))       # …pro X Sekunden

# Nachrichten-Batching: warte X Sekunden nach der letzten Nachricht bevor der DM antwortet
BATCH_WINDOW_SECONDS = int(os.getenv("BATCH_WINDOW_SECONDS", "60"))

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Log-Rotation
MAX_LOG_LINES = int(os.getenv("MAX_LOG_LINES", "500"))  # JSONL-Zeilen bis Rotation
