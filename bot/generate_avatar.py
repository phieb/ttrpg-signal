"""
Avatar- und Szenen-Bild-Generierung via Google Gemini Imagen.
Avatare: Portrait-PNG pro Charakter nach Session 0.
Szenen-Bilder: atmosphärisches 16:9-Bild der aktuellen Spielszene (!showme).
"""

import logging
from pathlib import Path

import anthropic
import vertexai
from vertexai.preview.vision_models import ImageGenerationModel

import session_manager
import signal_client
import usage_tracker
from config import GCP_PROJECT, GCP_LOCATION, TTRPG_PATH, ANTHROPIC_API_KEY

logger = logging.getLogger(__name__)

TTRPG = Path(TTRPG_PATH)
IMAGEN_MODEL = "imagen-4.0-fast-generate-001"
_claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def _prompt_path(adventure_folder: str, char_name: str) -> Path:
    slug = char_name.lower().replace(" ", "_")
    return TTRPG / "adventures" / adventure_folder / "characters" / f"{slug}_avatar.txt"


def get_portrait_prompt(adventure_folder: str, char_name: str) -> str | None:
    """
    Sucht den Portrait-Prompt für einen Charakter:
    1. [charname]_avatar.txt
    2. [charname]_portrait_prompt.txt  (legacy)
    3. imagen_prompt in charakter YAML
    """
    base = TTRPG / "adventures" / adventure_folder / "characters"
    slug = char_name.lower().replace(" ", "_")

    txt_file = _prompt_path(adventure_folder, char_name)
    if txt_file.exists():
        return txt_file.read_text().strip()

    legacy = base / f"{slug}_portrait_prompt.txt"
    if legacy.exists():
        return legacy.read_text().strip()

    for yaml_file in base.glob("*.yaml"):
        char = session_manager._load_yaml(yaml_file)
        if char.get("charakter", {}).get("name", "").lower() == char_name.lower():
            prompt = char.get("imagen_prompt")
            if prompt:
                return prompt

    return None


def save_portrait_prompt(adventure_folder: str, char_name: str, prompt: str) -> None:
    _prompt_path(adventure_folder, char_name).write_text(prompt.strip())


def _run_imagen(prompt: str, output_path: Path, aspect_ratio: str = "1:1") -> bool:
    """Ruft Imagen auf, speichert das Bild, trackt die Nutzung. Gibt True bei Erfolg zurück."""
    try:
        vertexai.init(project=GCP_PROJECT, location=GCP_LOCATION)
        model = ImageGenerationModel.from_pretrained(IMAGEN_MODEL)
        images = model.generate_images(prompt=prompt, number_of_images=1, aspect_ratio=aspect_ratio)
        images[0].save(str(output_path))
        usage_tracker.track_imagen(1)
        return True
    except Exception as e:
        logger.error(f"Vertex AI Fehler ({output_path.name}): {e}")
        return False


def generate_avatar(adventure_folder: str, char_name: str) -> Path | None:
    """
    Generiert ein Avatar-PNG für einen Charakter via Gemini Imagen.
    Speichert es als [charname]_avatar.png im characters/ Ordner.
    Gibt den Pfad zurück, oder None bei Fehler.
    """
    prompt = get_portrait_prompt(adventure_folder, char_name)
    if not prompt:
        logger.warning(f"Kein Portrait-Prompt für {char_name} gefunden")
        return None

    output_path = (
        TTRPG / "adventures" / adventure_folder / "characters"
        / f"{char_name.lower().replace(' ', '_')}_avatar.png"
    )

    if _run_imagen(prompt, output_path, aspect_ratio="1:1"):
        logger.info(f"Avatar generiert: {output_path}")
        return output_path
    return None


