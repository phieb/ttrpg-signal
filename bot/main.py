import time
import logging
import shutil
import signal
import yaml
from collections import defaultdict
from pathlib import Path

import signal_client
import session_manager
import dm_engine
import generate_avatar
from config import (
    SIGNAL_PHONE_NUMBER, ADMIN_PHONE_NUMBER, TTRPG_PATH, RESPONSE_DELAY_SECONDS,
    RATE_LIMIT_MESSAGES, RATE_LIMIT_WINDOW,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

POLL_INTERVAL = 3
TTRPG = Path(TTRPG_PATH)

# ── Graceful Shutdown ─────────────────────────────────────────────────────────

running = True
_processing = False  # True während eine Nachricht verarbeitet wird


def handle_signal(sig, frame):
    global running
    logger.info("Shutdown-Signal empfangen — warte auf laufende Verarbeitung...")
    running = False
    if not _processing:
        raise SystemExit(0)


# ── Rate Limiting ─────────────────────────────────────────────────────────────

_rate_timestamps: dict[str, list[float]] = defaultdict(list)


def _is_rate_limited(sender: str) -> bool:
    """True wenn der Absender im letzten Zeitfenster zu viele Nachrichten geschickt hat."""
    now = time.monotonic()
    timestamps = _rate_timestamps[sender]
    # Alte Einträge außerhalb des Fensters entfernen
    _rate_timestamps[sender] = [t for t in timestamps if now - t < RATE_LIMIT_WINDOW]
    if len(_rate_timestamps[sender]) >= RATE_LIMIT_MESSAGES:
        return True
    _rate_timestamps[sender].append(now)
    return False


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
    lines = [f"📍 **{ort}**", f"*{szene}*"]
    if quests:
        lines.append("🎯 " + " | ".join(quests))
    return "\n".join(lines)


def cmd_neu(args: list, reply_to: str, **_) -> str:
    if not args:
        return "Usage: !neu <abenteuer-name> [@Spieler1 @Spieler2 ...]"

    # Args aufteilen: alles vor dem ersten @-Token ist der Abenteuer-Name
    name_parts = []
    spieler_namen = []
    for token in args:
        if token.startswith("@") or spieler_namen:
            spieler_namen.append(token.lstrip("@"))
        else:
            name_parts.append(token)

    if not name_parts:
        return "Usage: !neu <abenteuer-name> [@Spieler1 @Spieler2 ...]"

    name = " ".join(name_parts).strip('"\'„"')
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

    # Spieler-Telefonnummern auflösen
    not_found = []
    spieler_eintraege = []
    member_phones = [ADMIN_PHONE_NUMBER]  # Admin immer dabei

    for sname in spieler_namen:
        telefon = find_player_phone(sname)
        if not telefon:
            not_found.append(sname)
        else:
            spieler_eintraege.append({"name": sname})
            if telefon not in member_phones:
                member_phones.append(telefon)

    # Signal-Gruppe erstellen (wenn Spieler angegeben)
    group_id = ""
    group_msg = ""
    if spieler_namen:
        if not_found:
            group_msg = f"\n⚠️ Nicht gefunden: {', '.join(not_found)} — manuell in status.yaml nachtragen."
        group_id = signal_client.create_group(name, member_phones) or ""
        if group_id:
            group_msg = f"\n✅ Signal-Gruppe erstellt (ID gespeichert).{group_msg}"
        else:
            group_msg = f"\n⚠️ Gruppe konnte nicht erstellt werden — ID manuell in status.yaml eintragen.{group_msg}"

    # status.yaml updaten
    status_path = TTRPG / "status.yaml"
    data = yaml.safe_load(status_path.read_text()) or {}
    data.setdefault("abenteuer", []).append({
        "ordner": ordner,
        "name": name,
        "status": "session_0",
        "letzte_szene": "",
        "zuletzt_gespielt": "",
        "signal_gruppe": group_id,
        "spieler": spieler_eintraege,
    })
    status_path.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False))

    logger.info(f"Neues Abenteuer erstellt: {ordner} (Gruppe: {group_id or '—'})")
    return f"✅ Abenteuer **{name}** erstellt.{group_msg}"


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


