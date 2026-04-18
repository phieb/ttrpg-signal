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
import usage_tracker
from config import (
    SIGNAL_PHONE_NUMBER, ADMIN_PHONE_NUMBER, TTRPG_PATH, RESPONSE_DELAY_SECONDS,
    RATE_LIMIT_MESSAGES, RATE_LIMIT_WINDOW, BATCH_WINDOW_SECONDS,
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


# ── Nachrichten-Batching ──────────────────────────────────────────────────────
# Pro Abenteuer werden eingehende DM-Nachrichten gesammelt.
# Erst wenn BATCH_WINDOW_SECONDS nach der letzten Nachricht vergangen sind,
# antwortet der DM auf alle gesammelten Nachrichten auf einmal.

_batch_messages: dict[str, list[tuple[str, str]]] = defaultdict(list)  # folder → [(name, text)]
_batch_deadline: dict[str, float] = {}   # folder → monotonic-Zeitstempel
_batch_reply_to: dict[str, str] = {}     # folder → reply_to
_batch_senders: dict[str, set[str]] = defaultdict(set)  # folder → Menge der Absender im aktuellen Batch

# ── Auto-Compress nach Inaktivität ───────────────────────────────────────────
AUTO_COMPRESS_SECONDS = 15 * 60  # 15 Minuten
_last_activity: dict[str, float] = {}   # folder → letzter Nachrichten-Zeitstempel
_auto_compressed: set[str] = set()      # Abenteuer die bereits auto-komprimiert wurden


def _get_adventure_players(adventure_folder: str) -> set[str]:
    """Gibt die Menge der Spielernamen (lowercase) eines Abenteuers zurück."""
    try:
        status = yaml.safe_load((TTRPG / "status.yaml").read_text()) or {}
        for a in status.get("abenteuer", []):
            if a.get("ordner") == adventure_folder:
                return {s["name"].lower() for s in a.get("spieler", [])}
    except Exception:
        pass
    return set()


_session0_finalized: set[str] = set()  # Abenteuer die bereits finalisiert wurden


def _finalize_session_0(folder: str, reply_to: str) -> None:
    """Avatare generieren, Sheets senden, Session auf aktiv setzen."""
    global _session0_finalized
    if folder in _session0_finalized:
        return
    _session0_finalized.add(folder)

    logger.info(f"[{folder}] Session 0 wird abgeschlossen")
    signal_client.send(reply_to, "✅ Alle Charaktere sind vollständig! Ich generiere die Portraits...")

    chars = session_manager.load_characters(folder)
    for char in chars:
        char_name = char.get("charakter", {}).get("name", "")
        if not char_name:
            continue
        avatar_path = generate_avatar.generate_avatar(folder, char_name)
        if avatar_path:
            # Avatar ist jetzt vorhanden — _send_charakter schickt Text + Avatar + PDF
            pass
        _send_charakter({"char": char, "abenteuer": folder}, reply_to)

    # Session-Status aktualisieren
    session = session_manager.load_session(folder)
    session["status"] = "aktiv"
    session_manager.save_session(folder, session)

    status_path = TTRPG / "status.yaml"
    try:
        data = yaml.safe_load(status_path.read_text()) or {}
        for a in data.get("abenteuer", []):
            if a.get("ordner") == folder:
                a["status"] = "aktiv"
                break
        status_path.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False))
    except Exception as e:
        logger.warning(f"status.yaml Update fehlgeschlagen: {e}")

    signal_client.send(reply_to, "⚔️ Session 0 abgeschlossen — das Abenteuer kann beginnen!")
    logger.info(f"[{folder}] Session 0 abgeschlossen")


def _maybe_finalize_session_0(folder: str, reply_to: str) -> None:
    """
    Nach jedem DM-Zug in Session 0:
    1. Versuche Charaktere aus der History zu extrahieren und als YAML zu speichern.
    2. Wenn alle vollständig → finalisieren.
    """
    if folder in _session0_finalized:
        return

    player_names = session_manager.get_adventure_player_names_proper(folder)
    if not player_names:
        return

    # Extraktion versuchen (überschreibt bestehende YAMLs mit aktuelleren Daten)
    extracted = dm_engine.extract_characters_from_history(folder, player_names)
    for player_name, char_data in extracted.items():
        if char_data.get("name"):
            session_manager.save_character(folder, player_name, char_data)

    # Vollständigkeit prüfen
    missing = session_manager.check_character_completeness(folder, player_names)
    if not missing:
        _finalize_session_0(folder, reply_to)
    else:
        logger.info(f"[{folder}] Noch nicht vollständig: {missing}")


