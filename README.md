# EnglishForge

Personal English practice app — BYOK, self-hosted, multi-user.

Practice English conversation with an AI tutor via voice or text. Get real-time corrections, build vocabulary with spaced repetition, and track your progress.

## Features

- **AI Conversation Tutor** — Roleplay scenarios (job interview, restaurant, hotel, etc.) with CEFR level adjustment (A1–C2)
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
#    - At least one LLM API key (e.g. OPENAI_API_KEY)
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

Add providers through the Settings UI or configure defaults in `.env`. Each provider needs:

| Field | Description |
|-------|-------------|
| Provider Name | Label (e.g. "openai", "deepseek") |
| API Key | Your API key (encrypted at rest in DB) |
| Base URL | API endpoint (e.g. `https://api.openai.com/v1`) |
| Model | Model name (e.g. `gpt-4.1-mini`) |
| Protocol | `openai` (OpenAI-compatible) or `anthropic` |
| Priority | Higher = tried first in fallback chain |

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

## Architecture

```
┌─────────────────────────────┐
│  Frontend (Next.js 14+ PWA) │  port 3590
│  shadcn/ui, dark theme      │
└──────────────┬──────────────┘
               │ REST + WebSocket (JWT)
┌──────────────▼──────────────┐
│  Backend (FastAPI, Python)  │  port 8230
│  JWT auth, multi-user       │
│  LLM router (BYOK)          │
│  SRS engine (SM-2)          │
└──────────────┬──────────────┘
               │
    ┌──────────┼──────────────┬──────────────┐
    ▼          ▼              ▼              ▼
 SQLite     personal-api   LLM APIs    faster-whisper
 (primary)   (TTS/STT)     (OpenAI...)  (optional)
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

### Database

SQLite is used via SQLAlchemy's `DeclarativeBase`. Tables are created automatically with `Base.metadata.create_all()` on first app start. For column additions that may be missing from existing databases, a startup sync mechanism (`_COLUMNS_TO_ADD` in `backend/app/main.py`) runs on every app launch and adds any missing columns with their declared defaults. This is safe to run repeatedly since each column is only added if absent (guarded by an inspector check).

Alembic is still available for full table migrations via `alembic revision --autogenerate -m "description"` and `alembic upgrade head`, but trivial column additions no longer require manual migration.

## Environment Variables

See `.env.example` for the full list. Required:

| Variable | Required | Description |
|----------|----------|-------------|
| `JWT_SECRET_KEY` | **Yes** | Secret for JWT signing |
| `DATABASE_URL` | Yes (Docker sets it) | PostgreSQL connection string |
| At least one `*_API_KEY` | **Yes** | LLM provider key |
| `PERSONAL_API_URL` | No | TTS/STT via personal-api. For Coolify deployments, this may be set by the platform. |
| `STT_MODE` | No | STT mode (default: web_speech) |

**Note on `.env` for Coolify**: The `docker-compose.yml` has `env_file:.env` marked as `required: false`. When deploying to Coolify, the `.env` file may be provided by the platform's environment configuration, so it's not strictly required in the compose file itself.

## License

Personal project — use freely.