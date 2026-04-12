import json
import logging
from datetime import datetime
from pathlib import Path
from collections import defaultdict

import anthropic

import session_manager
from config import ANTHROPIC_API_KEY, TTRPG_PATH, MAX_CONTEXT_TOKENS, HISTORY_MESSAGES

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


def _load_dm_prompt() -> str:
    """Lädt den statischen DM System-Prompt aus der Engine."""
    path = TTRPG / "_engine" / "DUNGEON_MASTER.md"
    try:
        return path.read_text()
    except Exception as e:
        logger.error(f"DUNGEON_MASTER.md konnte nicht geladen werden: {e}")
        return "Du bist ein Dungeon Master. Führe die Spieler durch ein Abenteuer."


def _build_system(adventure_folder: str) -> list:
    """Baut die system-Blöcke auf — DM-Prompt gecacht, Kontext frisch."""
    dm_prompt = _load_dm_prompt()
    context = session_manager.build_context(adventure_folder)

    return [
        {
            "type": "text",
            "text": dm_prompt,
            "cache_control": {"type": "ephemeral"},  # Prompt Caching
        },
        {
            "type": "text",
            "text": (
                "\n\n---\n\n"
                "## WICHTIG — Technischer Kontext\n\n"
                "Du läufst als Signal-Bot. Du hast KEINEN direkten Dateizugriff. "
                "Alle relevanten Spieldaten wurden bereits aus den YAML-Dateien geladen "
                "und sind unten als 'Aktueller Spielstand' eingebettet. "
                "Erwähne niemals Dateien oder fehlenden Zugriff. "
                "Steige direkt als DM in die Szene ein.\n\n"
                f"## Aktueller Spielstand\n\n{context}"
            ),
        },
    ]


def _trim_history(adventure_folder: str) -> None:
    """Kürzt die History auf die letzten HISTORY_MESSAGES Einträge."""
    history = _history[adventure_folder]
    if len(history) > HISTORY_MESSAGES * 2:
        _history[adventure_folder] = history[-(HISTORY_MESSAGES * 2):]


def _log_message(adventure_folder: str, role: str, name: str, text: str) -> None:
    """Hängt eine Nachricht ans JSONL-Log an — crash-safe."""
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


def respond(adventure_folder: str, sender_name: str, message: str) -> str:
    """
    Verarbeitet eine Spieler-Nachricht und gibt die DM-Antwort zurück.
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
            system=_build_system(adventure_folder),
            messages=_history[adventure_folder],
        )

        dm_reply = response.content[0].text

        # DM-Antwort loggen und zur History hinzufügen
        _log_message(adventure_folder, "assistant", "DM", dm_reply)
        _history[adventure_folder].append({"role": "assistant", "content": dm_reply})

        logger.info(
            f"[{adventure_folder}] Tokens: input={response.usage.input_tokens} "
            f"output={response.usage.output_tokens} "
            f"cache_read={getattr(response.usage, 'cache_read_input_tokens', 0)}"
        )

        _maybe_log_checkpoint(adventure_folder)
        return dm_reply

    except anthropic.APIError as e:
        logger.error(f"Claude API Fehler: {e}")
        _history[adventure_folder].pop()
        return "*(Der DM räuspert sich — kurze Pause, gleich weiter.)*"


def compress_session(adventure_folder: str) -> None:
    """
    Komprimiert session.yaml — neuere Einträge detailliert, ältere komprimiert.
    Wird bei !pause aufgerufen.
    """
    session = session_manager.load_session(adventure_folder)

    letzte = session.get("letzte_ereignisse", [])
    history = session.get("history", [])

    if not letzte and not history:
        return

    prompt = (
        "Du bist ein Archiv-Assistent für ein TTRPG-Abenteuer. "
        "Komprimiere den Spielverlauf nach diesen Regeln:\n\n"
        "1. `letzte_ereignisse` (aktuelle Session) → fasse sie in 2-3 prägnante Sätze zusammen. "
        "Wichtige Namen, Orte und Plot-Punkte müssen erhalten bleiben.\n"
        "2. `history` (ältere Sessions) → fasse mehrere Einträge zu einem einzigen Satz zusammen "
        "wenn sie thematisch zusammenpassen. Je älter, desto kürzer.\n\n"
        "Antworte NUR mit einem JSON-Objekt mit den Feldern `letzte_ereignisse_komprimiert` (string) "
        "und `history_komprimiert` (Liste von strings).\n\n"
        f"letzte_ereignisse:\n{json.dumps(letzte, ensure_ascii=False)}\n\n"
        f"history:\n{json.dumps(history, ensure_ascii=False)}"
    )

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        # JSON aus Antwort extrahieren
        start = raw.find("{")
        end = raw.rfind("}") + 1
        data = json.loads(raw[start:end])

        # Komprimierte Einträge in session.yaml schreiben
        new_summary = data.get("letzte_ereignisse_komprimiert", "")
        new_history = data.get("history_komprimiert", history)

        if new_summary:
            new_history = [new_summary] + new_history

        session["history"] = new_history
        session["letzte_ereignisse"] = []
        session_manager.save_session(adventure_folder, session)
        logger.info(f"[{adventure_folder}] Session komprimiert: {len(letzte)} Ereignisse → 1 Eintrag")

    except Exception as e:
        logger.error(f"Session-Komprimierung fehlgeschlagen: {e}")


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
