# EnglishForge

Personal English practice app — BYOK, self-hosted, multi-user.

Practice English conversation with an AI tutor via voice or text. Get real-time corrections, build vocabulary with spaced repetition, and track your progress.

## Features

- **AI Conversation Tutor** — Roleplay scenarios (job interview, restaurant, hotel, etc.) with CEFR level adjustment (A1–C2)
- **CEFR Assessment** — Conversational placement test (up to 10 questions) that estimates your level, strengths, and weaknesses
- **Learning Paths** — LLM-generated personalized curriculum based on your assessment, with per-level lesson targets and automatic progression
- **BYOK Multi-Provider LLM** — OpenAI, Anthropic, DeepSeek, Fireworks, ClinePass, or any OpenAI-compatible endpoint
- **Voice** — STT (4 modes: Web Speech API, Whisper WASM, faster-whisper server, Moonshine via personal-api) + TTS (Pocket TTS via personal-api)
- **Vocabulary SRS** — SM-2 spaced repetition (Anki-style) with flashcards and quiz modes
- **Lessons** — Static grammar library + dynamic LLM-generated lessons based on your error patterns
- **Dashboard** — Progress stats, weekly charts, CEFR level estimation
- **PWA** — Installable on mobile and desktop
- **Multi-user** — JWT auth, per-user settings, encrypted API keys

## Quick Start

### Prerequisites

- Docker & Docker Compose
- At least one LLM API key (OpenAI, Anthropic, DeepSeek, etc.)

### Setup

```bash
# 1. Clone and enter the project
cd english-forge

# 2. Copy environment file
cp .env.example .env

# 3. Edit .env — at minimum set:
#    - JWT_SECRET_KEY (generate: openssl rand -hex 32)
#    (LLM API keys are added later in the app's Settings UI)
nano .env

# 4. Start everything
# NOTE: For Coolify deployments, env_file is optional — the .env file
# will be provided by Coolify's environment configuration if deploying there.
docker compose up -d

# 5. Open the app
# Frontend: http://localhost:3590
# Backend API: http://localhost:8230
```

### First Run

1. Open `http://localhost:3590` in your browser
2. Create an account (email + password)
3. Go to **Settings** → **LLM Providers** → Add at least one provider with your API key
4. Start a conversation!

## Configuration

### LLM Providers

Add providers through the Settings UI. Each provider needs:

| Field | Description |
|-------|-------------|
| Provider Name | Label (e.g. "openai", "deepseek") |
| API Key | Your API key (encrypted at rest in DB) |
| Base URL | API endpoint (e.g. `https://api.openai.com/v1`) |
| Model | Model name (e.g. `gpt-4.1-mini`) |
| Protocol | `openai` (OpenAI-compatible) or `anthropic` |
| Priority | Higher = tried first in fallback chain |

**Note:** The `*_API_KEY` env vars in `.env.example` are currently **not** consumed by the LLM router — providers come exclusively from the Settings UI / `provider_configs` table (TBD).

**Supported out of the box:**

| Provider | Base URL | Protocol |
|----------|----------|----------|
| OpenAI | `https://api.openai.com/v1` | openai |
| Anthropic | (built-in SDK) | anthropic |
| DeepSeek | `https://api.deepseek.com/v1` | openai |
| Fireworks | `https://api.fireworks.ai/inference/v1` | openai |
| ClinePass | Custom | openai |
| Custom | Any | openai |

### TTS (Pocket TTS via personal-api)

If you have Pocket TTS running via `personal-api` on the `coolify` Docker network:

1. Set `PERSONAL_API_URL=http://personal-api:8000` in `.env`
2. Set `TTS_DEFAULT_VOICE` to a valid voice name
3. The backend automatically joins the `coolify` network

**Note:** Verify available voices with `GET /v1/voices` on your Pocket TTS instance before setting the default.

### STT Modes

| Mode | Description | Best For |
|------|-------------|----------|
| `web_speech` | Browser Web Speech API (default) | Live conversation (lowest latency) |
| `whisper_wasm` | Whisper.cpp compiled to WASM, runs in browser | Offline, no server needed |
| `whisper_server` | faster-whisper in the backend | Fallback, server-side processing |
| `personal_api` | Moonshine via personal-api (RQ queue) | Post-session review (highest accuracy) |