def _flush_batches() -> None:
    """Verarbeitet alle Batches deren Wartezeit abgelaufen ist."""
    global _processing
    now = time.monotonic()
    for folder in list(_batch_deadline):
        if now < _batch_deadline[folder]:
            continue

        messages = _batch_messages.pop(folder)
        del _batch_deadline[folder]
        reply_to = _batch_reply_to.pop(folder)
        _batch_senders.pop(folder, None)

        # Aktivität registrieren — setzt Auto-Compress-Timer zurück
        _last_activity[folder] = now
        _auto_compressed.discard(folder)

        # Mehrere Nachrichten zusammenfassen
        if len(messages) == 1:
            sender_name, text = messages[0]
            combined = text
        else:
            sender_name = "Gruppe"
            combined = "\n".join(f"**{n}:** {t}" for n, t in messages)

        # Session-Status laden
        session = session_manager.load_session(folder)
        is_session_0 = session.get("status") == "session_0" and folder not in _session0_finalized

        # Während Session 0: Charaktervollständigkeit prüfen und DM hinweisen
        if is_session_0:
            try:
                player_names = list(_get_adventure_players(folder))
                if player_names:
                    missing = session_manager.check_character_completeness(folder, player_names)
                    if missing:
                        hint = "[System: Folgende Charakterinfos fehlen noch — frage gezielt nach:]"
                        for player, gaps in missing.items():
                            hint += f"\n- {player}: {', '.join(gaps)}"
                        combined += f"\n\n{hint}"
                        logger.info(f"[{folder}] Charakterlücken: {missing}")
            except Exception as e:
                logger.warning(f"[{folder}] Vollständigkeitsprüfung fehlgeschlagen: {e}")

        _processing = True
        try:
            time.sleep(RESPONSE_DELAY_SECONDS)
            phase = "SESSION_ZERO" if is_session_0 else "DUNGEON_MASTER"
            dm_reply = dm_engine.respond(folder, sender_name, combined, phase=phase)
            signal_client.send(reply_to, dm_reply)

            # Session 0: Charaktere aus History extrahieren und ggf. finalisieren
            if is_session_0:
                _maybe_finalize_session_0(folder, reply_to)
        except Exception as e:
            logger.error(f"[{folder}] Batch-Verarbeitung fehlgeschlagen: {e}", exc_info=True)
        finally:
            _processing = False
            if not running:
                raise SystemExit(0)

    # Auto-Compress: session.yaml stille aktualisieren nach 15min Inaktivität
    for folder, last in list(_last_activity.items()):
        if folder in _auto_compressed:
            continue
        if folder in _batch_deadline:
            continue  # Batch läuft noch
        if now - last < AUTO_COMPRESS_SECONDS:
            continue
        if not dm_engine._history.get(folder):
            continue  # Keine neue History seit letzter Pause
        try:
            dm_engine.compress_session(folder, detailed=False)
            _auto_compressed.add(folder)
            logger.info(f"[{folder}] Auto-Compress nach Inaktivität")
        except Exception as e:
            logger.warning(f"[{folder}] Auto-Compress fehlgeschlagen: {e}")


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
        groups = set()
        for a in data.get("abenteuer", []):
            if a.get("signal_gruppe"):
                groups.add(a["signal_gruppe"])
            for s in a.get("spieler", []):
                if s.get("private_gruppe"):
                    groups.add(s["private_gruppe"])
        return groups
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
PLAYER_COMMANDS = {"!help", "!charakter", "!avatar"}


# ── Kommandos ─────────────────────────────────────────────────────────────────

def cmd_usage(**_) -> str:
    return usage_tracker.get_summary()


def cmd_showme(adventure_folder: str, reply_to: str, args: list, **_) -> None:
    hint = " ".join(args) if args else ""
    generate_avatar.generate_scene_image(adventure_folder, reply_to, hint=hint)