def generate_and_send_avatars(adventure_folder: str, reply_to: str,
                               char_name: str | None = None,
                               subcommand: str | None = None,
                               new_prompt: str | None = None) -> None:
    """
    !avatar                        → show own avatar + current prompt
    !avatar regen                  → regenerate with existing prompt
    !avatar prompt                 → show current prompt
    !avatar prompt <text>          → update prompt and regenerate
    """
    if not char_name:
        signal_client.send(reply_to, "❌ Kein Charakter angegeben.")
        return

    # ── show prompt ──────────────────────────────────────────────────────────
    if subcommand == "prompt" and not new_prompt:
        prompt = get_portrait_prompt(adventure_folder, char_name)
        if prompt:
            signal_client.send(reply_to, f"🖼 Aktueller Avatar-Prompt für **{char_name}**:\n\n{prompt}")
        else:
            signal_client.send(reply_to, f"❌ Kein Avatar-Prompt für **{char_name}** gespeichert.")
        return

    # ── update prompt + regenerate ───────────────────────────────────────────
    if subcommand == "prompt" and new_prompt:
        save_portrait_prompt(adventure_folder, char_name, new_prompt)
        signal_client.send(reply_to, f"✅ Prompt gespeichert. Generiere Avatar für **{char_name}**...")
        avatar_path = generate_avatar(adventure_folder, char_name)
        if avatar_path:
            signal_client.send_file(reply_to, str(avatar_path), f"🧙 {char_name}")
        else:
            signal_client.send(reply_to, "⚠️ Avatar konnte nicht generiert werden.")
        return

    # ── regen ────────────────────────────────────────────────────────────────
    if subcommand == "regen":
        prompt = get_portrait_prompt(adventure_folder, char_name)
        if not prompt:
            signal_client.send(reply_to, f"❌ Kein Prompt für **{char_name}** — bitte zuerst *!avatar prompt <text>* setzen.")
            return
        signal_client.send(reply_to, f"🎨 Regeneriere Avatar für **{char_name}**...")
        avatar_path = generate_avatar(adventure_folder, char_name)
        if avatar_path:
            signal_client.send_file(reply_to, str(avatar_path), f"🧙 {char_name}")
        else:
            signal_client.send(reply_to, "⚠️ Avatar konnte nicht generiert werden.")
        return

    # ── default: show current avatar + prompt ────────────────────────────────
    slug = char_name.lower().replace(" ", "_")
    avatar_path = TTRPG / "adventures" / adventure_folder / "characters" / f"{slug}_avatar.png"
    if avatar_path.exists():
        signal_client.send_file(reply_to, str(avatar_path), f"🧙 {char_name}")
    else:
        signal_client.send(reply_to, f"_(Noch kein Avatar für **{char_name}** vorhanden.)_")

    prompt = get_portrait_prompt(adventure_folder, char_name)
    if prompt:
        signal_client.send(reply_to, f"🖼 Prompt:\n{prompt}\n\n*Tippe !avatar regen um neu zu generieren oder !avatar prompt <text> um den Prompt zu ändern.*")
    else:
        signal_client.send(reply_to, "*Kein Prompt gesetzt. Tippe !avatar prompt <text> um einen zu setzen.*")


# ── Szenen-Bild (!showme) ────────────────────────────────────────────────────

