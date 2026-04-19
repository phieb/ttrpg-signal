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


def _get_nested(data: dict, path: str):
    """Navigate a nested dict by dot-path. Returns None if any key is missing."""
    val = data
    for key in path.split("."):
        if not isinstance(val, dict):
            return None
        val = val.get(key)
    return val


def _set_nested(data: dict, path: str, value) -> None:
    """Set a value in a nested dict by dot-path, creating intermediate dicts as needed."""
    keys = path.split(".")
    for key in keys[:-1]:
        data = data.setdefault(key, {})
    data[keys[-1]] = value


def _get_from_extraction(char_data: dict, path: str):
    """
    Look up a flag field value from an extraction result.
    Tries the full dot-path first, then falls back to the flat key (last component).
    """
    val = _get_nested(char_data, path)
    if val is not None:
        return val
    return char_data.get(path.split(".")[-1])


# ── Gruppen-Routing ───────────────────────────────────────────────────────────

def get_adventure_for_group(group_id: str) -> str | None:
    """Gibt den Abenteuer-Ordnernamen für eine Signal-Gruppen-ID zurück (Spielgruppe)."""
    status = _load_yaml(TTRPG / "status.yaml")
    for abenteuer in status.get("abenteuer", []):
        if abenteuer.get("signal_gruppe") == group_id:
            return abenteuer["ordner"]
    return None


def get_setup_context_for_group(group_id: str) -> tuple[str, str] | None:
    """
    Prüft ob eine Gruppen-ID ein privater Setup-Kanal ist.
    Gibt (adventure_folder, player_name) zurück oder None.
    """
    status = _load_yaml(TTRPG / "status.yaml")
    for abenteuer in status.get("abenteuer", []):
        for spieler in abenteuer.get("spieler", []):
            if spieler.get("private_gruppe") == group_id:
                return abenteuer["ordner"], spieler["name"]
    return None


def set_player_setup_status(adventure_folder: str, player_name: str, setup_status: str) -> None:
    """Setzt den setup_status eines Spielers (invited / ready)."""
    status_path = TTRPG / "status.yaml"
    data = _load_yaml(status_path)
    for abenteuer in data.get("abenteuer", []):
        if abenteuer.get("ordner") == adventure_folder:
            for spieler in abenteuer.get("spieler", []):
                if spieler.get("name", "").lower() == player_name.lower():
                    spieler["setup_status"] = setup_status
                    _save_yaml(status_path, data)
                    logger.info(f"[{adventure_folder}] {player_name} setup_status → {setup_status}")
                    return


def set_player_private_gruppe(adventure_folder: str, player_name: str, group_id: str) -> None:
    """Speichert die private Gruppen-ID eines Spielers für einen Abenteuer-Eintrag."""
    status_path = TTRPG / "status.yaml"
    data = _load_yaml(status_path)
    for abenteuer in data.get("abenteuer", []):
        if abenteuer.get("ordner") == adventure_folder:
            for spieler in abenteuer.get("spieler", []):
                if spieler.get("name", "").lower() == player_name.lower():
                    spieler["private_gruppe"] = group_id
                    spieler["setup_status"] = "invited"
                    _save_yaml(status_path, data)
                    logger.info(f"[{adventure_folder}] {player_name} private_gruppe → {group_id}")
                    return


def all_players_ready(adventure_folder: str) -> bool:
    """True wenn alle Spieler eines Abenteuers setup_status == 'ready' haben."""
    status = _load_yaml(TTRPG / "status.yaml")
    for abenteuer in status.get("abenteuer", []):
        if abenteuer.get("ordner") == adventure_folder:
            spieler_liste = abenteuer.get("spieler", [])
            if not spieler_liste:
                return False
            return all(s.get("setup_status") == "ready" for s in spieler_liste)
    return False


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