def cmd_save(adventure_folder: str, **_) -> str:
    dm_engine.compress_session(adventure_folder, detailed=True)
    dm_engine.clear_history(adventure_folder)
    return "💾 Saved. See you next time!"


def cmd_status(adventure_folder: str, **_) -> str:
    session = session_manager.load_session(adventure_folder)
    ort = session.get("aktueller_ort") or session.get("aktuelle_szene", {}).get("ort", "?")
    szene = session.get("letzte_szene") or session.get("aktuelle_szene", {}).get("zusammenfassung", "—")
    quests = [q["name"] for q in session.get("aktive_quests", []) if q.get("status") != "abgeschlossen"]
    lines = [f"📍 **{ort}**", f"*{szene}*"]
    if quests:
        lines.append("🎯 " + " | ".join(quests))
    return "\n".join(lines)


def cmd_new(args: list, reply_to: str, **_) -> str:
    if not args:
        return "Usage: !new <adventure-name> [@Player1 ...] [--flag1 --flag2 ...]"

    # Args aufteilen: Name | @Spieler-Token | --flag-Token (Reihenfolge egal)
    name_parts = []
    spieler_namen = []
    flag_namen = []
    in_name = True
    for token in args:
        # Flags erkennen: -- oder Signal-Autokorrektur — oder –
        stripped = token.lstrip("-–—")
        if stripped != token and stripped:
            in_name = False
            flag_namen.append(stripped.replace("-", "_"))
        elif token.startswith("@"):
            in_name = False
            spieler_namen.append(token.lstrip("@"))
        elif in_name:
            name_parts.append(token)
        else:
            spieler_namen.append(token)

    if not name_parts:
        return "Usage: !new <adventure-name> [@Player1 ...] [--flag1 --flag2 ...]"

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

    # Flags in setting.yaml schreiben
    if flag_namen:
        setting_path = adventure_path / "setting.yaml"
        setting = yaml.safe_load(setting_path.read_text()) or {} if setting_path.exists() else {}
        flags = setting.setdefault("flags", {})
        for flag in flag_namen:
            flags[flag] = True
        setting_path.write_text(yaml.dump(setting, allow_unicode=True, default_flow_style=False))
        logger.info(f"[{ordner}] Flags gesetzt: {flag_namen}")

    # Spieler-Telefonnummern auflösen
    not_found = []
    spieler_eintraege = []
    # Bot-Nummer + Admin immer dabei — Bot muss explizit rein damit signal-cli
    # die Gruppe auch bei nur einem weiteren Mitglied (Solo-Abenteuer) akzeptiert
    member_phones = list({SIGNAL_PHONE_NUMBER, ADMIN_PHONE_NUMBER})

    for sname in spieler_namen:
        telefon = find_player_phone(sname)
        if not telefon:
            not_found.append(sname)
        else:
            spieler_eintraege.append({"name": sname, "telefon": telefon})
            if telefon not in member_phones:
                member_phones.append(telefon)

    # Spielgruppe erstellen (wenn Spieler angegeben)
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

    # status.yaml updaten — Spieler ohne Telefon im Eintrag (nur Name + setup-Felder)
    status_path = TTRPG / "status.yaml"
    data = yaml.safe_load(status_path.read_text()) or {}
    status_spieler = [
        {"name": s["name"], "charakter": "", "setup_status": "invited", "private_gruppe": ""}
        for s in spieler_eintraege
    ]
    data.setdefault("abenteuer", []).append({
        "ordner": ordner,
        "name": name,
        "status": "setup",
        "letzte_szene": "",
        "zuletzt_gespielt": "",
        "signal_gruppe": group_id,
        "spieler": status_spieler,
    })
    status_path.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False))
    logger.info(f"Neues Abenteuer erstellt: {ordner} (Gruppe: {group_id or '—'})")

    # Privaten Setup-Kanal pro Spieler erstellen + CHARACTER_SETUP starten
    setup_msgs = []
    for s in spieler_eintraege:
        sname, stelefon = s["name"], s["telefon"]
        private_phones = list({SIGNAL_PHONE_NUMBER, ADMIN_PHONE_NUMBER, stelefon})
        private_group_id = signal_client.create_group(f"{name} — {sname}", private_phones)
        if private_group_id:
            session_manager.set_player_private_gruppe(ordner, sname, private_group_id)
            intro = dm_engine.respond_setup(
                ordner, sname,
                f"setup_start — Spieler {sname} wurde eingeladen. Starte die Einladungsphase."
            )
            signal_client.send(private_group_id, intro)
            logger.info(f"[{ordner}] Privater Setup-Kanal für {sname}: {private_group_id}")
        else:
            setup_msgs.append(f"⚠️ Setup-Kanal für {sname} konnte nicht erstellt werden.")
            logger.warning(f"[{ordner}] Privater Setup-Kanal für {sname} fehlgeschlagen")

    if setup_msgs:
        group_msg += "\n" + "\n".join(setup_msgs)

    # Spielgruppe informieren (noch kein !session0 — erst wenn alle ready)
    if group_id:
        signal_client.send(
            group_id,
            f"⚔️ **{name}** wurde erstellt!\n\n"
            "*Die Charaktererstellung läuft gerade privat — jeder Spieler bekommt seinen eigenen Kanal.*\n\n"
            "Sobald alle bereit sind geht es hier weiter. 🎲"
        )

    return f"✅ Abenteuer **{name}** erstellt.{group_msg}"


