import time
import logging
import shutil
import signal
import yaml
from pathlib import Path

import signal_client
import session_manager
import dm_engine
import generate_avatar
from config import SIGNAL_PHONE_NUMBER, ADMIN_PHONE_NUMBER, TTRPG_PATH, RESPONSE_DELAY_SECONDS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

POLL_INTERVAL = 3
TTRPG = Path(TTRPG_PATH)


def handle_signal(sig, frame):
    global running
    logger.info("Shutdown-Signal empfangen, beende Bot...")
    running = False


running = True


# ── Hilfsfunktionen ──────────────────────────────────────────────────────────

def load_registered_groups() -> set:
    try:
        data = yaml.safe_load((TTRPG / "status.yaml").read_text())
        return {a["signal_gruppe"] for a in data.get("abenteuer", []) if a.get("signal_gruppe")}
    except Exception as e:
        logger.warning(f"status.yaml konnte nicht geladen werden: {e}")
        return set()


def find_player_phone(name: str) -> str | None:
    """Gibt die Telefonnummer eines Spielers anhand seines Namens zurück."""
    for f in (TTRPG / "players").glob("*.yaml"):
        try:
            data = yaml.safe_load(f.read_text())
            s = data.get("spieler", {})
            if s.get("name", "").lower() == name.lower():
                return s["telefon"]
        except Exception:
            pass
    return None


# ── Autorisierung ────────────────────────────────────────────────────────────

def is_registered_player(sender: str, players: dict) -> bool:
    return sender in players

# Kommandos die jeder registrierte Spieler nutzen kann
PLAYER_COMMANDS = {"!help", "!charakter"}


# ── Kommandos ─────────────────────────────────────────────────────────────────

def cmd_pause(adventure_folder: str, **_) -> str:
    dm_engine.compress_session(adventure_folder)
    dm_engine.clear_history(adventure_folder)
    return "⏸ Spielstand gespeichert. Bis zum nächsten Mal!"


def cmd_status(adventure_folder: str, **_) -> str:
    session = session_manager.load_session(adventure_folder)
    ort = session.get("aktueller_ort") or session.get("aktuelle_szene", {}).get("ort", "?")
    szene = session.get("letzte_szene") or session.get("aktuelle_szene", {}).get("zusammenfassung", "—")
    quests = [q["name"] for q in session.get("aktive_quests", []) if q.get("status") != "abgeschlossen"]
    lines = [f"📍 *{ort}*", f"_{szene}_"]
    if quests:
        lines.append("🎯 " + " | ".join(quests))
    return "\n".join(lines)


def cmd_neu(args: list, reply_to: str, **_) -> str:
    if not args:
        return "Usage: !neu [abenteuer-name]"

    name = " ".join(args)
    ordner = name.lower().replace(" ", "_")
    adventure_path = TTRPG / "adventures" / ordner

    if adventure_path.exists():
        return f"❌ Ordner '{ordner}' existiert bereits."

    # Ordnerstruktur aus Template kopieren
    template = TTRPG / "adventures" / "_template"
    if template.exists():
        shutil.copytree(template, adventure_path)
    else:
        (adventure_path / "characters").mkdir(parents=True)
        for f in ["session.yaml", "setting.yaml", "npcs.yaml"]:
            (adventure_path / f).write_text("")

    # status.yaml updaten
    status_path = TTRPG / "status.yaml"
    data = yaml.safe_load(status_path.read_text()) or {}
    data.setdefault("abenteuer", []).append({
        "ordner": ordner,
        "name": name,
        "status": "session_0",
        "letzte_szene": "",
        "zuletzt_gespielt": "",
        "signal_gruppe": "",
        "spieler": [],
    })
    status_path.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False))

    logger.info(f"Neues Abenteuer erstellt: {ordner}")
    return f"✅ Abenteuer '{name}' erstellt. Ordner: {ordner}\nJetzt Signal-Gruppe anlegen und ID in status.yaml eintragen."


def cmd_session0(adventure_folder: str, reply_to: str, **_) -> str:
    """Startet Session 0 — DM begrüßt die Gruppe."""
    session = session_manager.load_session(adventure_folder)
    session["status"] = "session_0"
    session_manager.save_session(adventure_folder, session)
    dm_engine.clear_history(adventure_folder)

    # DM Session-0-Eröffnung generieren
    intro = dm_engine.respond(adventure_folder, "System", "Starte Session 0. Begrüße die Spieler und führe durch die Charaktererstellung.")
    signal_client.send(reply_to, intro)
    return None  # DM hat bereits geantwortet


def cmd_dm(args: list, **_) -> str:
    """!dm @Spieler [nachricht] — geheime 1:1 Nachricht an einen Spieler."""
    if len(args) < 2:
        return "Usage: !dm @Spieler [nachricht]"

    spieler_name = args[0].lstrip("@")
    nachricht = " ".join(args[1:])

    telefon = find_player_phone(spieler_name)
    if not telefon:
        return f"❌ Spieler '{spieler_name}' nicht gefunden."

    signal_client.send(telefon, f"📨 Geheime DM-Nachricht:\n{nachricht}")
    return f"✅ Nachricht an {spieler_name} gesendet."


def cmd_help(sender: str, **_) -> str:
    is_admin = sender == ADMIN_PHONE_NUMBER
    lines = ["*Verfügbare Kommandos:*", ""]

    lines += [
        "!charakter — dein Charakterblatt anzeigen",
        "!help — diese Hilfe",
    ]

    if is_admin:
        lines += [
            "",
            "*Admin:*",
            "!status — aktueller Spielstand",
            "!pause — Spielstand speichern & Session beenden",
            "!neu [name] — neues Abenteuer anlegen",
            "!session0 — Session 0 starten",
            "!dm @Spieler [text] — geheime 1:1 Nachricht",
            "!avatare — Charakter-Portraits generieren",
        ]

    return "\n".join(lines)


