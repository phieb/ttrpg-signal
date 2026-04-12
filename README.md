# ttrpg-signal

Signal-Bot der als Dungeon Master via Claude API antwortet. Spieler schreiben in eine Signal-Gruppe (oder 1:1), der Bot antwortet als DM.

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
GOOGLE_API_KEY=...              # optional, nicht aktiv genutzt
SIGNAL_PHONE_NUMBER=+43...      # Castle Assistant Nummer (linked device)
ADMIN_PHONE_NUMBER=+43...       # Wer !kommandos schicken darf
TTRPG_PATH=/mnt/ttrpg
MAX_CONTEXT_TOKENS=3000
HISTORY_MESSAGES=10
RESPONSE_DELAY_SECONDS=2
GCP_PROJECT=...                 # GCP Projekt-ID für Vertex AI
GCP_LOCATION=us-central1
```

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

1. Signal-Gruppe auf dem Handy erstellen
2. Gruppen-ID auslesen:
   ```bash
   curl -s http://localhost:8085/v1/groups/+43NUMMER
   ```
3. In `status.yaml` eintragen:
   ```yaml
   abenteuer:
     - ordner: "mein_abenteuer"
       name: "Mein Abenteuer"
       status: session_0
       signal_gruppe: "GRUPPEN_ID"
       spieler:
         - name: "Phieb"
         - name: "Markus"
   ```
4. Ordner anlegen via Signal: `!neu Mein Abenteuer`
5. Session 0 starten: `!session0`

---

## Kommandos

### Alle registrierten Spieler

| Kommando | Beschreibung |
|----------|-------------|
| `!help` | Verfügbare Kommandos anzeigen |
| `!charakter` | Eigene Charaktere anzeigen |
| `!charakter <name>` | Bestimmten Charakter anzeigen |

### Nur Admin

| Kommando | Beschreibung |
|----------|-------------|
| `!status` | Aktueller Spielstand (Ort, Szene, Quests) |
| `!pause` | Spielstand speichern, Session beenden |
| `!neu <name>` | Neues Abenteuer anlegen |
| `!session0` | Session 0 starten |
| `!dm @Spieler <text>` | Geheime 1:1 Nachricht an Spieler |
| `!avatare` | Charakterliste anzeigen |
| `!avatare <name>` | Portrait für diesen Charakter generieren |

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