def cmd_session0(adventure_folder: str, reply_to: str, **_) -> str:
    """Startet Session 0 — DM begrüßt die Gruppe."""
    session = session_manager.load_session(adventure_folder)
    session["status"] = "session_0"
    session_manager.save_session(adventure_folder, session)
    dm_engine.clear_history(adventure_folder)

    # DM Session-0-Eröffnung generieren
    intro = dm_engine.respond(adventure_folder, "System", "Starte Session 0. Begrüße die Spieler — die Charaktere sind bereits erstellt.", phase="SESSION_ZERO")
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


def cmd_adventures(**_) -> str:
    """!adventures — alle Abenteuer mit Status anzeigen."""
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


def cmd_adventure(args: list, **_) -> str:
    """!adventure <name> — Zusammenfassung eines Abenteuers."""
    if not args:
        return "Usage: !adventure <name>"

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


def cmd_players(**_) -> str:
    """!players — alle registrierten Spieler anzeigen."""
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
    """!invite +43... Name — neuen Spieler global registrieren."""
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

    willkommen = (
        f"⚔️ Hallo **{name}**!\n\n"
        "Ich bin euer digitaler Dungeon Master.\n"
        "Du wurdest registriert — sobald ein Abenteuer für dich bereit ist, melde ich mich hier.\n\n"
        "Tippe *!help* um zu sehen was du fragen kannst. Bis bald! 🎲"
    )
    signal_client.send(telefon, willkommen)

    return f"✅ **{name}** ({telefon}) registriert."


def cmd_help(sender: str, **_) -> str:
    is_admin = sender == ADMIN_PHONE_NUMBER
    lines = ["**Verfügbare Kommandos:**", ""]

    lines += [
        "!charakter — show your character sheet",
        "!avatar — show / regenerate your portrait",
        "!help — this help",
    ]

    if is_admin:
        lines += [
            "",
            "**Admin:**",
            "!status — current game state",
            "!save — save game & end session",
            "!new <name> [@Player ...] [--flag ...] — create new adventure",
            "!session0 — start Session 0",
            "!dm @Player [text] — secret 1:1 message to a player",
            "!invite +43... Name — register new player",
            "!players — list all registered players",
            "!adventures — list all adventures",
            "!adventure <name> — summary of an adventure",
            "!usage — API usage & estimated costs",
            "!showme [idea] — generate & send an atmospheric scene image",
        ]

    return "\n".join(lines)


def cmd_avatar(sender: str, args: list, players: dict, adventure_folder: str, reply_to: str, **_) -> None:
    """Im Gruppenchat: direkt den eigenen Charakter generieren/anzeigen."""
    if args:
        char_name = " ".join(args)
    else:
        # Eigenen Charakter im Abenteuer ermitteln
        player_name = signal_client.get_sender_name(sender, players)
        char = session_manager.get_character_for_player(adventure_folder, player_name)
        char_name = char.get("charakter", {}).get("name") if char else None
        if not char_name:
            signal_client.send(reply_to, f"❌ Kein Charakter für {player_name} in diesem Abenteuer gefunden.")
            return None

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


