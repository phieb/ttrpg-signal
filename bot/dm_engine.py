import json
import logging
from datetime import datetime
from pathlib import Path
from collections import defaultdict

import anthropic

import session_manager
import usage_tracker
from config import ANTHROPIC_API_KEY, TTRPG_PATH, MAX_CONTEXT_TOKENS, HISTORY_MESSAGES, MAX_LOG_LINES

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"
TTRPG = Path(TTRPG_PATH)
SESSION_SAVE_INTERVAL = 10  # alle N Interaktionen session.yaml updaten

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# Konversations-History pro Abenteuer
# { adventure_folder: [ {"role": "user"|"assistant", "content": "..."}, ... ] }
_history: dict[str, list] = defaultdict(list)

# Interaktions-Zähler pro Abenteuer
_interaction_count: dict[str, int] = defaultdict(int)


def _load_engine_file(filename: str, fallback: str = "") -> str:
    """Lädt eine Datei aus _engine/ mit optionalem Fallback-Text."""
    path = TTRPG / "_engine" / filename
    try:
        return path.read_text()
    except Exception as e:
        logger.error(f"{filename} konnte nicht geladen werden: {e}")
        return fallback


def _load_dm_prompt() -> str:
    return _load_engine_file("DUNGEON_MASTER.md", "Du bist ein Dungeon Master. Führe die Spieler durch ein Abenteuer.")


def _load_character_setup_prompt() -> str:
    return _load_engine_file("CHARACTER_SETUP.md", "Du führst ein privates Charaktererstellungs-Gespräch.")


def _load_top_rules() -> str:
    return _load_engine_file("TOP_DM_REGELN.md")


def _load_flag_prompts(adventure_folder: str, phase: str = "DUNGEON_MASTER") -> str:
    """
    Lädt alle aktiven Flag-Prompts für die angegebene Phase.
    Sucht in flags/[flag]/[phase].md — z.B. flags/booktok/CHARACTER_SETUP.md
    Jeder Flag lebt in einem eigenen Unterordner.
    """
    flags = session_manager.load_flags(adventure_folder)
    flags_dir = TTRPG / "_engine" / "flags"
    parts = []
    for flag, enabled in flags.items():
        if not enabled:
            continue
        prompt_path = flags_dir / flag / f"{phase}.md"
        if prompt_path.exists():
            parts.append(prompt_path.read_text())
        else:
            logger.debug(f"Kein {phase}.md für Flag '{flag}' — übersprungen")
    return "\n\n".join(parts)


_SIGNAL_INSTRUCTIONS = (
    "\n\n---\n\n"
    "## WICHTIG — Technischer Kontext\n\n"
    "Du läufst als Signal-Bot. Du hast KEINEN direkten Dateizugriff. "
    "Alle relevanten Spieldaten wurden bereits aus den YAML-Dateien geladen "
    "und sind unten eingebettet. "
    "Erwähne niemals Dateien oder fehlenden Zugriff. "
    "Steige direkt in die Szene bzw. das Gespräch ein.\n\n"
    "FORMATIERUNG: Du schreibst in Signal (text_mode=styled). Verwende ausschließlich:\n"
    "- **fett** für wichtige Begriffe, Ortsnamen, NSC-Namen\n"
    "- *kursiv* für atmosphärische Beschreibungen, Gedanken, Flüstern\n"
    "- Keine Markdown-Header (##), keine HTML, kein Underscore-Italic\n"
    "- Emojis sparsam einsetzen, nur wenn sie zur Atmosphäre passen"
)


def _build_system(adventure_folder: str, phase: str = "DUNGEON_MASTER") -> list:
    """System-Blöcke für reguläres Spiel und Session 0 (Gruppenkanal)."""
    dm_prompt = _load_dm_prompt()
    flag_prompts = _load_flag_prompts(adventure_folder, phase=phase)
    context = session_manager.build_context(adventure_folder)
    top_rules = _load_top_rules()

    cached_text = dm_prompt + _SIGNAL_INSTRUCTIONS
    if flag_prompts:
        cached_text += "\n\n" + flag_prompts

    dynamic_parts = []
    if top_rules:
        dynamic_parts.append(top_rules)
    dynamic_parts.append(f"## Aktueller Spielstand\n\n{context}")

    return [
        {
            "type": "text",
            "text": cached_text,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": "\n\n".join(dynamic_parts),
        },
    ]