def _build_scene_imagen_prompt(adventure_folder: str, hint: str = "") -> str | None:
    """
    Fragt Claude nach einem englischen Imagen-Prompt für die aktuelle Szene.
    Bezieht Ort, Szenenbeschreibung, Setting-Atmosphäre und Charakteraussehen ein.
    Nur Charaktere mit vorhandenem Avatar werden erwähnt.
    hint: optionaler Wunsch des Spielers (z.B. "Koral in seiner Werkstatt mit uns beiden")
    """
    session = session_manager.load_session(adventure_folder)
    setting = session_manager.load_setting(adventure_folder)

    ort = session.get("aktueller_ort", "")
    szene = session.get("letzte_szene", "")
    ereignisse = session.get("letzte_ereignisse", [])

    welt = setting.get("welt", {})
    welt_name = welt.get("name", "")
    welt_beschreibung = welt.get("beschreibung", "")
    welt_stimmung = welt.get("stimmung", "")  # falls im Setting vorhanden

    # Charaktere mit vorhandenem Avatar
    chars_dir = TTRPG / "adventures" / adventure_folder / "characters"
    char_descriptions = []
    for char in session_manager.load_characters(adventure_folder):
        name = char.get("charakter", {}).get("name", "")
        if not name:
            continue
        slug = name.lower().replace(" ", "_")
        if not (chars_dir / f"{slug}_avatar.png").exists():
            continue
        # Aussehen aus identitaet.aussehen oder imagen_prompt ableiten
        aussehen = char.get("identitaet", {}).get("aussehen", "")
        char_descriptions.append(f"- {name}: {aussehen}" if aussehen else f"- {name}")

    # Zusammenbau des Kontext-Textes für Claude
    kontext_parts = []
    if welt_name:
        kontext_parts.append(f"World: {welt_name}")
    if welt_beschreibung:
        kontext_parts.append(f"Setting: {welt_beschreibung}")
    if welt_stimmung:
        kontext_parts.append(f"Atmosphere: {welt_stimmung}")
    if ort:
        kontext_parts.append(f"Current location: {ort}")
    if szene:
        kontext_parts.append(f"Current scene: {szene}")
    if ereignisse:
        kontext_parts.append("Recent events: " + " | ".join(ereignisse))
    if char_descriptions:
        kontext_parts.append("Characters present:\n" + "\n".join(char_descriptions))
    if hint:
        kontext_parts.append(f"Player's scene suggestion: {hint}")

    if not kontext_parts:
        return None

    hint_instruction = (
        "The player has given a scene suggestion — treat it as inspiration, "
        "incorporate it if it fits, adjust freely to serve the mood. "
    ) if hint else ""

    prompt = (
        "You are writing a prompt for an AI image generator (Imagen 4). "
        "Based on the TTRPG scene below, write a single detailed English image prompt. "
        "The image should be atmospheric, cinematic, wide-angle (16:9). "
        "Include the characters in the scene if their appearance is described. "
        f"{hint_instruction}"
        "Focus on mood, lighting, environment, and visual storytelling. "
        "Return ONLY the image prompt — no explanation, no quotes.\n\n"
        + "\n".join(kontext_parts)
    )

    try:
        response = _claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        usage_tracker.track_anthropic(
            response.usage.input_tokens, response.usage.output_tokens,
            getattr(response.usage, "cache_read_input_tokens", 0),
            getattr(response.usage, "cache_creation_input_tokens", 0),
        )
        return response.content[0].text.strip()
    except Exception as e:
        logger.error(f"Szenen-Prompt Generierung fehlgeschlagen: {e}")
        return None


def generate_scene_image(adventure_folder: str, reply_to: str, hint: str = "") -> None:
    """
    !showme [hint] — generiert ein atmosphärisches 16:9-Szenenbild und schickt es in die Gruppe.
    hint: optionaler Spieler-Wunsch, der als Inspiration in den Prompt einfließt.
    """
    signal_client.send(reply_to, "🎨 Generiere Szenen-Bild... einen Moment!")

    imagen_prompt = _build_scene_imagen_prompt(adventure_folder, hint=hint)
    if not imagen_prompt:
        signal_client.send(reply_to, "⚠️ Keine Szenen-Informationen verfügbar.")
        return

    logger.info(f"[{adventure_folder}] Szenen-Prompt: {imagen_prompt[:120]}...")

    output_path = TTRPG / "adventures" / adventure_folder / "_scene_current.png"
    if _run_imagen(imagen_prompt, output_path, aspect_ratio="16:9"):
        logger.info(f"[{adventure_folder}] Szenen-Bild gespeichert: {output_path}")
        session = session_manager.load_session(adventure_folder)
        caption = session.get("aktueller_ort") or "Aktuelle Szene"
        signal_client.send_file(reply_to, str(output_path), caption)
    else:
        signal_client.send(reply_to, "⚠️ Bild konnte nicht generiert werden.")