def cmd_charakter(sender: str, args: list, players: dict, reply_to: str,
                   adventure_folder: str | None = None, **_) -> None:
    """
    Im Gruppenchat: Charakter des Spielers für dieses Abenteuer anzeigen.
    Im 1:1 Chat:    Alle Charaktere des Spielers durchsuchen.
    """
    player_name = signal_client.get_sender_name(sender, players)

    # Gruppenchat → nur dieses Abenteuer
    if adventure_folder:
        char = session_manager.get_character_for_player(adventure_folder, player_name)
        if not char:
            signal_client.send(reply_to, f"❌ Kein Charakter für {player_name} in diesem Abenteuer gefunden.")
            return None
        _send_charakter({"char": char, "abenteuer": adventure_folder}, reply_to)
        return None

    # 1:1 Chat → alle Abenteuer durchsuchen
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

    # Mehrere → Liste
    lines = [f"**{player_name}s Charaktere:**", ""]
    for entry in all_chars:
        name = entry["char"].get("charakter", {}).get("name", "?")
        abenteuer = entry["abenteuer"].replace("_", " ").title()
        lines.append(f"• **{name}** *{abenteuer}*")
    lines += ["", "Type *!charakter <name>* to show one."]
    signal_client.send(reply_to, "\n".join(lines))
    return None


# ── Kommando-Router ───────────────────────────────────────────────────────────

COMMANDS = {
    "!save":        cmd_save,
    "!status":      cmd_status,
    "!new":         cmd_new,
    "!session0":    cmd_session0,
    "!dm":          cmd_dm,
    "!charakter":   cmd_charakter,
    "!avatar":      cmd_avatar,
    "!help":        cmd_help,
    "!invite":      cmd_invite,
    "!players":     cmd_players,
    "!adventures":  cmd_adventures,
    "!adventure":   cmd_adventure,
    "!usage":       cmd_usage,
    "!showme":      cmd_showme,
}

NEEDS_ADVENTURE = {"!save", "!status", "!session0", "!avatar", "!showme"}


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

def _handle_setup_message(adventure_folder: str, player_name: str, text: str, reply_to: str) -> None:
    """Verarbeitet eine Nachricht im privaten CHARACTER_SETUP Kanal."""
    global _processing
    _processing = True
    try:
        time.sleep(RESPONSE_DELAY_SECONDS)
        dm_reply = dm_engine.respond_setup(adventure_folder, player_name, text)
        signal_client.send(reply_to, dm_reply)

        # Charakter aus History extrahieren und speichern
        char_data = dm_engine.extract_character_from_setup_history(adventure_folder, player_name)
        if char_data.get("name"):
            session_manager.save_character(adventure_folder, player_name, char_data)

        # Prüfen ob der DM "ready" signalisiert hat (einfache Heuristik: "bereit" im Reply)
        if "bereit" in dm_reply.lower() and char_data.get("name"):
            session_manager.set_player_setup_status(adventure_folder, player_name, "ready")
            logger.info(f"[{adventure_folder}] {player_name} Setup abgeschlossen")
            # Avatar generieren
            avatar_path = generate_avatar.generate_avatar(adventure_folder, char_data["name"])
            if avatar_path:
                signal_client.send_file(reply_to, str(avatar_path), f"🎭 {char_data['name']}")
            # Wenn alle Spieler ready → Gruppe benachrichtigen
            if session_manager.all_players_ready(adventure_folder):
                _notify_group_all_ready(adventure_folder)

    except Exception as e:
        logger.error(f"[{adventure_folder}/setup/{player_name}] Fehler: {e}", exc_info=True)
    finally:
        _processing = False
        if not running:
            raise SystemExit(0)


def _notify_group_all_ready(adventure_folder: str) -> None:
    """Setzt Abenteuer-Status auf session_0 und benachrichtigt den Gruppenkanal."""
    try:
        status_path = TTRPG / "status.yaml"
        data = yaml.safe_load(status_path.read_text()) or {}
        for a in data.get("abenteuer", []):
            if a.get("ordner") == adventure_folder:
                a["status"] = "session_0"
                status_path.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False))
                group_id = a.get("signal_gruppe")
                if group_id:
                    signal_client.send(
                        group_id,
                        "⚔️ Alle Charaktere sind bereit — Session 0 kann starten!\n"
                        "Schreibt *!session0* um loszulegen."
                    )
                return
    except Exception as e:
        logger.warning(f"[{adventure_folder}] Gruppen-Benachrichtigung fehlgeschlagen: {e}")