def _build_system_setup(adventure_folder: str, player_name: str, setting: dict) -> list:
    """System-Blöcke für den privaten CHARACTER_SETUP Kanal."""
    setup_prompt = _load_character_setup_prompt()
    flag_prompts = _load_flag_prompts(adventure_folder, phase="CHARACTER_SETUP")

    # Kontext: Abenteuer-Setting + Spieler-Info (kein voller session-Kontext nötig)
    welt = setting.get("welt", {})
    abenteuer_name = setting.get("name", adventure_folder.replace("_", " ").title())
    stimmung = setting.get("stimmung", "")
    trigger_warnings = setting.get("trigger_warnings", [])
    welt_beschreibung = welt.get("beschreibung", "")
    verfuegbare_spezies = setting.get("verfuegbare_spezies", [])

    context_lines = [
        f"## Abenteuer: {abenteuer_name}",
        f"Spieler: {player_name}",
    ]
    if stimmung:
        context_lines.append(f"Stimmung: {stimmung}")
    if welt_beschreibung:
        context_lines.append(f"Welt: {welt_beschreibung}")
    if trigger_warnings:
        context_lines.append("Trigger Warnings: " + ", ".join(trigger_warnings))
    if verfuegbare_spezies:
        context_lines.append("verfuegbare_spezies: " + ", ".join(verfuegbare_spezies))

    cached_text = setup_prompt + _SIGNAL_INSTRUCTIONS
    if flag_prompts:
        cached_text += "\n\n" + flag_prompts

    return [
        {
            "type": "text",
            "text": cached_text,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": "\n".join(context_lines),
        },
    ]


def _trim_history(adventure_folder: str) -> None:
    """Kürzt die History auf die letzten HISTORY_MESSAGES Einträge."""
    history = _history[adventure_folder]
    if len(history) > HISTORY_MESSAGES * 2:
        _history[adventure_folder] = history[-(HISTORY_MESSAGES * 2):]


def _rotate_log_if_needed(log_path: Path) -> None:
    """Trimmt das JSONL auf MAX_LOG_LINES wenn es zu groß wird."""
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines(keepends=True)
        if len(lines) > MAX_LOG_LINES:
            keep = lines[-MAX_LOG_LINES:]
            log_path.write_text("".join(keep), encoding="utf-8")
            logger.info(f"Log rotiert: {len(lines)} → {len(keep)} Zeilen ({log_path.name})")
    except Exception as e:
        logger.warning(f"Log-Rotation fehlgeschlagen: {e}")


def _log_message(adventure_folder: str, role: str, name: str, text: str) -> None:
    """Hängt eine Nachricht ans JSONL-Log an — crash-safe, mit Rotation."""
    log_path = TTRPG / "adventures" / adventure_folder / "spielprotokoll.jsonl"
    entry = {
        "ts": datetime.utcnow().isoformat(),
        "role": role,
        "name": name,
        "text": text,
    }
    try:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _rotate_log_if_needed(log_path)
    except Exception as e:
        logger.warning(f"Log-Schreiben fehlgeschlagen ({log_path}): {e}")


def _maybe_log_checkpoint(adventure_folder: str) -> None:
    """Schreibt alle SESSION_SAVE_INTERVAL Interaktionen einen Checkpoint-Marker ins Log."""
    _interaction_count[adventure_folder] += 1
    if _interaction_count[adventure_folder] % SESSION_SAVE_INTERVAL == 0:
        _log_message(adventure_folder, "system", "bot",
                     f"--- checkpoint #{_interaction_count[adventure_folder]} ---")


