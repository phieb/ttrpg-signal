# ttrpg-signal

Signal-Bot der als Dungeon Master via Claude API antwortet. Spieler schreiben in eine Signal-Gruppe (oder 1:1), der Bot antwortet als DM.

> **Spielwelt-Daten:** Engine, Templates und Abenteuer-Struktur liegen in einem separaten Repo:
> [phieb/ttrpg](https://github.com/phieb/ttrpg) — wird als NFS-Mount unter `TTRPG_PATH` eingebunden.

## Stack

- **signal-cli** (`bbernhard/signal-cli-rest-api`) — Signal Protokoll
- **Python 3.11** — Bot-Service
- **Claude API** (`claude-haiku-4-5`) — DM-Logik mit Prompt Caching
- **Vertex AI Imagen 4** — Charakter-Portrait-Generierung
- **Docker** — läuft auf Various (Alpine Linux)
- **NFS** — TTRPG-Daten vom NAS gemountet

---

## Setup

### 1. signal-cli starten

```bash
cd ~/docker/ttrpg-signal
docker compose up -d signal-cli
```

Als linked device registrieren — QR-Code generieren:

```bash
curl -s "http://localhost:8085/v1/qrcodelink?device_name=ttrpg-bot" -o qrcode.png
```

PNG öffnen → Signal → Einstellungen → Verknüpfte Geräte → Gerät hinzufügen → scannen.

### 2. `.env` befüllen

```bash
cp .env.example .env
```

```env
ANTHROPIC_API_KEY=sk-ant-...
SIGNAL_PHONE_NUMBER=+43...      # Bot-Nummer (linked device)
ADMIN_PHONE_NUMBER=+43...       # Wer !kommandos schicken darf
TTRPG_PATH=/mnt/ttrpg
MAX_CONTEXT_TOKENS=3000
HISTORY_MESSAGES=6
RESPONSE_DELAY_SECONDS=2
GCP_PROJECT=...                 # GCP Projekt-ID für Vertex AI
GCP_LOCATION=us-central1
RATE_LIMIT_MESSAGES=5
RATE_LIMIT_WINDOW=60
BATCH_WINDOW_SECONDS=60
MAX_LOG_LINES=500
```

Alle Variablen mit Beschreibung: siehe `.env.example`.

### 3. GCP Service Account (für Avatar-Generierung)

```bash
export PATH="/opt/google-cloud-sdk/bin:$PATH"
gcloud auth login

gcloud iam service-accounts create ttrpg-bot \
  --display-name="TTRPG Bot" --project=PROJEKT_ID

gcloud projects add-iam-policy-binding PROJEKT_ID \
  --member="serviceAccount:ttrpg-bot@PROJEKT_ID.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"

gcloud iam service-accounts keys create gcp-sa.json \
  --iam-account="ttrpg-bot@PROJEKT_ID.iam.gserviceaccount.com"
```

`gcp-sa.json` liegt im Projektordner (in `.gitignore`, nie ins Git!).

### 4. Spieler anlegen

```yaml
# /mnt/coding/ttrpg/players/spieler.yaml
spieler:
  name: "Name"
  telefon: "+43..."
  rolle: spieler
```

### 5. Bot starten

```bash
cd ~/docker/ttrpg-signal
docker compose up -d
```

---

## Neues Abenteuer anlegen

1. Spieler registrieren (einmalig pro Spieler):
   ```
   !invite +43... Name
   ```
2. Abenteuer anlegen — erstellt Ordnerstruktur, Signal-Gruppe und schickt Willkommenstext:
   ```
   !neu Mein Abenteuer @Spieler1 @Spieler2
   ```
3. Session 0 starten (Charaktererstellung + Weltenbau):
   ```
   !session0
   ```

Der Bot finalisiert Session 0 automatisch sobald alle Charakterblätter vollständig sind — er legt YAMLs an, generiert Portraits und schickt Charakterblatt-PDFs in die Gruppe.

---

## Kommandos

### Alle Spieler (1:1 oder Gruppe)

| Kommando | Beschreibung |
|----------|-------------|
| `!help` | Verfügbare Kommandos anzeigen |
| `!charakter` | Eigenes Charakterblatt + PDF anzeigen (im Gruppenchat: Charakter des laufenden Abenteuers) |
| `!charakter <name>` | Bestimmten Charakter nach Name suchen |
| `!avatar` | Eigenes Portrait anzeigen / neu generieren (im Gruppenchat: direkt der eigene Charakter) |

### Nur Admin

| Kommando | Beschreibung |
|----------|-------------|
| `!status` | Aktueller Spielstand (Ort, letzte Szene, Ereignisse) |
| `!pause` | Spielstand per Claude komprimieren → session.yaml, Session beenden |
| `!session0` | Session 0 starten — DM führt durch Charaktererstellung |
| `!neu <name> [@Spieler1 @Spieler2 ...]` | Neues Abenteuer anlegen, Signal-Gruppe erstellen, Spieler einladen |
| `!invite +43... Name` | Spieler registrieren (players/Name.yaml anlegen) + Willkommensnachricht schicken |
| `!dm @Spieler <text>` | Geheime 1:1 Nachricht an einen Spieler |
| `!spieler` | Alle registrierten Spieler mit Nummer und Rolle anzeigen |
| `!spiele` | Alle Abenteuer mit Status und letztem Spieltag anzeigen |
| `!spiel <name>` | Zusammenfassung eines Abenteuers (Setting, Spieler, Charaktere, letzte Szene) |

---

## Dateistruktur

```
/mnt/coding/ttrpg-signal/     ← Bot-Code
├── Dockerfile
├── docker-compose.yml         ← liegt in ~/docker/ttrpg-signal/
├── requirements.txt
├── .env                       ← nie ins Git!
├── gcp-sa.json                ← nie ins Git!
└── bot/
    ├── main.py                ← Event Loop, Kommando-Router
    ├── signal_client.py       ← signal-cli REST API Wrapper
    ├── dm_engine.py           ← Claude API, History, Log, Komprimierung
    ├── session_manager.py     ← YAML lesen/schreiben, Kontext-Builder
    ├── generate_avatar.py     ← Vertex AI Imagen
    └── config.py

/mnt/coding/ttrpg/            ← TTRPG Engine + Daten (NFS vom NAS)
├── status.yaml               ← Abenteuer-Übersicht + Signal-Gruppen
├── players/                  ← Spieler-Verzeichnis
│   ├── phieb.yaml
│   └── markus.yaml
├── _engine/
│   └── DUNGEON_MASTER.md     ← System-Prompt für Claude
└── adventures/
    └── mein_abenteuer/
        ├── session.yaml
        ├── setting.yaml
        ├── npcs.yaml
        ├── spielprotokoll.jsonl   ← Crash-sicheres Log (wird bei !pause geleert)
        └── characters/
            ├── held.yaml
            └── held_portrait_prompt.txt
```

## Persistenz

| Was | Wo | Wann |
|-----|----|----|
| Jede Nachricht | `spielprotokoll.jsonl` | sofort (append) |
| History bei Neustart | aus `spielprotokoll.jsonl` | beim ersten Zugriff |
| Spielstand/Zusammenfassung | `session.yaml` | bei `!pause` (Claude komprimiert) |
| JSONL | geleert | bei `!pause` |

## Bot neu bauen (nach Code-Änderungen)

```bash
cd ~/docker/ttrpg-signal
docker compose build ttrpg-bot
docker compose up -d ttrpg-bot
```