def cmd_avatare(adventure_folder: str, reply_to: str, **_) -> None:
    """Generiert Avatare für alle Charaktere via Gemini Imagen."""
    generate_avatar.generate_and_send_avatars(adventure_folder, reply_to)
    return None  # generate_and_send_avatars schickt selbst


def _format_charakter(char: dict) -> str:
    c = char.get("charakter", {})
    ident = char.get("identitaet", {})
    skills = char.get("skills", [])
    mot = char.get("motivation", {})
    lines = [
        f"🎭 *{c.get('name', '?')}*",
        f"_{ident.get('wer_bist_du', '')}_",
        f"Aussehen: {ident.get('aussehen', '—')}",
        "",
        "*Skills:* " + ", ".join(s["name"] for s in skills),
        "",
        f"*Will:* {mot.get('will', '—')}",
        f"*Fürchtet:* {mot.get('fuerchtet', '—')}",
    ]
    return "\n".join(lines)


def cmd_charakter(sender: str, args: list, players: dict, **_) -> str:
    """
    !charakter         → alle eigenen Charaktere, oder direkt anzeigen wenn nur einer
    !charakter <name>  → bestimmten Charakter anzeigen
    """
    player_name = signal_client.get_sender_name(sender, players)
    all_chars = session_manager.get_all_characters_for_player(player_name)

    if not all_chars:
        return f"❌ Keine Charaktere für {player_name} gefunden."

    # Mit Name-Argument: direkt anzeigen
    if args:
        char_name = " ".join(args)
        char = session_manager.find_character_by_name(player_name, char_name)
        if not char:
            names = ", ".join(e["char"]["charakter"]["name"] for e in all_chars)
            return f"❌ Charakter '{char_name}' nicht gefunden.\nDeine Charaktere: {names}"
        return _format_charakter(char)

    # Nur ein Charakter → direkt anzeigen
    if len(all_chars) == 1:
        return _format_charakter(all_chars[0]["char"])

    # Mehrere → Liste zurückgeben
    lines = [f"*{player_name}s Charaktere:*", ""]
    for entry in all_chars:
        name = entry["char"].get("charakter", {}).get("name", "?")
        abenteuer = entry["abenteuer"].replace("_", " ").title()
        lines.append(f"• *{name}* _{abenteuer}_")
    lines += ["", "Tippe _!charakter <name>_ um einen anzuzeigen."]
    return "\n".join(lines)


# ── Kommando-Router ───────────────────────────────────────────────────────────

COMMANDS = {
    "!pause":     cmd_pause,
    "!status":    cmd_status,
    "!neu":       cmd_neu,
    "!session0":  cmd_session0,
    "!dm":        cmd_dm,
    "!charakter": cmd_charakter,
    "!avatare":   cmd_avatare,
    "!help":      cmd_help,
}

NEEDS_ADVENTURE = {"!pause", "!status", "!session0", "!avatare"}


def handle_command(text: str, sender: str, adventure_folder: str | None,
                   reply_to: str, players: dict) -> str | None:
    parts = text.split()
    cmd = parts[0].lower()
    args = parts[1:]

    is_admin = sender == ADMIN_PHONE_NUMBER
    is_player = is_registered_player(sender, players)

    handler = COMMANDS.get(cmd)
    if not handler:
        return f"❓ Unbekanntes Kommando: {cmd}"

    # Zugriffscheck
    if cmd not in PLAYER_COMMANDS and not is_admin:
        logger.warning(f"Kommando {cmd} von {sender} verweigert (kein Admin)")
        return None

    if cmd in NEEDS_ADVENTURE and not adventure_folder:
        return "❌ Kein aktives Abenteuer gefunden."

    return handler(
        args=args,
        sender=sender,
        adventure_folder=adventure_folder,
        reply_to=reply_to,
        players=players,
    )


# ── Message Processing ────────────────────────────────────────────────────────

def process_message(msg: dict, players: dict, registered_groups: set):
    sender = msg["sender"]
    text = msg["text"].strip()
    group_id = msg["group_id"]
    sender_name = signal_client.get_sender_name(sender, players)

    # Eigene Nachrichten ignorieren
    if sender == SIGNAL_PHONE_NUMBER:
        return

    # Abenteuer und Reply-Ziel bestimmen
    if group_id:
        if group_id not in registered_groups:
            logger.debug(f"Gruppe {group_id} nicht registriert — ignoriert")
            return
        adventure_folder = session_manager.get_adventure_for_group(group_id)
        reply_to = group_id
    else:
        adventure_folder = session_manager.get_adventure_for_player(sender)
        reply_to = sender

    # Unbekannte Nummern still ignorieren
    if not is_registered_player(sender, players) and sender != ADMIN_PHONE_NUMBER:
        logger.debug(f"Unbekannte Nummer ignoriert: {sender}")
        return

    logger.info(f"[{adventure_folder or '?'}] {sender_name}: {text}")

    # !Kommandos
    if text.startswith("!"):
        response = handle_command(text, sender, adventure_folder, reply_to, players)
        if response:
            signal_client.send(reply_to, response)
        return

    # Kein Abenteuer → ignorieren
    if not adventure_folder:
        logger.debug(f"Kein Abenteuer für {sender_name} — ignoriert")
        return

    # DM antworten lassen
    time.sleep(RESPONSE_DELAY_SECONDS)
    dm_reply = dm_engine.respond(adventure_folder, sender_name, text)
    signal_client.send(reply_to, dm_reply)


# ── Main Loop ─────────────────────────────────────────────────────────────────

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