def _load_history_from_log(adventure_folder: str) -> None:
    """Lädt die letzten HISTORY_MESSAGES Nachrichten aus spielprotokoll.jsonl beim Start."""
    if _history[adventure_folder]:
        return  # bereits im Memory

    log_path = TTRPG / "adventures" / adventure_folder / "spielprotokoll.jsonl"
    if not log_path.exists():
        return

    try:
        entries = []
        with log_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))

        # Nur user/assistant Einträge, letzte HISTORY_MESSAGES * 2
        conversation = [e for e in entries if e["role"] in ("user", "assistant")]
        conversation = conversation[-(HISTORY_MESSAGES * 2):]

        for e in conversation:
            if e["role"] == "user":
                content = f"**{e['name']}:** {e['text']}"
            else:
                content = e["text"]
            _history[adventure_folder].append({"role": e["role"], "content": content})

        if _history[adventure_folder]:
            logger.info(f"[{adventure_folder}] History aus Log geladen ({len(_history[adventure_folder])} Einträge)")
    except Exception as ex:
        logger.warning(f"Log-Lesen fehlgeschlagen ({log_path}): {ex}")


def respond(adventure_folder: str, sender_name: str, message: str, phase: str = "DUNGEON_MASTER") -> str:
    """
    Verarbeitet eine Spieler-Nachricht und gibt die DM-Antwort zurück.
    phase: "DUNGEON_MASTER" für reguläres Spiel, "SESSION_ZERO" für Session 0.
    History wird im Memory gehalten.
    """
    # History aus Log laden falls noch nicht im Memory (z.B. nach Neustart)
    _load_history_from_log(adventure_folder)

    # Spieler-Nachricht loggen und zur History hinzufügen
    _log_message(adventure_folder, "user", sender_name, message)
    user_content = f"**{sender_name}:** {message}"
    _history[adventure_folder].append({"role": "user", "content": user_content})
    _trim_history(adventure_folder)

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_CONTEXT_TOKENS,
            system=_build_system(adventure_folder, phase=phase),
            messages=_history[adventure_folder],
        )

        dm_reply = response.content[0].text

        # DM-Antwort loggen und zur History hinzufügen
        _log_message(adventure_folder, "assistant", "DM", dm_reply)
        _history[adventure_folder].append({"role": "assistant", "content": dm_reply})

        cache_read  = getattr(response.usage, "cache_read_input_tokens", 0)
        cache_write = getattr(response.usage, "cache_creation_input_tokens", 0)
        logger.info(
            f"[{adventure_folder}] Tokens: input={response.usage.input_tokens} "
            f"output={response.usage.output_tokens} "
            f"cache_read={cache_read} cache_write={cache_write}"
        )
        usage_tracker.track_anthropic(
            response.usage.input_tokens, response.usage.output_tokens,
            cache_read, cache_write,
        )

        _maybe_log_checkpoint(adventure_folder)
        return dm_reply

    except anthropic.APIError as e:
        logger.error(f"Claude API Fehler: {e}")
        _history[adventure_folder].pop()
        return "*(Der DM räuspert sich — kurze Pause, gleich weiter.)*"


