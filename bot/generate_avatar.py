"""
Avatar-Generierung via Google Gemini Imagen.
Wird nach Session 0 aufgerufen — generiert ein Portrait-PNG pro Charakter
und schickt es als Signal-Attachment in die Gruppe.
"""

import logging
from pathlib import Path

from google import genai
from google.genai import types

import session_manager
import signal_client
from config import GOOGLE_API_KEY, TTRPG_PATH

logger = logging.getLogger(__name__)

TTRPG = Path(TTRPG_PATH)
IMAGEN_MODEL = "imagen-3.0-generate-002"


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
        client = genai.Client(api_key=GOOGLE_API_KEY)
        response = client.models.generate_images(
            model=IMAGEN_MODEL,
            prompt=prompt,
            config=types.GenerateImagesConfig(
                number_of_images=1,
                aspect_ratio="1:1",
                safety_filter_level="block_low_and_above",
                person_generation="allow_adult",
            ),
        )

        image_bytes = response.generated_images[0].image.image_bytes
        output_path.write_bytes(image_bytes)
        logger.info(f"Avatar generiert: {output_path}")
        return output_path

    except Exception as e:
        logger.error(f"Imagen-Fehler für {char_name}: {e}")
        return None


def generate_and_send_avatars(adventure_folder: str, reply_to: str) -> None:
    """
    Generiert Avatare für alle Charaktere eines Abenteuers
    und schickt sie via Signal.
    """
    characters = session_manager.load_characters(adventure_folder)
    if not characters:
        signal_client.send(reply_to, "❌ Keine Charaktere gefunden.")
        return

    signal_client.send(reply_to, f"🎨 Generiere {len(characters)} Avatar(e)... kurz warten!")

    for char in characters:
        char_name = char.get("charakter", {}).get("name")
        if not char_name:
            continue

        avatar_path = generate_avatar(adventure_folder, char_name)
        if avatar_path:
            signal_client.send_file(reply_to, str(avatar_path), f"🧙 {char_name}")
        else:
            signal_client.send(reply_to, f"⚠️ Avatar für {char_name} konnte nicht generiert werden.")
