"""
Avatar-Generierung via Google Gemini Imagen.
Wird nach Session 0 aufgerufen — generiert ein Portrait-PNG pro Charakter
und schickt es als Signal-Attachment in die Gruppe.
"""

import logging
from pathlib import Path

import vertexai
from vertexai.preview.vision_models import ImageGenerationModel

import session_manager
import signal_client
from config import GCP_PROJECT, GCP_LOCATION, TTRPG_PATH

logger = logging.getLogger(__name__)

TTRPG = Path(TTRPG_PATH)
IMAGEN_MODEL = "imagen-4.0-fast-generate-001"


def _get_portrait_prompt(adventure_folder: str, char_name: str) -> str | None:
    """
    Sucht den Portrait-Prompt für einen Charakter:
    1. [charname]_portrait_prompt.txt
    2. imagen_prompt in charakter YAML
    """
    base = TTRPG / "adventures" / adventure_folder / "characters"

    # Variante 1: dedizierte .txt Datei
    slug = char_name.lower().replace(" ", "_")
    txt_file = base / f"{slug}_portrait_prompt.txt"
    if txt_file.exists():
        return txt_file.read_text().strip()

    # Variante 2: imagen_prompt im YAML
    for yaml_file in base.glob("*.yaml"):
        char = session_manager._load_yaml(yaml_file)
        if char.get("charakter", {}).get("name", "").lower() == char_name.lower():
            prompt = char.get("imagen_prompt")
            if prompt:
                return prompt

    return None


def generate_avatar(adventure_folder: str, char_name: str) -> Path | None:
    """
    Generiert ein Avatar-PNG für einen Charakter via Gemini Imagen.
    Speichert es als [charname]_avatar.png im characters/ Ordner.
    Gibt den Pfad zurück, oder None bei Fehler.
    """
    prompt = _get_portrait_prompt(adventure_folder, char_name)
    if not prompt:
        logger.warning(f"Kein Portrait-Prompt für {char_name} gefunden")
        return None

    output_path = (
        TTRPG / "adventures" / adventure_folder / "characters"
        / f"{char_name.lower().replace(' ', '_')}_avatar.png"
    )

    try:
        vertexai.init(project=GCP_PROJECT, location=GCP_LOCATION)
        model = ImageGenerationModel.from_pretrained(IMAGEN_MODEL)
        images = model.generate_images(prompt=prompt, number_of_images=1, aspect_ratio="1:1")
        images[0].save(str(output_path))
        logger.info(f"Avatar generiert: {output_path}")
        return output_path

    except Exception as e:
        logger.error(f"Vertex AI Fehler für {char_name}: {e}")
        return None


def generate_and_send_avatars(adventure_folder: str, reply_to: str,
                               char_name_filter: str | None = None) -> None:
    """
    !avatare          → Liste der Charakternamen
    !avatare <name>   → Avatar für diesen Charakter generieren
    """
    characters = session_manager.load_characters(adventure_folder)
    if not characters:
        signal_client.send(reply_to, "❌ Keine Charaktere gefunden.")
        return

    # Kein Name → nur Liste anzeigen
    if not char_name_filter:
        lines = ["**Verfügbare Charaktere:**", ""]
        for c in characters:
            name = c.get("charakter", {}).get("name", "?")
            lines.append(f"• {name}")
        lines += ["", "Tippe *!avatare <name>* um ein Portrait zu generieren."]
        signal_client.send(reply_to, "\n".join(lines))
        return

    # Name angegeben → Avatar generieren
    match = next(
        (c for c in characters
         if c.get("charakter", {}).get("name", "").lower() == char_name_filter.lower()),
        None
    )
    if not match:
        names = ", ".join(c.get("charakter", {}).get("name", "?") for c in characters)
        signal_client.send(reply_to, f"❌ '{char_name_filter}' nicht gefunden.\nVerfügbar: {names}")
        return

    char_name = match["charakter"]["name"]
    signal_client.send(reply_to, f"🎨 Generiere Avatar für **{char_name}**... kurz warten!")
    avatar_path = generate_avatar(adventure_folder, char_name)
    if avatar_path:
        signal_client.send_file(reply_to, str(avatar_path), f"🧙 {char_name}")
    else:
        signal_client.send(reply_to, f"⚠️ Avatar für {char_name} konnte nicht generiert werden.")
