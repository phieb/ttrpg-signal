"""
Optional webhook receive mode.

When RECEIVE_MODE=webhook, the bot does NOT poll signal-cli. Instead, an
external dispatcher (e.g. n8n) POSTs signal-cli envelopes to /receive.
The envelope payload matches the JSON returned by signal-cli's
/v1/receive/<number> endpoint — either a single envelope object or a
list of envelopes.
"""
import logging
import threading
import time

from fastapi import FastAPI, Header, HTTPException, Request

import signal_client
from config import WEBHOOK_SECRET

logger = logging.getLogger(__name__)

# Serializes message handling so concurrent webhook deliveries don't trample
# the shared batch state in main.py.
_process_lock = threading.Lock()


def _check_auth(authorization: str | None, x_webhook_secret: str | None) -> None:
    if not WEBHOOK_SECRET:
        return
    provided = x_webhook_secret
    if not provided and authorization:
        # Support "Authorization: Bearer <secret>" as well
        parts = authorization.split(None, 1)
        provided = parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else authorization
    if provided != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="invalid webhook secret")


def build_app(process_message, get_players, get_registered_groups, flush_batches):
    """Build the FastAPI app with handlers bound to main.py's processing logic."""
    app = FastAPI()

    @app.post("/receive")
    async def receive(
        request: Request,
        authorization: str | None = Header(default=None),
        x_webhook_secret: str | None = Header(default=None, alias="X-Webhook-Secret"),
    ):
        _check_auth(authorization, x_webhook_secret)

        payload = await request.json()
        envelopes = payload if isinstance(payload, list) else [payload]

        with _process_lock:
            players = get_players()
            registered_groups = get_registered_groups()
            for envelope in envelopes:
                msg = signal_client.extract_message(envelope)
                if msg:
                    process_message(msg, players, registered_groups)
                    signal_client.mark_read(msg["sender"], msg["timestamp"])
            flush_batches()
        return {"status": "ok"}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


def run_flush_loop(flush_batches, stop_event: threading.Event, interval: float = 3.0):
    """Background thread: periodically drains batches whose timer has elapsed."""
    while not stop_event.is_set():
        try:
            with _process_lock:
                flush_batches()
        except SystemExit:
            stop_event.set()
            break
        except Exception as e:
            logger.error(f"Flush-Loop Fehler: {e}", exc_info=True)
        stop_event.wait(interval)