def respond_setup(adventure_folder: str, player_name: str, message: str) -> str:
    """
    Verarbeitet eine Nachricht im privaten CHARACTER_SETUP Kanal.
    History-Key: "setup_{adventure_folder}_{player_name}" — getrennt vom Gruppenkanal.
    """
    history_key = f"setup_{adventure_folder}_{player_name.lower()}"

    _log_message(adventure_folder, "user", player_name, f"[SETUP] {message}")
    user_content = f"**{player_name}:** {message}"
    _history[history_key].append({"role": "user", "content": user_content})
    _trim_history(history_key)

    setting = session_manager.load_setting(adventure_folder)

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_CONTEXT_TOKENS,
            system=_build_system_setup(adventure_folder, player_name, setting),
            messages=_history[history_key],
        )
        dm_reply = response.content[0].text

        _log_message(adventure_folder, "assistant", "DM", f"[SETUP] {dm_reply}")
        _history[history_key].append({"role": "assistant", "content": dm_reply})

        cache_read  = getattr(response.usage, "cache_read_input_tokens", 0)
        cache_write = getattr(response.usage, "cache_creation_input_tokens", 0)
        logger.info(
            f"[{adventure_folder}/setup/{player_name}] Tokens: input={response.usage.input_tokens} "
            f"output={response.usage.output_tokens} "
            f"cache_read={cache_read} cache_write={cache_write}"
        )
        usage_tracker.track_anthropic(
            response.usage.input_tokens, response.usage.output_tokens,
            cache_read, cache_write,
        )
        return dm_reply

    except anthropic.APIError as e:
        logger.error(f"Claude API Fehler (Setup): {e}")
        _history[history_key].pop()
        return "*(Kurze Pause — gleich weiter.)*"


def _flag_field_prompt_additions(adventure_folder: str) -> str:
    """
    Builds extraction prompt additions for flag-specific character fields.
    Returns an empty string if no flag fields are defined.
    """
    fields = session_manager.load_character_fields(adventure_folder)
    if not fields:
        return ""
    lines = ["\n\nZusätzlich extrahiere diese Flag-spezifischen Felder als Top-Level-Keys im JSON:"]
    for f in fields:
        flat = f["key"].split(".")[-1]
        req = " [PFLICHTFELD — muss vorhanden sein]" if f.get("required") else " [falls im Gespräch erwähnt]"
        detail = " Exakt übernehmen, nichts kürzen oder zusammenfassen." if f.get("detail") == "full" else ""
        lines.append(f'- "{flat}" → speichern unter {f["key"]}{req}{detail}')
    return "\n".join(lines)


def extract_character_from_setup_history(adventure_folder: str, player_name: str) -> dict:
    """
    Extrahiert strukturierte Charakterdaten aus der Setup-Konversations-History.
    Gibt ein char_data dict zurück (wie extract_characters_from_history).
    """
    history_key = f"setup_{adventure_folder}_{player_name.lower()}"
    history = _history.get(history_key, [])
    if not history:
        return {}

    conversation = "\n".join(
        f"DM: {msg['content']}" if msg["role"] == "assistant" else msg["content"]
        for msg in history
    )

    flag_additions = _flag_field_prompt_additions(adventure_folder)

    prompt = (
        f"Analysiere dieses Charaktererstellungs-Gespräch für Spieler '{player_name}' "
        f"und extrahiere die Charakterdaten.\n\n"
        f"Gespräch:\n{conversation}\n\n"
        "Gib die extrahierten Daten als JSON zurück — nur Felder die tatsächlich "
        "im Gespräch vorkommen, keine Erfindungen:\n"
        '{"name": "Charaktername", '
        '"wer_bist_du": "Kurze Charakterbeschreibung", '
        '"aussehen": "Aussehen", '
        '"alter": "Alter", '
        '"herkunft": "Herkunft", '
        '"skills": [{"name": "Skillname", "beschreibung": "Kurze Beschreibung"}], '
        '"will": "Ziel/Wunsch des Charakters (Liste wenn mehrere)", '
        '"fuerchtet": "Angst/Schwäche", '
        '"geheimnis": "Geheimnis (falls erwähnt)", '
        '"praeferenzen": {"no_gos": [], "wishes": []}, '
        '"imagen_prompt": "Detailed English portrait prompt: appearance, clothing, style, background, lighting, mood"'
        "}"
        f"{flag_additions}\n\n"
        "Nur JSON zurückgeben. Felder weglassen wenn keine Info vorhanden."
    )

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        start = raw.find("{")
        end = raw.rfind("}") + 1
        data = json.loads(raw[start:end])
        usage_tracker.track_anthropic(
            response.usage.input_tokens, response.usage.output_tokens,
            getattr(response.usage, "cache_read_input_tokens", 0),
            getattr(response.usage, "cache_creation_input_tokens", 0),
        )
        logger.info(f"[{adventure_folder}/setup/{player_name}] Charakter extrahiert: {data.get('name', '?')}")
        return data
    except Exception as e:
        logger.error(f"[{adventure_folder}/setup/{player_name}] Charakter-Extraktion fehlgeschlagen: {e}")
        return {}