def process_message(msg: dict, players: dict, registered_groups: set):
    global _processing
    sender = msg["sender"]
    text = msg["text"].strip()
    group_id = msg["group_id"]
    sender_name = signal_client.get_sender_name(sender, players)

    # Eigene Nachrichten ignorieren
    if sender == SIGNAL_PHONE_NUMBER:
        return

    # Unbekannte Nummern still ignorieren
    if not is_registered_player(sender, players) and sender != ADMIN_PHONE_NUMBER:
        logger.debug(f"Unbekannte Nummer ignoriert: {sender}")
        return

    # Privaten Setup-Kanal prüfen (vor allem anderen — eindeutige Zuordnung per Gruppen-ID)
    if group_id:
        setup_ctx = session_manager.get_setup_context_for_group(group_id)
        if setup_ctx:
            adventure_folder, player_name = setup_ctx
            logger.info(f"[{adventure_folder}/setup/{player_name}] {sender_name}: {text}")
            if not text.startswith("!"):
                if not _is_rate_limited(sender):
                    _handle_setup_message(adventure_folder, player_name, text, group_id)
                else:
                    logger.warning(f"Rate limit für {sender_name} im Setup — ignoriert")
            return  # Setup-Kanal: keine Kommandos, kein Batching

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

    # Rate Limiting (Kommandos ausgenommen — nur Spielnachrichten drosseln)
    if not text.startswith("!") and _is_rate_limited(sender):
        logger.warning(f"Rate limit für {sender_name} — Nachricht ignoriert")
        return

    logger.info(f"[{adventure_folder or '?'}] {sender_name}: {text}")

    # !Kommandos sofort verarbeiten
    if text.startswith("!"):
        _processing = True
        try:
            response = handle_command(text, sender, adventure_folder, reply_to, players)
            if response:
                signal_client.send(reply_to, response)
        except Exception as e:
            logger.error(f"Fehler bei Kommando von {sender_name}: {e}", exc_info=True)
        finally:
            _processing = False
            if not running:
                raise SystemExit(0)
        return

    # Kein Abenteuer → ignorieren
    if not adventure_folder:
        logger.debug(f"Kein Abenteuer für {sender_name} — ignoriert")
        return

    # Nachricht in Batch-Puffer
    _batch_messages[adventure_folder].append((sender_name, text))
    _batch_senders[adventure_folder].add(sender_name.lower())
    _batch_deadline[adventure_folder] = time.monotonic() + BATCH_WINDOW_SECONDS
    _batch_reply_to[adventure_folder] = reply_to

    # Activity-Tracking für Auto-Compress
    _last_activity[adventure_folder] = time.monotonic()
    _auto_compressed.discard(adventure_folder)

    # Sofort verarbeiten wenn: Solo-Abenteuer (1 Spieler) oder alle Spieler geantwortet haben
    expected = _get_adventure_players(adventure_folder)
    if expected and (len(expected) == 1 or _batch_senders[adventure_folder] >= expected):
        _batch_deadline[adventure_folder] = 0
        reason = "Solo-Abenteuer" if len(expected) == 1 else "alle Spieler haben geantwortet"
        logger.info(f"[{adventure_folder}] {reason} — sofortige Verarbeitung")
    else:
        count = len(_batch_messages[adventure_folder])
        logger.info(f"[{adventure_folder}] Gepuffert ({count} Nachricht{'en' if count > 1 else ''}), DM antwortet in ~{BATCH_WINDOW_SECONDS}s")


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
        # Gruppen + Spieler bei jedem Durchlauf neu laden — neue Abenteuer/Spieler
        # werden sonst erst nach einem Bot-Neustart erkannt
        registered_groups = load_registered_groups()
        players = signal_client.load_players()

        envelopes = signal_client.receive()
        for envelope in envelopes:
            msg = signal_client.extract_message(envelope)
            if msg:
                process_message(msg, players, registered_groups)
                signal_client.mark_read(msg["sender"], msg["timestamp"])
        _flush_batches()
        time.sleep(POLL_INTERVAL)

    logger.info("Bot beendet.")


if __name__ == "__main__":
    main()