def get_adventure_player_names_proper(adventure_folder: str) -> list[str]:
    """
    Gibt die Spielernamen eines Abenteuers mit korrekter Groß-/Kleinschreibung zurück
    (cross-referenced mit players/*.yaml).
    """
    status = _load_yaml(TTRPG / "status.yaml")
    raw_names = []
    for a in status.get("abenteuer", []):
        if a.get("ordner") == adventure_folder:
            raw_names = [s["name"] for s in a.get("spieler", [])]
            break

    proper: dict[str, str] = {}
    for f in (TTRPG / "players").glob("*.yaml"):
        try:
            data = yaml.safe_load(f.read_text()) or {}
            n = data.get("spieler", {}).get("name", "")
            if n:
                proper[n.lower()] = n
        except Exception:
            pass

    return [proper.get(n.lower(), n.title()) for n in raw_names]


def save_character(adventure_folder: str, player_name: str, char_data: dict) -> Path | None:
    """
    Schreibt ein extrahiertes Charakterblatt als YAML.
    Überschreibt bestehende Dateien (Session-0-Daten wachsen mit der Konversation).
    """
    chars_dir = TTRPG / "adventures" / adventure_folder / "characters"
    chars_dir.mkdir(exist_ok=True)

    char_name = char_data.get("name") or player_name
    slug = char_name.lower().replace(" ", "_")
    yaml_path = chars_dir / f"{slug}.yaml"

    # Remove any stale YAML for this player (name may have changed during setup)
    for existing in chars_dir.glob("*.yaml"):
        if existing == yaml_path:
            continue
        try:
            existing_data = yaml.safe_load(existing.read_text()) or {}
            if existing_data.get("charakter", {}).get("gespielt_von", "").lower() == player_name.lower():
                existing.unlink()
                logger.info(f"Veraltete Charakterdatei entfernt: {existing.name}")
        except Exception:
            pass

    praeferenzen = char_data.get("praeferenzen", {})
    if not praeferenzen:
        # Flat fields from extraction fallback
        no_gos = char_data.get("no_gos", [])
        wishes = char_data.get("wishes", [])
        if no_gos or wishes:
            praeferenzen = {"no_gos": no_gos, "wishes": wishes}

    char_yaml = {
        "charakter": {"name": char_name, "gespielt_von": player_name},
        "identitaet": {k: v for k, v in {
            "wer_bist_du": char_data.get("wer_bist_du", ""),
            "aussehen":    char_data.get("aussehen", ""),
            "alter":       char_data.get("alter", ""),
            "herkunft":    char_data.get("herkunft", ""),
        }.items() if v},
        "skills": char_data.get("skills", []),
        "motivation": {k: v for k, v in {
            "will":       char_data.get("will", ""),
            "fuerchtet":  char_data.get("fuerchtet", ""),
            "geheimnis":  char_data.get("geheimnis", ""),
        }.items() if v},
        "beziehungen": {k: v for k, v in {
            "begleiter": char_data.get("begleiter", ""),
        }.items() if v},
        "praeferenzen": praeferenzen or {"no_gos": [], "wishes": []},
        "imagen_prompt": char_data.get("imagen_prompt", ""),
    }

    # Apply flag-specific fields from extraction result
    for field_def in load_character_fields(adventure_folder):
        value = _get_from_extraction(char_data, field_def["key"])
        if value is not None:
            _set_nested(char_yaml, field_def["key"], value)

    yaml_path.write_text(yaml.dump(char_yaml, allow_unicode=True, default_flow_style=False))
    logger.info(f"Charakter gespeichert: {yaml_path.name}")
    return yaml_path


def find_character_entry_by_name(player_name: str, char_name: str) -> dict | None:
    """Sucht einen Charakter über alle Abenteuer, gibt {"char": dict, "abenteuer": "folder"} zurück."""
    for entry in get_all_characters_for_player(player_name):
        if entry["char"].get("charakter", {}).get("name", "").lower() == char_name.lower():
            return entry
    return None


# ── Charakter-Vollständigkeit ─────────────────────────────────────────────────

