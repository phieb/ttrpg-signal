import time
import requests
import yaml
import logging
from pathlib import Path
from config import SIGNAL_CLI_URL, SIGNAL_PHONE_NUMBER, TTRPG_PATH

logger = logging.getLogger(__name__)

_RETRY_DELAYS = [1, 3, 8]  # Sekunden zwischen Versuchen


def _post_with_retry(url: str, **kwargs) -> requests.Response:
    """HTTP POST mit bis zu 3 Wiederholungsversuchen bei transienten Fehlern."""
    last_exc = None
    for attempt, delay in enumerate([0] + _RETRY_DELAYS, 1):
        if delay:
            time.sleep(delay)
        try:
            r = requests.post(url, **kwargs)
            r.raise_for_status()
            return r
        except requests.exceptions.ConnectionError as e:
            last_exc = e
            logger.warning(f"Verbindungsfehler (Versuch {attempt}): {e}")
        except requests.exceptions.HTTPError as e:
            # 5xx → retry, 4xx → sofort abbrechen
            if e.response is not None and e.response.status_code < 500:
                raise
            last_exc = e
            logger.warning(f"HTTP {e.response.status_code} (Versuch {attempt}): {e}")
        except requests.exceptions.Timeout as e:
            last_exc = e
            logger.warning(f"Timeout (Versuch {attempt})")
    raise last_exc


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
    """Sendet eine Nachricht an eine Nummer oder Gruppen-ID (mit Retry)."""
    try:
        _post_with_retry(
            f"{SIGNAL_CLI_URL}/v2/send",
            json={
                "message": message,
                "number": SIGNAL_PHONE_NUMBER,
                "recipients": [recipient],
                "text_mode": "styled",
            },
            timeout=10,
        )
        return True
    except Exception as e:
        logger.error(f"Senden an {recipient} fehlgeschlagen: {e}")
        return False


def send_file(recipient: str, file_path: str, caption: str = "") -> bool:
    """Sendet eine Datei als Signal-Attachment (mit Retry)."""
    try:
        import base64
        with open(file_path, "rb") as f:
            data = base64.b64encode(f.read()).decode()

        _post_with_retry(
            f"{SIGNAL_CLI_URL}/v2/send",
            json={
                "message": caption,
                "number": SIGNAL_PHONE_NUMBER,
                "recipients": [recipient],
                "base64_attachments": [data],
                "text_mode": "styled",
            },
            timeout=30,
        )
        return True
    except Exception as e:
        logger.error(f"Senden von {file_path} an {recipient} fehlgeschlagen: {e}")
        return False


def create_group(name: str, members: list[str]) -> str | None:
    """Erstellt eine neue Signal-Gruppe und gibt die Gruppen-ID zurück."""
    try:
        r = requests.post(
            f"{SIGNAL_CLI_URL}/v1/groups/{SIGNAL_PHONE_NUMBER}",
            json={"name": name, "members": members},
            timeout=15,
        )
        r.raise_for_status()
        group_id = r.json().get("id")
        logger.info(f"Signal-Gruppe erstellt: '{name}' → {group_id}")
        return group_id
    except Exception as e:
        logger.error(f"Gruppe erstellen fehlgeschlagen: {e}")
        return None


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