Change mode in Settings → Preferences → STT Mode.

**Note:** The conversation and assessment pages always use Web Speech API in the browser. The `whisper_server` and `personal_api` modes run server-side in the backend's WebSocket flow. `whisper_wasm` is selectable in Settings but has no client implementation yet.

## Architecture

```
┌─────────────────────────────┐
│  Frontend (Next.js 16 PWA)  │  port 3590
│  React 19, Tailwind 4,      │
│  shadcn-style UI, dark      │
└──────────────┬──────────────┘
               │ REST + WebSocket (JWT)
┌──────────────▼──────────────┐
│  Backend (FastAPI, Python)  │  port 8230
│  JWT auth, multi-user       │
│  LLM router (BYOK)          │
│  SRS engine (SM-2)          │
│  Assessment + learning path │
└──────────────┬──────────────┘
               │
    ┌──────────┼──────────────┬──────────────┐
    ▼          ▼              ▼              ▼
 SQLite      personal-api   LLM APIs    faster-whisper
 (default)    (TTS/STT)     (OpenAI...)  (optional)
    │
    └─ PostgreSQL also supported (the bundled compose
       stack ships a postgres:16 db service)
```

## Development

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8230
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

### Tests

```bash
cd backend
python -m pytest
```

Test config lives in `backend/pytest.ini` (`testpaths = tests`). The suite covers the LLM router JSON parsing, lesson normalization, assessment logic, and learning-path schemas. The frontend has no test suite — only ESLint (`npm run lint`).

### Database

SQLite is the default database (`sqlite+aiosqlite:///./data/englishforge.db`, set in `backend/app/config.py`). In Docker the file lives on the `backend_data` volume (`/app/data`). PostgreSQL is also supported via `DATABASE_URL` — note that `docker-compose.yml` currently still ships a `postgres:16-alpine` `db` service and `.env.example` points `DATABASE_URL` at it. **TBD:** the compose stack and the "SQLite default" haven't been fully reconciled yet — if you want SQLite in Docker, remove the `db` service and the `DATABASE_URL` from your `.env`; if you want PostgreSQL, keep them.

Schema handling (no manual migrations needed for routine changes):

- Tables are created with `Base.metadata.create_all()` on startup.
- Columns added to existing tables after first deployment are applied by the startup sync in `backend/app/main.py` (`_COLUMNS_TO_ADD`), guarded by an inspector check so it's safe to run on every launch.
- Partial unique indexes enforcing invariants (one in-progress assessment, one active learning path per user) are created at startup (`_INDEXES_TO_ADD`) — see ADR-007.
- Alembic remains available for full table migrations: `alembic revision --autogenerate -m "description"` and `alembic upgrade head` (run from `backend/`).

## Documentation

- [`spec.md`](./spec.md) — product specification (domain, features, data model, integrations)
- [`DESIGN.md`](./DESIGN.md) — technical design (architecture, layers, key flows)
- [`ADR.md`](./ADR.md) — architecture decision records

## Environment Variables

See `.env.example` for the full list. Required:

| Variable | Required | Description |
|----------|----------|-------------|
| `JWT_SECRET_KEY` | **Yes** | Secret for JWT signing (also derives the Fernet key for API-key encryption when `SETTINGS_ENCRYPTION_KEY` is empty) |
| `DATABASE_URL` | No (default: `sqlite+aiosqlite:///./data/englishforge.db`) | SQLAlchemy async URL. Use `postgresql+asyncpg://...` for PostgreSQL (the bundled compose stack's `.env.example` sets this). |
| `APP_PIN` | No | Optional PIN for extra protection when exposed outside the LAN. Currently only logged at startup when set — no middleware enforces it yet (TBD). |
| `PERSONAL_API_URL` | No | TTS/STT via personal-api. For Coolify deployments, this may be set by the platform. |
| `STT_MODE` | No | STT mode (default: web_speech) |

LLM API keys are configured in the app's Settings UI (encrypted in the DB) — see the note under [LLM Providers](#llm-providers).

**Note on `.env` for Coolify**: The `docker-compose.yml` has `env_file:.env` marked as `required: false`. When deploying to Coolify, the `.env` file may be provided by the platform's environment configuration, so it's not strictly required in the compose file itself.

## License

Personal project — use freely.