# Pflichtfelder für ein fertiges Charakterblatt (Pfad, Anzeigename)
REQUIRED_CHAR_FIELDS = [
    ("charakter.name",          "Name"),
    ("identitaet.wer_bist_du",  "Hintergrund / Wer bist du"),
    ("identitaet.aussehen",     "Aussehen"),
    ("skills",                  "Skills (mindestens einer)"),
    ("motivation.will",         "Was will dein Charakter"),
    ("motivation.fuerchtet",    "Was fürchtet dein Charakter"),
]


def check_character_completeness(adventure_folder: str, player_names: list[str]) -> dict[str, list[str]]:
    """
    Prüft für jeden Spieler ob sein Charakterblatt vollständig ist.
    Gibt {spielername: [fehlende Felder]} zurück — nur Spieler mit Lücken.
    Spieler ohne YAML bekommen "Charakterblatt fehlt komplett".
    Pflichtfelder = Basis-Felder + required-Felder aus aktiven Flag-CHARACTER_FIELDS.yaml.
    """
    flag_fields = load_character_fields(adventure_folder)
    required_flag_fields = [
        (f["key"], f["key"].split(".")[-1].replace("_", " ").title())
        for f in flag_fields if f.get("required")
    ]
    all_required = list(REQUIRED_CHAR_FIELDS) + required_flag_fields

    chars_by_player = {}
    for char in load_characters(adventure_folder):
        owner = char.get("charakter", {}).get("gespielt_von", "").lower()
        if owner:
            chars_by_player[owner] = char

    missing: dict[str, list[str]] = {}
    for name in player_names:
        char = chars_by_player.get(name.lower())
        if not char:
            missing[name] = ["Charakterblatt fehlt komplett"]
            continue

        gaps = []
        for field_path, label in all_required:
            val = _get_nested(char, field_path)
            if not val:
                gaps.append(label)
        if gaps:
            missing[name] = gaps

    return missing


# ── Setting & NPCs ────────────────────────────────────────────────────────────

def load_setting(adventure_folder: str) -> dict:
    return _load_yaml(TTRPG / "adventures" / adventure_folder / "setting.yaml")


def load_flags(adventure_folder: str) -> dict:
    """Gibt das flags-Dict aus setting.yaml zurück (leer falls keine gesetzt)."""
    return load_setting(adventure_folder).get("flags", {})


def load_character_fields(adventure_folder: str) -> list[dict]:
    """
    Loads flag-specific character field definitions for an adventure.
    Each active flag may have a CHARACTER_FIELDS.yaml in its flag folder.
    Returns a merged list of field dicts: [{key, required, detail}, ...]
    """
    flags = load_flags(adventure_folder)
    flags_dir = TTRPG / "_engine" / "flags"
    fields = []
    for flag, enabled in flags.items():
        if not enabled:
            continue
        fields_path = flags_dir / flag / "CHARACTER_FIELDS.yaml"
        if not fields_path.exists():
            continue
        try:
            data = yaml.safe_load(fields_path.read_text()) or {}
            fields.extend(data.get("fields", []))
        except Exception as e:
            logger.warning(f"CHARACTER_FIELDS.yaml für Flag '{flag}' konnte nicht geladen werden: {e}")
    # Deduplicate by key (first occurrence wins)
    seen = set()
    deduped = []
    for f in fields:
        if f["key"] not in seen:
            seen.add(f["key"])
            deduped.append(f)
    return deduped


def load_npcs(adventure_folder: str) -> dict:
    return _load_yaml(TTRPG / "adventures" / adventure_folder / "npcs.yaml")


# ── Kontext-Builder für Claude API ────────────────────────────────────────────

