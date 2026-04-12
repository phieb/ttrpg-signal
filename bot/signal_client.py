import requests
import yaml
import logging
from pathlib import Path
from config import SIGNAL_CLI_URL, SIGNAL_PHONE_NUMBER, TTRPG_PATH

logger = logging.getLogger(__name__)


def load_players() -> dict:
    """Lädt alle Spieler aus /players/*.yaml und gibt telefon→name dict zurück."""
    players = {}
    players_dir = Path(TTRPG_PATH) / "players"
    for f in players_dir.glob("*.yaml"):
        try:
            data = yaml.safe_load(f.read_text())
            telefon = data["spieler"]["telefon"]
            name = data["spieler"]["name"]
            players[telefon] = name
        except Exception as e:
            logger.warning(f"Spieler-Datei {f.name} konnte nicht geladen werden: {e}")
    return players


def get_sender_name(telefon: str, players: dict) -> str:
    return players.get(telefon, telefon)


def receive() -> list:
    """Holt neue Nachrichten vom signal-cli via HTTP polling."""
    try:
        r = requests.get(
            f"{SIGNAL_CLI_URL}/v1/receive/{SIGNAL_PHONE_NUMBER}",
            timeout=10,
        )
        r.raise_for_status()
        return r.json() or []
    except requests.exceptions.RequestException as e:
        logger.error(f"Fehler beim Empfangen: {e}")
        return []


def send(recipient: str, message: str) -> bool:
    """Sendet eine Nachricht an eine Nummer oder Gruppen-ID."""
    try:
        r = requests.post(
            f"{SIGNAL_CLI_URL}/v2/send",
            json={
                "message": message,
                "number": SIGNAL_PHONE_NUMBER,
                "recipients": [recipient],
            },
            timeout=10,
        )
        r.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Fehler beim Senden an {recipient}: {e}")
        return False


def send_file(recipient: str, file_path: str, caption: str = "") -> bool:
    """Sendet eine Datei (z.B. Avatar-PNG) als Signal-Attachment."""
    try:
        with open(file_path, "rb") as f:
            import base64
            data = base64.b64encode(f.read()).decode()

        payload = {
            "message": caption,
            "number": SIGNAL_PHONE_NUMBER,
            "recipients": [recipient],
            "base64_attachments": [data],
        }
        r = requests.post(f"{SIGNAL_CLI_URL}/v2/send", json=payload, timeout=30)
        r.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Fehler beim Senden von {file_path} an {recipient}: {e}")
        return False


def mark_read(sender: str, timestamp: int) -> None:
    """Markiert eine Nachricht als gelesen."""
    try:
        r = requests.post(
            f"{SIGNAL_CLI_URL}/v1/receipts/{SIGNAL_PHONE_NUMBER}",
            json={
                "receipt_type": "read",
                "recipient": sender,
                "timestamp": timestamp,
            },
            timeout=10,
        )
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.warning(f"mark_read fehlgeschlagen für {sender}: {e}")


def extract_message(envelope: dict) -> dict | None:
    """Extrahiert relevante Felder aus einem Signal-Envelope."""
    try:
        source = envelope.get("envelope", {}).get("source", "")
        data_message = envelope.get("envelope", {}).get("dataMessage", {})
        if not data_message:
            return None

        group_info = data_message.get("groupInfo", None)
        text = data_message.get("message", None)

        if not text:
            return None

        return {
            "sender": source,
            "text": text,
            "group_id": group_info.get("groupId") if group_info else None,
            "timestamp": data_message.get("timestamp", 0),
        }
    except Exception as e:
        logger.warning(f"Envelope konnte nicht geparst werden: {e}")
        return None