def cmd_spiele(**_) -> str:
    """!spiele — alle Abenteuer mit Status anzeigen."""
    status_path = TTRPG / "status.yaml"
    data = yaml.safe_load(status_path.read_text()) or {}
    abenteuer = data.get("abenteuer", [])
    if not abenteuer:
        return "❌ Keine Abenteuer gefunden."

    STATUS_ICON = {
        "session_0": "🌱",
        "aktiv":     "⚔️",
        "pausiert":  "⏸",
        "beendet":   "🏁",
    }
    lines = ["**Abenteuer:**", ""]
    for a in abenteuer:
        icon = STATUS_ICON.get(a.get("status", ""), "📖")
        name = a.get("name", a.get("ordner", "?"))
        spieler = ", ".join(s["name"] for s in a.get("spieler", []))
        zuletzt = a.get("zuletzt_gespielt", "")
        zeile = f"{icon} **{name}**"
        if spieler:
            zeile += f" — {spieler}"
        if zuletzt:
            zeile += f" _{zuletzt}_"
        lines.append(zeile)
    return "\n".join(lines)


def cmd_spiel(args: list, **_) -> str:
    """!spiel <name> — Zusammenfassung eines Abenteuers."""
    if not args:
        return "Usage: !spiel <abenteuer-name>"

    suche = " ".join(args).lower()
    status_path = TTRPG / "status.yaml"
    data = yaml.safe_load(status_path.read_text()) or {}

    # Suche nach Name oder Ordner (Teilstring reicht)
    treffer = None
    for a in data.get("abenteuer", []):
        if suche in a.get("name", "").lower() or suche in a.get("ordner", "").lower():
            treffer = a
            break

    if not treffer:
        return f"❌ Kein Abenteuer gefunden für '{suche}'."

    ordner = treffer["ordner"]
    name = treffer.get("name", ordner)
    status = treffer.get("status", "?")
    zuletzt = treffer.get("zuletzt_gespielt", "—")
    letzte_szene = treffer.get("letzte_szene", "")
    spieler_liste = [s["name"] for s in treffer.get("spieler", [])]

    lines = [f"📖 **{name}**", f"Status: {status} | Zuletzt: {zuletzt or '—'}", ""]

    # Setting laden
    setting = session_manager.load_setting(ordner)
    welt = setting.get("welt", {})
    if welt.get("beschreibung"):
        lines.append(f"*{welt['beschreibung']}*")
        lines.append("")

    konflikt = setting.get("konflikt", {})
    if konflikt.get("hauptbedrohung"):
        lines.append(f"**Konflikt:** {konflikt['hauptbedrohung']}")

    if spieler_liste:
        lines.append(f"**Spieler:** {', '.join(spieler_liste)}")

    # Charaktere im Abenteuer
    chars = session_manager.load_characters(ordner)
    if chars:
        char_namen = [c.get("charakter", {}).get("name", "?") for c in chars]
        lines.append(f"**Charaktere:** {', '.join(char_namen)}")

    if letzte_szene:
        lines.append("")
        lines.append(f"**Letzte Szene:** {letzte_szene}")

    return "\n".join(lines)