def build_context(adventure_folder: str) -> str:
    """Baut einen kompakten Kontext-String für die Claude API zusammen."""
    lines = []

    # Spieler-Liste — immer zuerst, damit der DM nie Spieler mit NSCs verwechselt
    characters = load_characters(adventure_folder)
    if characters:
        spieler_lines = []
        for char in characters:
            c = char.get("charakter", {})
            spieler = c.get("gespielt_von", "")
            char_name = c.get("name", "")
            if spieler and char_name:
                spieler_lines.append(f"- {spieler} → spielt {char_name}")
            elif spieler:
                spieler_lines.append(f"- {spieler}")
        if spieler_lines:
            lines.append("## Spieler (echte Menschen — niemals für sie handeln oder sprechen)")
            lines.extend(spieler_lines)
            lines.append("")

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

    # Charaktere (details)
    if characters:
        lines.append("\n## Charaktere")
        flag_fields = load_character_fields(adventure_folder)
        # Paths already shown in the base block — skip in the flag-fields loop
        base_shown = {
            "identitaet.wer_bist_du", "identitaet.aussehen", "identitaet.alter",
            "skills", "motivation.will", "motivation.fuerchtet", "motivation.geheimnis",
            "beziehungen",
            "praeferenzen", "praeferenzen.no_gos", "praeferenzen.wishes",
            "praeferenzen.mature_content_grenzen",
        }
        for char in characters:
            c = char.get("charakter", {})
            ident = char.get("identitaet", {})
            mot = char.get("motivation", {})
            skills = char.get("skills", [])
            beziehungen = char.get("beziehungen", {})
            praef = char.get("praeferenzen", {})

            lines.append(f"\n**{c.get('name', '?')}** (gespielt von {c.get('gespielt_von', '?')})")
            if ident.get("wer_bist_du"):
                lines.append(ident["wer_bist_du"])
            if ident.get("aussehen"):
                lines.append(f"Aussehen: {ident['aussehen']}")
            if ident.get("alter"):
                lines.append(f"Alter: {ident['alter']}")
            if skills:
                for s in skills:
                    lines.append(f"  [{s['name']}] {s.get('beschreibung', '')}")
            if mot.get("will"):
                will = mot["will"]
                lines.append("Will: " + (" | ".join(will) if isinstance(will, list) else will))
            if mot.get("fuerchtet"):
                lines.append(f"Fürchtet: {mot['fuerchtet']}")
            if mot.get("geheimnis"):
                lines.append(f"[DM only] Geheimnis: {mot['geheimnis']}")
            if beziehungen:
                for k, v in beziehungen.items():
                    if v:
                        lines.append(f"Beziehung {k}: {v}")

            # Präferenzen — safety-critical, always show prominently
            no_gos = praef.get("no_gos", [])
            grenzen = praef.get("mature_content_grenzen", [])
            wishes = praef.get("wishes", [])
            if no_gos:
                lines.append("⛔ NO-GOs: " + (", ".join(no_gos) if isinstance(no_gos, list) else no_gos))
            if grenzen:
                lines.append("⛔ Mature-Grenzen: " + (", ".join(grenzen) if isinstance(grenzen, list) else grenzen))
            if wishes:
                lines.append("✨ Wishes: " + (", ".join(wishes) if isinstance(wishes, list) else wishes))

            # Flag-specific fields — skip anything already shown in the base block
            for field_def in flag_fields:
                key = field_def["key"]
                if key in base_shown or any(key.startswith(p + ".") for p in base_shown):
                    continue
                val = _get_nested(char, key)
                if not val:
                    continue
                label = key.split(".")[-1].replace("_", " ").title()
                if isinstance(val, list):
                    lines.append(f"{label}: " + " | ".join(str(v) for v in val))
                elif isinstance(val, dict):
                    lines.append(f"{label}:")
                    for k, v in val.items():
                        if v:
                            lines.append(f"  {k}: {v}")
                else:
                    lines.append(f"{label}: {val}")

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

        wiederaufnahme = session.get("wiederaufnahme", "")
        if wiederaufnahme:
            lines.append(f"\n*Wiederaufnahme-Notiz:* {wiederaufnahme}")

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
