# ttrpg-signal

Signal-Bot der als Dungeon Master via Claude API antwortet. Spieler schreiben in eine Signal-Gruppe (oder 1:1), der Bot antwortet als DM.

> **Spielwelt-Daten:** Engine, Templates und Abenteuer-Struktur kommen aus [phieb/ttrpg](https://github.com/phieb/ttrpg) —
> wird beim ersten `docker compose up` automatisch geklont.

## Stack

- **signal-cli** (`bbernhard/signal-cli-rest-api`) — Signal Protokoll
- **Python 3.11** — Bot-Service
- **DM AI** — konfigurierbar: OpenAI GPT-4o, Claude Sonnet, oder Gemini (siehe `DM_PROVIDER`)
- **Claude Haiku** — Hilfsaufgaben (Charakter-Extraktion, Session-Komprimierung)
- **Vertex AI Imagen 4** — Charakter-Portrait-Generierung
- **Docker** — containerisiert

---

## Setup

### 1. Repo klonen

```bash
git clone https://github.com/phieb/ttrpg-signal.git
cd ttrpg-signal
```

Das [ttrpg](https://github.com/phieb/ttrpg) Engine-Repo wird beim ersten `docker compose up` automatisch geklont — kein manueller Checkout nötig.

### 2. `docker-compose.yml` anpassen

Signal-CLI-Daten werden als Bind Mount eingebunden — Pfad anpassen:

```yaml
signal-cli:
  volumes:
    - /pfad/zu/signal-cli-data:/home/.local/share/signal-cli
```

Wer signal-cli schon laufen hat: einfach den bestehenden Datenpfad eintragen, fertig — keine Neuregistrierung nötig.

### 3. signal-cli starten und registrieren

```bash
docker compose up -d signal-cli
```

Als linked device registrieren — QR-Code generieren:

```bash
curl -s "http://localhost:8085/v1/qrcodelink?device_name=ttrpg-bot" -o qrcode.png
```

PNG öffnen → Signal → Einstellungen → Verknüpfte Geräte → Gerät hinzufügen → scannen.

### 4. `.env` befüllen

```bash
cp .env.example .env
```

```env
# DM Provider: openai | anthropic | gemini
DM_PROVIDER=openai
OPENAI_API_KEY=sk-...           # für DM_PROVIDER=openai
ANTHROPIC_API_KEY=sk-ant-...   # immer benötigt (Charakter-Extraktion, Komprimierung)
# GEMINI_API_KEY=...            # für DM_PROVIDER=gemini

# Optionale Modell-Overrides (Defaults siehe config.py)
# OPENAI_DM_MODEL=gpt-4o
# ANTHROPIC_DM_MODEL=claude-sonnet-4-6
# GEMINI_DM_MODEL=gemini-2.0-flash

SIGNAL_PHONE_NUMBER=+43...      # Bot-Nummer (linked device)
ADMIN_PHONE_NUMBER=+43...       # Wer !kommandos schicken darf
GCP_PROJECT=...                 # GCP Projekt-ID für Vertex AI (Avatar-Generierung)
GCP_LOCATION=us-central1
```

Alle Variablen mit Beschreibung und Defaults: siehe `.env.example`.

### 5. GCP Service Account (für Avatar-Generierung)

```bash
gcloud iam service-accounts create ttrpg-bot \
  --display-name="TTRPG Bot" --project=PROJEKT_ID

gcloud projects add-iam-policy-binding PROJEKT_ID \
  --member="serviceAccount:ttrpg-bot@PROJEKT_ID.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"

gcloud iam service-accounts keys create gcp-sa.json \
  --iam-account="ttrpg-bot@PROJEKT_ID.iam.gserviceaccount.com"
```

`gcp-sa.json` im Projektordner ablegen (in `.gitignore`, nie ins Git!).

### 6. Addons einbinden (optional)

Addons werden als zusätzliche Volume-Mounts aktiviert — jeder Flavour eines Addons bekommt eine eigene Zeile in `docker-compose.yml`:

```yaml
volumes:
  - /pfad/zu/mein-addon/flavours/mein-flavour:/mnt/ttrpg/_engine/flavours/mein-flavour
```

Kein Code-Change nötig — der Bot erkennt neue Flavour-Ordner automatisch. Wie du ein eigenes Addon baust steht in `ttrpg-adult/README.md` als Referenzimplementierung (Struktur, manifest.yaml, CHARACTER_FIELDS.yaml).

### 7. Starten

```bash
docker compose up -d
```

Beim ersten Start klont Docker automatisch das ttrpg Engine-Repo und legt `status.yaml` aus der Vorlage an. Spieler danach per `!invite` direkt über den Bot registrieren.

---

## Neues Abenteuer anlegen

1. Spieler registrieren (einmalig pro Spieler):
   ```
   !invite +43... Name
   ```
2. Abenteuer anlegen — erstellt Ordnerstruktur, Signal-Gruppe und schickt Willkommenstext:
   ```
   !new Mein Abenteuer @Spieler1 @Spieler2 --fantasy
   ```
   Optionale Flavours mit `--name` anhängen. Addon-Flavours funktionieren genauso sobald das Addon eingebunden ist.
3. Session 0 starten (Charaktererstellung + Weltenbau):
   ```
   !session0
   ```

Der Bot finalisiert Session 0 automatisch sobald alle Charakterblätter vollständig sind — er legt YAMLs an, generiert Portraits und schickt Charakterblatt-PDFs in die Gruppe.

---

## Kommandos

### All players (group, private setup channel, or 1:1)

Commands work in all three contexts: the adventure group, the private 1:1 setup channel, and direct messages.

| Command | Description |
|---------|-------------|
| `!help` | Show available commands |
| `!status` | In group: current adventure state. In DM: list your adventures |
| `!status <name>` | In DM: details of a named adventure (own adventures only) |
| `!charakter` | Show your character sheet + PDF |
| `!charakter <name>` | Find a specific character by name |
| `!avatar` | Show your current portrait and prompt |
| `!avatar regen` | Regenerate portrait with the existing prompt |
| `!avatar prompt` | Show the current image generation prompt |
| `!avatar prompt <text>` | Update the prompt and regenerate portrait |
| `!bugreport <text>` | Report a bug |

### Admin only

| Command | Description |
|---------|-------------|
| `!save` | Compress & save game state → session.yaml, end session |
| `!session0` | Start Session 0 — DM leads world-building + intro scene |
| `!new <name> [@Player1 @Player2 ...]` | Create adventure, Signal group, private setup channels per player |
| `!invite +43... Name` | Register player (creates players/Name.yaml) + welcome message |
| `!dm @Player <text>` | Secret 1:1 message to a player |
| `!players` | List all registered players with number and role |
| `!showme [idea]` | Generate and send an atmospheric scene image — optional idea as inspiration |
| `!usage` | API usage & estimated costs (all providers + Vertex AI) |

---

## Dateistruktur

```
ttrpg-signal/                  ← dieses Repo (Bot-Code)
├── Dockerfile
├── docker-compose.yml
├── .env                       ← nie ins Git!
├── gcp-sa.json                ← nie ins Git!
└── bot/
    ├── main.py                ← Event Loop, Kommando-Router
    ├── signal_client.py       ← signal-cli REST API Wrapper
    ├── dm_engine.py           ← DM-Logik, History, Log, Komprimierung
    ├── llm_client.py          ← AI-Provider-Adapter (OpenAI / Anthropic / Gemini)
    ├── session_manager.py     ← YAML lesen/schreiben, Kontext-Builder
    ├── generate_avatar.py     ← Vertex AI Imagen, Prompt-Verwaltung
    ├── usage_tracker.py       ← Token- und Kosten-Tracking
    └── config.py              ← alle Env-Variablen

ttrpg/                         ← separates Repo, eingebunden via TTRPG_PATH
├── status.yaml                ← Abenteuer-Übersicht + Signal-Gruppen
├── status.example.yaml        ← Vorlage
├── players/                   ← ein YAML pro Spieler (Telefonnummer etc.)
├── _engine/
│   ├── DUNGEON_MASTER.md      ← DM System-Prompt
│   ├── CHARACTER_SETUP.md     ← privater Setup-Kanal Prompt
│   └── templates/             ← YAML-Vorlagen für neue Abenteuer
└── adventures/
    └── mein-abenteuer/
        ├── session.yaml
        ├── setting.yaml
        ├── npcs.yaml
        ├── spielprotokoll.jsonl   ← Crash-sicheres Log (wird bei !save geleert)
        └── characters/
            ├── held.yaml
            ├── held_avatar.png
            └── held_avatar.txt    ← Imagen-Prompt (optional, überschreibt YAML-Feld)
```

---

## Persistenz

| Was | Wo | Wann |
|-----|----|------|
| Jede Nachricht | `spielprotokoll.jsonl` | sofort (append) |
| History bei Neustart | aus `spielprotokoll.jsonl` | beim ersten Zugriff |
| Spielstand/Zusammenfassung | `session.yaml` | bei `!save` (Claude komprimiert) |
| JSONL | geleert | bei `!save` |

---

## Backup

Die Engine-Dateien (`_engine/`, Templates) kommen aus Git und sind jederzeit wiederherstellbar.
Was **nicht** in Git liegt und gesichert werden sollte:

| Was | Wo im `ttrpg-data` Volume |
|-----|--------------------------|
| Spielstände & Szenen | `adventures/*/session.yaml` |
| Charakterblätter | `adventures/*/characters/*.yaml` |
| Portraits & PDFs | `adventures/*/characters/*.png`, `*.pdf` |
| Spielprotokolle | `adventures/*/spielprotokoll.jsonl` |
| Spieler-Registry | `players/*.yaml` |
| Abenteuer-Übersicht | `status.yaml` |

Einfachstes Backup — `ttrpg/`-Ordner sichern (Bind Mount, direkt zugänglich):

```bash
tar czf /pfad/zu/backup/ttrpg-backup-$(date +%Y%m%d).tar.gz -C /pfad/zu/ttrpg-signal ttrpg
```

---

## Bot neu bauen (nach Code-Änderungen)

```bash
cd /pfad/zu/ttrpg-signal
git pull
docker compose build ttrpg-bot
docker compose up -d ttrpg-bot
```
