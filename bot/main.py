import time
import logging
import signal
import yaml
from pathlib import Path

import signal_client
import session_manager
import dm_engine
from config import SIGNAL_PHONE_NUMBER, TTRPG_PATH, RESPONSE_DELAY_SECONDS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

POLL_INTERVAL = 3

running = True


def handle_signal(sig, frame):
    global running
    logger.info("Shutdown-Signal empfangen, beende Bot...")
    running = False


def load_registered_groups() -> set:
    status_path = Path(TTRPG_PATH) / "status.yaml"
    try:
        data = yaml.safe_load(status_path.read_text())
        return {
            a["signal_gruppe"]
            for a in data.get("abenteuer", [])
            if a.get("signal_gruppe")
        }
    except Exception as e:
        logger.warning(f"status.yaml konnte nicht geladen werden: {e}")
        return set()


def handle_command(cmd: str, sender: str, adventure_folder: str) -> str | None:
    """
    Verarbeitet !kommandos vom DM (castle assistant).
    Gibt eine Antwort-Nachricht zurück oder None.
    """
    if cmd == "!pause":
        dm_engine.compress_session(adventure_folder)
        dm_engine.clear_history(adventure_folder)
        return "⏸ Spielstand gespeichert und komprimiert. Bis zum nächsten Mal!"

    if cmd == "!status":
        session = session_manager.load_session(adventure_folder)
        szene = session.get("aktuelle_szene", {})
        return (
            f"📍 Ort: {szene.get('ort', '?')}\n"
            f"📖 {session.get('story_so_far', '—')}"
        )

    return None


def process_message(
    msg: dict,
    players: dict,
    registered_groups: set,
):
    sender = msg["sender"]
    text = msg["text"].strip()
    group_id = msg["group_id"]
    sender_name = signal_client.get_sender_name(sender, players)

    # Eigene Nachrichten ignorieren
    if sender == SIGNAL_PHONE_NUMBER:
        return

    # Abenteuer bestimmen
    if group_id:
        if group_id not in registered_groups:
            logger.debug(f"Gruppe {group_id} nicht registriert — ignoriert")
            return
        adventure_folder = session_manager.get_adventure_for_group(group_id)
        reply_to = group_id
    else:
        adventure_folder = session_manager.get_adventure_for_player(sender)
        reply_to = sender

    if not adventure_folder:
        logger.warning(f"Kein Abenteuer für {sender_name} ({sender}) gefunden")
        return

    logger.info(f"[{adventure_folder}] {sender_name}: {text}")

    # !Kommandos (nur vom castle assistant / DM)
    if text.startswith("!") and sender == SIGNAL_PHONE_NUMBER:
        response = handle_command(text.lower(), sender, adventure_folder)
        if response:
            signal_client.send(reply_to, response)
        return

    # DM antworten lassen
    time.sleep(RESPONSE_DELAY_SECONDS)
    dm_reply = dm_engine.respond(adventure_folder, sender_name, text)
    signal_client.send(reply_to, dm_reply)


def main():
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    logger.info("ttrpg-bot startet...")

    players = signal_client.load_players()
    logger.info(f"Spieler geladen: {list(players.values())}")

    registered_groups = load_registered_groups()
    logger.info(f"Registrierte Gruppen: {registered_groups or '(noch keine)'}")

    while running:
        envelopes = signal_client.receive()
        for envelope in envelopes:
            msg = signal_client.extract_message(envelope)
            if msg:
                process_message(msg, players, registered_groups)
                signal_client.mark_read(msg["sender"], msg["timestamp"])
        time.sleep(POLL_INTERVAL)

    logger.info("Bot beendet.")


if __name__ == "__main__":
    main()
