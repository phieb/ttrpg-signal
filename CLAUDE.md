# ttrpg-signal — Bot Codebase

Signal-Bot der als Dungeon Master antwortet. Spieler schreiben in eine Signal-Gruppe oder einen 1:1-Kanal, der Bot antwortet als DM.

## Repos

- `ttrpg-signal/` — dieser Repo: Bot-Code, Docker, Config
- `ttrpg/` — separates Repo: Engine-Prompts, Templates, Abenteuer-Daten (eingebunden via `TTRPG_PATH`)

Beide Repos sind eng gekoppelt. Änderungen an den Engine-Prompts (`ttrpg/_engine/`) wirken sich direkt auf das DM-Verhalten aus.

## Bot-Dateien (`bot/`)

| Datei | Zweck |
|-------|-------|
| `main.py` | Event Loop, Message-Routing, Batching, Kommando-Router |
| `dm_engine.py` | DM-Logik, Konversations-History, Komprimierung, Charakter-Extraktion |
| `llm_client.py` | Provider-Adapter: OpenAI / Anthropic / Gemini — ein Interface für alle |
| `session_manager.py` | YAML lesen/schreiben, Kontext-Builder für den System-Prompt |
| `generate_avatar.py` | Vertex AI Imagen — Avatar + Szenenbilder, Prompt-Verwaltung |
| `signal_client.py` | signal-cli REST API Wrapper |
| `usage_tracker.py` | Token- und Kosten-Tracking für alle Provider |
| `config.py` | Alle Umgebungsvariablen mit Defaults |

## Flavour System

Adventures can activate optional DM behaviour extensions called **flavours** (set via `!new --flavour`).
Each flavour is a folder under `ttrpg/_engine/flavours/[name]/` containing optional phase-specific
`.md` files and a `manifest.yaml` (for `requires:` dependencies and a description).

**Addons** are external flavour repos mounted into `_engine/flavours/` via Docker volumes — the bot
picks them up automatically with no registration or code change required.

## AI Provider

Der DM-Provider ist per `DM_PROVIDER` in `.env` konfigurierbar:

- `openai` — GPT-4o (Standard, empfohlen für deutsches Storytelling)
- `anthropic` — Claude Sonnet
- `gemini` — Gemini Flash

Charakter-Extraktion und Session-Komprimierung laufen immer auf **Claude Haiku** (strukturierte JSON-Tasks, unabhängig vom DM-Provider). Dafür ist `ANTHROPIC_API_KEY` immer erforderlich.

## Message Flow

```
Signal-Nachricht
  → process_message()
      → Setup-Kanal? → _handle_setup_message() → dm_engine.respond_setup()
      → Kommando (!...)? → handle_command() → cmd_*()
      → Spielnachricht → Batch-Puffer → _flush_batches() → dm_engine.respond()
```

Privater Setup-Kanal: Kommandos (`!avatar`, `!charakter`, `!status` etc.) funktionieren dort ebenfalls — `adventure_folder` wird aus dem Setup-Kontext aufgelöst.

## Avatar-Prompts

Prompt-Lookup-Reihenfolge für Charakter-Portraits:
1. `characters/[slug]_avatar.txt` — dedizierte Prompt-Datei (schreibt `!avatar prompt <text>`)
2. `characters/[slug]_portrait_prompt.txt` — Legacy-Format
3. `imagen_prompt`-Feld im Charakter-YAML — wird von der Charakter-Extraktion befüllt

## Nachrichten-Batching

Eingehende Spielnachrichten werden pro Abenteuer gesammelt. Der DM antwortet erst nach `BATCH_WINDOW_SECONDS` Sekunden Stille — oder sofort wenn bei einem Solo-Abenteuer die einzige Spielerin geschrieben hat bzw. alle Spieler geantwortet haben.

## Session-Komprimierung

- Automatisch nach 15 Minuten Inaktivität (stille Background-Komprimierung)
- Bei `!save`: detaillierte Komprimierung inkl. `wiederaufnahme`-Text für den nächsten Session-Start

## Wichtig beim Entwickeln

- `_history` in `dm_engine.py` ist ein In-Memory-Dict — geht bei Neustart verloren, wird aber aus `spielprotokoll.jsonl` wiederhergestellt
- Alle Pfade gehen über `TTRPG` = `Path(TTRPG_PATH)` — nie hardcoden
- Docker rebuild nötig nach jeder `.py`-Änderung: `docker compose build ttrpg-bot && docker compose up -d ttrpg-bot`

## TODOS

### Open
- reconfigure local deployment so all game data is stored separately — advise others to do the same for easy backup
- enable multi-language — find a fitting package, extract all prompts into language-specific files; language spec comes from ttrpg, signal is just the bot
- think about additional flavours for the core and for public addon projects (e.g. DnD classes/creatures/spells, steampunk, post-apocalyptic, zombies, academy trope, mafia trope, good-night stories, Edgar Allan Poe, Cthulhu...) — ensure content has a compatible license; add a `!flavours` command that lists all active/available flavours with descriptions from manifest.yaml

### Done
- ✅ separate 18+ content into ttrpg-adult addon — mature + booktok moved out of ttrpg core; docker-compose.yml documents how to mount addons; ttrpg repo is now clean for public release (delete GitHub repo + push fresh to avoid history with mature content)
- ✅ addon interface defined — flavours/ drop-in pattern, manifest.yaml, README in ttrpg-adult explains how to build addons
- ✅ flags renamed to flavours throughout both repos
- ✅ validate ttrpg_signal DM mechanism — fixed "bereit" heuristic ([SETUP_COMPLETE] marker), moved flag dependency config to manifest.yaml, moved utility prompts to ttrpg/_engine/templates/
- ✅ docker down graceful save — already implemented via SIGTERM handler + _shutdown_save()