def clear_setup_history(adventure_folder: str, player_name: str) -> None:
    """Löscht die Setup-History eines Spielers nach Abschluss."""
    history_key = f"setup_{adventure_folder}_{player_name.lower()}"
    _history[history_key] = []


def _load_session_text_from_log(adventure_folder: str) -> str:
    """Liest den JSONL-Log und gibt die Konversation als lesbaren Text zurück."""
    log_path = TTRPG / "adventures" / adventure_folder / "spielprotokoll.jsonl"
    if not log_path.exists():
        return ""
    lines = []
    try:
        with log_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                if entry["role"] == "user":
                    lines.append(f"SPIELER: {entry['content'] if 'content' in entry else entry.get('text', '')}")
                elif entry["role"] == "assistant":
                    lines.append(f"DM: {entry.get('text', '')}")
    except Exception as e:
        logger.warning(f"Log-Lesen fehlgeschlagen: {e}")
    return "\n\n".join(lines)


def compress_session(adventure_folder: str, detailed: bool = False) -> None:
    """
    Komprimiert session.yaml.
    detailed=True (bei !save): generiert zusätzlich 'wiederaufnahme' —
    eine atmosphärische Zusammenfassung für den nächsten Session-Start.
    """
    session = session_manager.load_session(adventure_folder)
    letzte = session.get("letzte_ereignisse", [])
    history = session.get("history", [])

    # Quelle bestimmen: YAML-Felder oder JSONL-Log
    if letzte:
        quelle = f"Letzte Ereignisse:\n{json.dumps(letzte, ensure_ascii=False)}"
        if history:
            quelle += f"\n\nÄltere History:\n{json.dumps(history, ensure_ascii=False)}"
    else:
        mem = _history.get(adventure_folder, [])
        if mem:
            quelle = "Konversations-History:\n" + "\n".join(
                f"{'DM' if m['role'] == 'assistant' else 'Spieler'}: {m['content']}"
                for m in mem
            )
        else:
            quelle = "Konversations-Log:\n" + _load_session_text_from_log(adventure_folder)

        if not quelle.strip() or len(quelle) < 50:
            logger.info(f"[{adventure_folder}] Nichts zu komprimieren")
            return

    wiederaufnahme_feld = (
        '  "wiederaufnahme": "3-5 atmosphärische Sätze die den DM beim nächsten Start '
        'direkt in die Szene holen — Stimmung, Spannung, offene Fäden, wo die Gruppe steht",\n'
        if detailed else ""
    )

    prompt = (
        "Du bist ein Archiv-Assistent für ein TTRPG-Abenteuer. "
        "Fasse den Spielverlauf kompakt zusammen — bewahre alle wichtigen Namen, "
        "Orte, Gegenstände und Plot-Punkte. Ältere Ereignisse kürzer, neuere etwas detaillierter.\n\n"
        "Antworte NUR mit einem JSON-Objekt:\n"
        "{\n"
        '  "aktueller_ort": "Wo sind die Charaktere gerade",\n'
        '  "letzte_szene": "1-2 Sätze was zuletzt passiert ist",\n'
        f"{wiederaufnahme_feld}"
        '  "letzte_ereignisse": ["Ereignis 1", "Ereignis 2", ...],\n'
        '  "history": ["Ältere Zusammenfassung 1", ...]\n'
        "}\n\n"
        f"{quelle}"
    )

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1200 if detailed else 800,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        start = raw.find("{")
        end = raw.rfind("}") + 1
        data = json.loads(raw[start:end])

        usage_tracker.track_anthropic(
            response.usage.input_tokens, response.usage.output_tokens,
            getattr(response.usage, "cache_read_input_tokens", 0),
            getattr(response.usage, "cache_creation_input_tokens", 0),
        )
        session["aktueller_ort"] = data.get("aktueller_ort", session.get("aktueller_ort", ""))
        session["letzte_szene"] = data.get("letzte_szene", "")
        session["letzte_ereignisse"] = data.get("letzte_ereignisse", [])
        session["history"] = data.get("history", history)
        if detailed and data.get("wiederaufnahme"):
            session["wiederaufnahme"] = data["wiederaufnahme"]
        session_manager.save_session(adventure_folder, session)
        logger.info(f"[{adventure_folder}] Session komprimiert → {session['letzte_szene'][:60]}...")

    except Exception as e:
        logger.error(f"Session-Komprimierung fehlgeschlagen: {e}")