def cmd_spieler(**_) -> str:
    """!spieler — alle registrierten Spieler anzeigen."""
    players_dir = TTRPG / "players"
    eintraege = []
    for f in sorted(players_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(f.read_text())
            s = data.get("spieler", {})
            name = s.get("name", f.stem)
            telefon = s.get("telefon", "?")
            rolle = s.get("rolle", "spieler")
            eintraege.append(f"• **{name}** {telefon} _({rolle})_")
        except Exception:
            pass
    if not eintraege:
        return "❌ Keine Spieler registriert."
    lines = ["**Registrierte Spieler:**", ""] + eintraege
    return "\n".join(lines)


def cmd_invite(args: list, **_) -> str:
    """!invite +43... Name — neuen Spieler registrieren."""
    if len(args) < 2:
        return "Usage: !invite +43... Name"

    telefon = args[0]
    name = " ".join(args[1:])

    if not telefon.startswith("+"):
        return "❌ Telefonnummer muss mit + beginnen (z.B. +43...)."

    players_dir = TTRPG / "players"
    slug = name.lower().replace(" ", "_")
    target = players_dir / f"{slug}.yaml"

    if target.exists():
        return f"❌ Spieler '{name}' existiert bereits."

    # Doppelte Nummer prüfen
    for f in players_dir.glob("*.yaml"):
        try:
            data = yaml.safe_load(f.read_text())
            if data.get("spieler", {}).get("telefon") == telefon:
                existing = data["spieler"]["name"]
                return f"❌ Nummer {telefon} ist bereits bei '{existing}' registriert."
        except Exception:
            pass

    target.write_text(yaml.dump(
        {"spieler": {"name": name, "telefon": telefon, "rolle": "spieler"}},
        allow_unicode=True, default_flow_style=False,
    ))
    logger.info(f"Neuer Spieler registriert: {name} ({telefon})")

    # Willkommensnachricht an den neuen Spieler
    willkommen = (
        f"⚔️ Hallo **{name}**!\n\n"
        "Ich bin der **Castle Assistant** — dein digitaler Dungeon Master.\n"
        "Du wurdest als Spieler registriert. Schreib mir hier direkt oder in eurer Abenteurergruppe.\n\n"
        "Tippe *!help* um zu sehen, was du alles fragen kannst. Bis bald am Spieltisch! 🎲"
    )
    signal_client.send(telefon, willkommen)

    return f"✅ **{name}** ({telefon}) registriert und begrüßt."


def cmd_help(sender: str, **_) -> str:
    is_admin = sender == ADMIN_PHONE_NUMBER
    lines = ["**Verfügbare Kommandos:**", ""]

    lines += [
        "!charakter — dein Charakterblatt anzeigen",
        "!help — diese Hilfe",
    ]

    if is_admin:
        lines += [
            "",
            "**Admin:**",
            "!status — aktueller Spielstand",
            "!pause — Spielstand speichern & Session beenden",
            "!neu [name] — neues Abenteuer anlegen",
            "!session0 — Session 0 starten",
            "!dm @Spieler [text] — geheime 1:1 Nachricht",
            "!avatare — Charakter-Portraits generieren",
            "!invite +43... Name — neuen Spieler registrieren",
            "!spieler — alle registrierten Spieler anzeigen",
            "!spiele — alle Abenteuer anzeigen",
            "!spiel <name> — Zusammenfassung eines Abenteuers",
        ]

    return "\n".join(lines)


def cmd_avatare(args: list, adventure_folder: str, reply_to: str, **_) -> None:
    """!avatare → Liste | !avatare <name> → Avatar generieren."""
    char_name = " ".join(args) if args else None
    generate_avatar.generate_and_send_avatars(adventure_folder, reply_to, char_name)
    return None


def _format_charakter(char: dict) -> str:
    c = char.get("charakter", {})
    ident = char.get("identitaet", {})
    skills = char.get("skills", [])
    mot = char.get("motivation", {})
    lines = [
        f"🎭 **{c.get('name', '?')}**",
        f"*{ident.get('wer_bist_du', '')}*",
        f"Aussehen: {ident.get('aussehen', '—')}",
        "",
        "**Skills:** " + ", ".join(s["name"] for s in skills),
        "",
        f"**Will:** {mot.get('will', '—')}",
        f"**Fürchtet:** {mot.get('fuerchtet', '—')}",
    ]
    return "\n".join(lines)


def _char_slug(char_name: str) -> str:
    return char_name.lower().replace(" ", "_")


def _get_or_generate_pdf(adventure_folder: str, char_name: str, char_data: dict) -> Path | None:
    """Gibt den PDF-Pfad zurück — generiert das PDF falls noch nicht vorhanden."""
    slug = _char_slug(char_name)
    chars_dir = TTRPG / "adventures" / adventure_folder / "characters"
    pdf_path = chars_dir / f"{slug}_charakterblatt.pdf"

    if pdf_path.exists():
        return pdf_path

    try:
        import sys
        engine_tools = str(TTRPG / "_engine" / "tools")
        if engine_tools not in sys.path:
            sys.path.insert(0, engine_tools)
        from generate_character_pdf import generate_character_pdf  # noqa: PLC0415
        setting_data = session_manager.load_setting(adventure_folder)
        generate_character_pdf(char_data, setting_data, str(pdf_path))
        logger.info(f"PDF generiert: {pdf_path}")
        return pdf_path
    except Exception as e:
        logger.error(f"PDF-Generierung fehlgeschlagen für {char_name}: {e}")
        return None


def _send_charakter(entry: dict, reply_to: str) -> None:
    """Sendet Charakterblatt-Text + Avatar (falls vorhanden) + PDF."""
    char = entry["char"]
    adventure_folder = entry["abenteuer"]
    char_name = char.get("charakter", {}).get("name", "?")
    slug = _char_slug(char_name)

    signal_client.send(reply_to, _format_charakter(char))

    # Avatar senden wenn bereits generiert
    avatar_path = TTRPG / "adventures" / adventure_folder / "characters" / f"{slug}_avatar.png"
    if avatar_path.exists():
        signal_client.send_file(reply_to, str(avatar_path), f"🎭 {char_name}")

    # PDF generieren (falls nötig) und senden
    pdf_path = _get_or_generate_pdf(adventure_folder, char_name, char)
    if pdf_path:
        signal_client.send_file(reply_to, str(pdf_path), f"📜 Charakterblatt {char_name}")


def cmd_charakter(sender: str, args: list, players: dict, reply_to: str, **_) -> None:
    """
    !charakter         → alle eigenen Charaktere, oder direkt anzeigen wenn nur einer
    !charakter <name>  → bestimmten Charakter anzeigen (mit Avatar + PDF)
    """
    player_name = signal_client.get_sender_name(sender, players)
    all_chars = session_manager.get_all_characters_for_player(player_name)

    if not all_chars:
        signal_client.send(reply_to, f"❌ Keine Charaktere für {player_name} gefunden.")
        return None

    # Mit Name-Argument: direkt anzeigen
    if args:
        char_name = " ".join(args)
        entry = session_manager.find_character_entry_by_name(player_name, char_name)
        if not entry:
            names = ", ".join(e["char"]["charakter"]["name"] for e in all_chars)
            signal_client.send(reply_to, f"❌ Charakter '{char_name}' nicht gefunden.\nDeine Charaktere: {names}")
            return None
        _send_charakter(entry, reply_to)
        return None

    # Nur ein Charakter → direkt anzeigen
    if len(all_chars) == 1:
        _send_charakter(all_chars[0], reply_to)
        return None

    # Mehrere → Liste zurückgeben
    lines = [f"**{player_name}s Charaktere:**", ""]
    for entry in all_chars:
        name = entry["char"].get("charakter", {}).get("name", "?")
        abenteuer = entry["abenteuer"].replace("_", " ").title()
        lines.append(f"• **{name}** *{abenteuer}*")
    lines += ["", "Tippe *!charakter <name>* um einen anzuzeigen."]
    signal_client.send(reply_to, "\n".join(lines))
    return None


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
    "!invite":    cmd_invite,
    "!spieler":   cmd_spieler,
    "!spiele":    cmd_spiele,
    "!spiel":     cmd_spiel,
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
    global _processing
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

    # Rate Limiting (Kommandos ausgenommen — nur Spielnachrichten drosseln)
    if not text.startswith("!") and _is_rate_limited(sender):
        logger.warning(f"Rate limit für {sender_name} — Nachricht ignoriert")
        return

    logger.info(f"[{adventure_folder or '?'}] {sender_name}: {text}")

    _processing = True
    try:
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
    except Exception as e:
        logger.error(f"Fehler bei Nachrichtenverarbeitung von {sender_name}: {e}", exc_info=True)
    finally:
        _processing = False
        if not running:
            raise SystemExit(0)


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
