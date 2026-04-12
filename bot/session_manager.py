import yaml
import logging
from pathlib import Path
from config import TTRPG_PATH

logger = logging.getLogger(__name__)

TTRPG = Path(TTRPG_PATH)


# ── Hilfsfunktionen ──────────────────────────────────────────────────────────

def _load_yaml(path: Path) -> dict:
    try:
        return yaml.safe_load(path.read_text()) or {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning(f"YAML konnte nicht geladen werden ({path}): {e}")
        return {}


def _save_yaml(path: Path, data: dict) -> None:
    try:
        path.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False))
    except Exception as e:
        logger.error(f"YAML konnte nicht gespeichert werden ({path}): {e}")


# ── Gruppen-Routing ───────────────────────────────────────────────────────────

def get_adventure_for_group(group_id: str) -> str | None:
    """Gibt den Abenteuer-Ordnernamen für eine Signal-Gruppen-ID zurück."""
    status = _load_yaml(TTRPG / "status.yaml")
    for abenteuer in status.get("abenteuer", []):
        if abenteuer.get("signal_gruppe") == group_id:
            return abenteuer["ordner"]
    return None


def _load_players() -> dict:
    """Gibt name→telefon dict aus players/*.yaml zurück."""
    players = {}
    for f in (TTRPG / "players").glob("*.yaml"):
        try:
            data = yaml.safe_load(f.read_text()) or {}
            s = data.get("spieler", {})
            players[s["name"]] = s["telefon"]
        except Exception:
            pass
    return players


def get_adventure_for_player(phone: str) -> str | None:
    """Gibt den Abenteuer-Ordner für eine Spieler-Nummer zurück (1:1 Nachrichten)."""
    players = _load_players()          # name → telefon
    name_for_phone = {v: k for k, v in players.items()}
    player_name = name_for_phone.get(phone)
    if not player_name:
        return None

    status = _load_yaml(TTRPG / "status.yaml")
    for abenteuer in status.get("abenteuer", []):
        for spieler in abenteuer.get("spieler", []):
            if spieler.get("name") == player_name:
                return abenteuer["ordner"]
    return None


# ── Session lesen/schreiben ───────────────────────────────────────────────────

def load_session(adventure_folder: str) -> dict:
    return _load_yaml(TTRPG / "adventures" / adventure_folder / "session.yaml")


def save_session(adventure_folder: str, data: dict) -> None:
    path = TTRPG / "adventures" / adventure_folder / "session.yaml"
    _save_yaml(path, data)
    logger.info(f"session.yaml gespeichert: {adventure_folder}")


# ── Charaktere ───────────────────────────────────────────────────────────────

def load_characters(adventure_folder: str) -> list[dict]:
    """Lädt alle Charaktere eines Abenteuers."""
    chars_dir = TTRPG / "adventures" / adventure_folder / "characters"
    characters = []
    for f in sorted(chars_dir.glob("*.yaml")):
        data = _load_yaml(f)
        if data:
            characters.append(data)
    return characters


def get_character_for_player(adventure_folder: str, player_name: str) -> dict | None:
    """Gibt den Charakter eines Spielers in einem bestimmten Abenteuer zurück."""
    for char in load_characters(adventure_folder):
        if char.get("charakter", {}).get("gespielt_von", "").lower() == player_name.lower():
            return char
    return None


def get_all_characters_for_player(player_name: str) -> list[dict]:
    """
    Sucht alle Charaktere eines Spielers über alle Abenteuer.
    Gibt Liste von dicts zurück: {"char": {...}, "abenteuer": "ordner"}
    """
    results = []
    adventures_dir = TTRPG / "adventures"
    for adventure_path in sorted(adventures_dir.iterdir()):
        if not adventure_path.is_dir() or adventure_path.name.startswith("_"):
            continue
        chars_dir = adventure_path / "characters"
        if not chars_dir.exists():
            continue
        for f in sorted(chars_dir.glob("*.yaml")):
            char = _load_yaml(f)
            if char.get("charakter", {}).get("gespielt_von", "").lower() == player_name.lower():
                results.append({"char": char, "abenteuer": adventure_path.name})
    return results