def extract_characters_from_history(adventure_folder: str, player_names: list[str]) -> dict[str, dict]:
    """
    Analysiert die Konversations-History der Session 0 und extrahiert strukturierte
    Charakterdaten für jeden Spieler. Gibt {spielername: char_dict} zurück.
    """
    history = _history.get(adventure_folder, [])
    if not history:
        return {}

    conversation = "\n".join(
        f"DM: {msg['content']}" if msg["role"] == "assistant"
        else msg["content"]
        for msg in history
    )

    players_str = ", ".join(player_names)
    flag_additions = _flag_field_prompt_additions(adventure_folder)

    prompt = (
        f"Analysiere dieses Session-0-Gespräch und extrahiere die Charakterdaten "
        f"für die Spieler: {players_str}\n\n"
        f"Gespräch:\n{conversation}\n\n"
        "Gib die extrahierten Daten als JSON zurück — nur Felder die tatsächlich "
        "im Gespräch vorkommen, keine Erfindungen:\n"
        '{"charaktere": {"SpielerName": {'
        '"name": "Charaktername", '
        '"wer_bist_du": "Kurze Charakterbeschreibung", '
        '"aussehen": "Aussehen", '
        '"alter": "Alter", '
        '"herkunft": "Herkunft", '
        '"skills": [{"name": "Skillname", "beschreibung": "Kurze Beschreibung"}], '
        '"will": "Ziel/Wunsch des Charakters (Liste wenn mehrere)", '
        '"fuerchtet": "Angst/Schwäche", '
        '"geheimnis": "Geheimnis (falls erwähnt)", '
        '"begleiter": "Wichtige Beziehung (falls erwähnt)", '
        '"imagen_prompt": "Detailed English portrait prompt: appearance, clothing, style, background, lighting, mood"'
        "}}}"
        f"{flag_additions}\n\n"
        "Nur JSON zurückgeben. Felder weglassen wenn keine Info vorhanden."
    )

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        start = raw.find("{")
        end = raw.rfind("}") + 1
        data = json.loads(raw[start:end])
        usage_tracker.track_anthropic(
            response.usage.input_tokens, response.usage.output_tokens,
            getattr(response.usage, "cache_read_input_tokens", 0),
            getattr(response.usage, "cache_creation_input_tokens", 0),
        )
        result = data.get("charaktere", {})
        logger.info(f"[{adventure_folder}] Charaktere extrahiert: {list(result.keys())}")
        return result
    except Exception as e:
        logger.error(f"[{adventure_folder}] Charakter-Extraktion fehlgeschlagen: {e}")
        return {}


def clear_history(adventure_folder: str) -> None:
    """Löscht History und JSONL-Log nach einer Pause/Session-Save."""
    _history[adventure_folder] = []
    _interaction_count[adventure_folder] = 0

    log_path = TTRPG / "adventures" / adventure_folder / "spielprotokoll.jsonl"
    try:
        log_path.write_text("")
        logger.info(f"[{adventure_folder}] History und Log geleert")
    except Exception as e:
        logger.warning(f"Log konnte nicht geleert werden: {e}")
