# ttrpg-signal

Signal-Bot der als Dungeon Master via Claude API antwortet. Spieler schreiben in eine Signal-Gruppe (oder 1:1), der Bot antwortet als DM.

> **Spielwelt-Daten:** Engine, Templates und Abenteuer-Struktur liegen in einem separaten Repo:
> [phieb/ttrpg](https://github.com/phieb/ttrpg) — wird über `TTRPG_PATH` eingebunden (z.B. als NFS-Mount oder lokaler Ordner).

## Stack

- **signal-cli** (`bbernhard/signal-cli-rest-api`) — Signal Protokoll
- **Python 3.11** — Bot-Service
- **Claude API** (`claude-haiku-4-5`) — DM-Logik mit Prompt Caching
- **Vertex AI Imagen 4** — Charakter-Portrait-Generierung
- **Docker** — containerisiert

---

## Setup

### 1. Repos klonen

```bash
git clone https://github.com/phieb/ttrpg-signal.git
git clone https://github.com/phieb/ttrpg.git
```

Die `docker-compose.yml` liegt im ttrpg-signal Repo. Du kannst sie direkt dort oder in einem separaten Deployment-Ordner betreiben — passe die Pfade in `docker-compose.yml` entsprechend an.

### 2. signal-cli starten und registrieren

```bash
cd /pfad/zu/ttrpg-signal
docker compose up -d signal-cli
```

Als linked device registrieren — QR-Code generieren:

```bash
curl -s "http://localhost:8085/v1/qrcodelink?device_name=ttrpg-bot" -o qrcode.png
```

PNG öffnen → Signal → Einstellungen → Verknüpfte Geräte → Gerät hinzufügen → scannen.

### 3. `.env` befüllen

```bash
cp .env.example .env
```

```env
ANTHROPIC_API_KEY=sk-ant-...
SIGNAL_PHONE_NUMBER=+43...      # Bot-Nummer (linked device)
ADMIN_PHONE_NUMBER=+43...       # Wer !kommandos schicken darf
TTRPG_PATH=/pfad/zu/ttrpg      # Wo das ttrpg-Repo liegt (lokal oder NFS-Mount)
GCP_PROJECT=...                 # GCP Projekt-ID für Vertex AI (Avatar-Generierung)
GCP_LOCATION=us-central1
```

Alle Variablen mit Beschreibung und Defaults: siehe `.env.example`.

### 4. GCP Service Account (für Avatar-Generierung)

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

### 5. Spielwelt vorbereiten

Im ttrpg-Repo `status.yaml` aus der Vorlage anlegen:

```bash
cp /pfad/zu/ttrpg/status.example.yaml /pfad/zu/ttrpg/status.yaml
```

Spieler werden danach per `!invite` direkt über den Bot registriert.

### 6. Repos klonen und `docker-compose.yml` anlegen

Da das Repo privat ist, muss es lokal geklont sein — Docker baut direkt aus dem lokalen Ordner.
`.env` und `gcp-sa.json` liegen im selben Ordner wie die `docker-compose.yml`.

```bash
git clone https://github.com/phieb/ttrpg-signal.git /pfad/zu/ttrpg-signal
```

```yaml
services:

  signal-cli:
    image: bbernhard/signal-cli-rest-api:latest
    container_name: signal-cli
    restart: unless-stopped
    environment:
      - MODE=native
    ports:
      - "8085:8080"
    volumes:
      - ./signal-cli-data:/home/.local/share/signal-cli

  ttrpg-bot:
    build: /pfad/zu/ttrpg-signal          # lokaler Clone des privaten Repos
    container_name: ttrpg-bot
    restart: unless-stopped
    depends_on:
      - signal-cli
    env_file:
      - .env
    volumes:
      - /pfad/zu/ttrpg:/mnt/ttrpg          # ttrpg-Repo → im Container immer /mnt/ttrpg
      - ./gcp-sa.json:/app/gcp-sa.json:ro
    environment:
      - GOOGLE_APPLICATION_CREDENTIALS=/app/gcp-sa.json
```

> `TTRPG_PATH` in der `.env` muss auf den Container-Pfad zeigen, also `/mnt/ttrpg`.

### 7. Bot starten

```bash
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
| `!zeigmal [Idee]` | Atmosphärisches Szenen-Bild generieren und schicken — optionale Idee fließt als Inspiration ein |
| `!usage` | API-Nutzung & geschätzte Kosten anzeigen (Anthropic + Vertex AI) |

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
    ├── dm_engine.py           ← Claude API, History, Log, Komprimierung
    ├── session_manager.py     ← YAML lesen/schreiben, Kontext-Builder
    ├── generate_avatar.py     ← Vertex AI Imagen
    └── config.py

ttrpg/                         ← separates Repo, eingebunden via TTRPG_PATH
├── status.yaml                ← Abenteuer-Übersicht + Signal-Gruppen
├── status.example.yaml        ← Vorlage
├── players/                   ← ein YAML pro Spieler (Telefonnummer etc.)
├── _engine/
│   ├── DUNGEON_MASTER.md      ← System-Prompt für Claude
│   └── templates/             ← YAML-Vorlagen für neue Abenteuer
└── adventures/
    └── mein-abenteuer/
        ├── session.yaml
        ├── setting.yaml
        ├── npcs.yaml
        ├── spielprotokoll.jsonl   ← Crash-sicheres Log (wird bei !pause geleert)
        └── characters/
            ├── held.yaml
            └── held_avatar.png
```

---

## Persistenz

| Was | Wo | Wann |
|-----|----|------|
| Jede Nachricht | `spielprotokoll.jsonl` | sofort (append) |
| History bei Neustart | aus `spielprotokoll.jsonl` | beim ersten Zugriff |
| Spielstand/Zusammenfassung | `session.yaml` | bei `!pause` (Claude komprimiert) |
| JSONL | geleert | bei `!pause` |

---

## Bot neu bauen (nach Code-Änderungen)

```bash
cd /pfad/zu/ttrpg-signal
git pull
cd /pfad/zu/deployment-ordner
docker compose build --no-cache ttrpg-bot
docker compose up -d ttrpg-bot
```