def find_character_by_name(player_name: str, char_name: str) -> dict | None:
    """Sucht einen Charakter über alle Abenteuer anhand des Charakternamens."""
    for entry in get_all_characters_for_player(player_name):
        if entry["char"].get("charakter", {}).get("name", "").lower() == char_name.lower():
            return entry["char"]
    return None


def find_character_entry_by_name(player_name: str, char_name: str) -> dict | None:
    """Sucht einen Charakter über alle Abenteuer, gibt {"char": dict, "abenteuer": "folder"} zurück."""
    for entry in get_all_characters_for_player(player_name):
        if entry["char"].get("charakter", {}).get("name", "").lower() == char_name.lower():
            return entry
    return None


# ── Setting & NPCs ────────────────────────────────────────────────────────────

def load_setting(adventure_folder: str) -> dict:
    return _load_yaml(TTRPG / "adventures" / adventure_folder / "setting.yaml")


def load_npcs(adventure_folder: str) -> dict:
    return _load_yaml(TTRPG / "adventures" / adventure_folder / "npcs.yaml")


# ── Kontext-Builder für Claude API ────────────────────────────────────────────

def build_context(adventure_folder: str) -> str:
    """Baut einen kompakten Kontext-String für die Claude API zusammen."""
    lines = []

    # Setting
    setting = load_setting(adventure_folder)
    welt = setting.get("welt", {})
    if welt:
        lines.append(f"## Welt: {welt.get('name', '')}")
        lines.append(welt.get("beschreibung", ""))
        if welt.get("magie"):
            lines.append(f"Magie: {welt['magie']}")
        if welt.get("gefahr"):
            lines.append(f"Gefahr: {welt['gefahr']}")

    # Charaktere
    characters = load_characters(adventure_folder)
    if characters:
        lines.append("\n## Charaktere")
        for char in characters:
            c = char.get("charakter", {})
            ident = char.get("identitaet", {})
            mot = char.get("motivation", {})
            skills = char.get("skills", [])
            lines.append(f"\n**{c.get('name', '?')}** (gespielt von {c.get('gespielt_von', '?')})")
            lines.append(ident.get("wer_bist_du", ""))
            if skills:
                skill_list = ", ".join(s["name"] for s in skills)
                lines.append(f"Skills: {skill_list}")
            if mot.get("will"):
                lines.append(f"Will: {mot['will']}")
            if mot.get("fuerchtet"):
                lines.append(f"Fürchtet: {mot['fuerchtet']}")

    # Session-Zustand
    session = load_session(adventure_folder)
    if session:
        lines.append(f"\n## Aktuelle Szene")
        ort = session.get("aktueller_ort") or session.get("aktuelle_szene", {}).get("ort", "")
        if ort:
            lines.append(f"Ort: {ort}")

        szene_text = session.get("letzte_szene") or session.get("aktuelle_szene", {}).get("zusammenfassung", "")
        if szene_text:
            lines.append(szene_text)

        # Offene Fäden (altes Format)
        faeden = session.get("aktuelle_szene", {}).get("offene_faeden", [])
        if faeden:
            lines.append("Offene Fäden: " + " | ".join(faeden))

        # Letzte Ereignisse
        ereignisse = session.get("letzte_ereignisse", [])
        if ereignisse:
            lines.append("Letzte Ereignisse: " + " | ".join(ereignisse))

        # Aktive Quests
        quests = [q for q in session.get("aktive_quests", []) if q.get("status") != "abgeschlossen"]
        if quests:
            lines.append("\nAktive Quests: " + " | ".join(q["name"] for q in quests))

        # Ältere History
        history = session.get("history", [])
        if history:
            lines.append("\nHistory: " + " ".join(history))

        # Fallback story_so_far (altes Format)
        if session.get("story_so_far"):
            lines.append(f"\n## Story so far\n{session['story_so_far']}")

    # NPCs
    npcs_data = load_npcs(adventure_folder)
    npcs = npcs_data.get("npcs", [])
    aktive_npcs = [n for n in npcs if n.get("status", "aktiv") == "aktiv" and n.get("name")]
    if aktive_npcs:
        lines.append("\n## Aktive NSCs")
        for npc in aktive_npcs:
            lines.append(f"**{npc['name']}** ({npc.get('rolle', '')}): {npc.get('persoenlichkeit', '')}")

    return "\n".join(lines)
