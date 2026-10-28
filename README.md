# EnglishForge

Personal English practice app — BYOK, self-hosted, multi-user.

Practice English conversation with an AI tutor via voice or text. Get real-time corrections, build vocabulary with spaced repetition, and track your progress.

## Features

- **AI Conversation Tutor** — Roleplay scenarios (job interview, restaurant, hotel, etc.) with CEFR level adjustment (A1–C2), proactive in-character tutor opening greetings with Pocket TTS synthesis/autoplay, custom scenario creation modal with LLM-generated greetings, and collapsible scenario briefing header
- **CEFR Assessment** — Multi-skill placement: voice interview (min 6 exchanges), audio-only listening items, and read-aloud pronunciation scoring (WER + phoneme error rate + fluency), with per-dimension scores (A1–C2)
- **Learning Paths** — LLM-generated personalized curriculum based on your assessment, with per-level lesson targets, specialized multi-type interactive lessons (conversation roleplay with direct AI tutor launch, vocabulary with one-click SRS deck import, listening with audio passages, grammar with rule formulas), server-side exercise grading with flexible normalization, elimination of skip facades, and automatic CEFR level progression
- **BYOK Multi-Provider LLM** — OpenAI, Anthropic, DeepSeek, Fireworks, ClinePass, or any OpenAI-compatible endpoint, with automatic server environment fallback (`.env` keys) when no database providers are configured
- **Voice & STT** — Universal recording via MediaRecorder with live audio visualizer and server-side transcription (`/api/sessions/{id}/recordings` and `/api/assessment/{id}/recordings` via Moonshine / faster-whisper), plus browser Web Speech API fallback; TTS (Pocket TTS via personal-api). Streamlined STT options removing non-functional WASM facades.
- **Vocabulary SRS** — SM-2 spaced repetition (Anki-style) with dedicated Flashcard Review mode (`/vocab?mode=review`), Web Speech pronunciation audio with US/UK accent toggle and keyboard shortcuts, 4-tier response grading (Again, Hard, Good, Easy), session retention metrics, dashboard "Review Now" quick launcher, and multiple-choice / fill-in-the-blank quiz modes
- **Lessons Hub** — Interactive grammar and vocabulary hub featuring three integrated modes: curated static library with completion tracking and daily streak sync; on-demand topical and CEFR-targeted (A1–C2) AI lesson generation (without requiring prior conversation error history) with deterministic seed fallbacks; and Learning Path curriculum progression. All exercises enforce server-side evaluation with upfront answer and explanation stripping to prevent answer pre-disclosure.
- **Dashboard** — Progress stats, weekly charts, CEFR level estimation
- **PWA** — Installable on mobile and desktop
- **Multi-user** — JWT auth, per-user settings, encrypted API keys

## Quick Start

### Prerequisites

- Docker & Docker Compose
- At least one LLM API key (OpenAI, Anthropic, DeepSeek, etc.) or configured server environment keys

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
3. `TTS_MODEL` pins the Pocket TTS model sent on every `/v1/speak` call (default `english_2026-04_24l`) — personal-api servers default to a Spanish model, so english-forge always sends the English one. Unknown model ids are ignored by Pocket TTS (hot-swap safe, and older personal-api servers ignore the field entirely).
4. The backend automatically joins the `coolify` network

**Note:** Verify available voices with `GET /v1/voices` on your Pocket TTS instance before setting the default.

### STT Modes

| Mode | Description | Best For |
|------|-------------|----------|
| `web_speech` | Browser Web Speech API (default) | Live conversation (lowest latency) |
| `whisper_wasm` | Whisper.cpp compiled to WASM, runs in browser | Offline, no server needed |
| `whisper_server` | faster-whisper in the backend | Fallback, server-side processing |
| `personal_api` | Moonshine via personal-api (RQ queue) | Post-session review (highest accuracy) |

Change mode in Settings → Preferences → STT Mode.

**Note:** Both conversation and assessment pages use universal voice recording: audio captured via `MediaRecorder` (up to 10MB, with live audio level meter and countdown timer) is transcribed server-side via `POST /api/sessions/{session_id}/recordings` or `POST /api/assessment/{id}/recordings` (Moonshine via personal-api, faster-whisper in-process fallback). If `MediaRecorder` is unavailable or fails, conversation recording falls back seamlessly to client-side Web Speech API. The `whisper_server` and `personal_api` modes also run server-side in the backend's WebSocket flow. `whisper_wasm` is selectable in Settings but has no client implementation yet. Every personal-api transcription call sends `language=en` (`STT_LANGUAGE`), which unlocks real Moonshine word timestamps for English — the input to the pronunciation fluency metrics.

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
 (only)      (TTS/STT)     (OpenAI...)  (optional)
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
pip install -r requirements-dev.txt   # requirements.txt + pytest
python -m pytest
```

Test config lives in `backend/pytest.ini` (`testpaths = tests`). The suite covers the LLM router (tolerant JSON parsing, empty-content/retry policies), lesson normalization, Lessons Hub (on-demand topical generation, CEFR level targeting, deterministic A1–C2 fallbacks, library completion tracking with atomic daily streak sync, and answer stripping), assessment logic and scoring, assessment phase-flow integration (in-memory SQLite), pronunciation scoring, personal-api payload contracts, learning paths (schemas, multi-type lazy content generation, lesson-linked conversation session launch, flexible MCQ/letter normalization, CEFR level advancement, and server-side grading), vocabulary SRS (SM-2 interval and ease-factor calculation, due cards retrieval with null/past review dates, atomic daily review incrementation, filter tabs, regex word-boundary fill-in-the-blank quiz generation, and single-query streak aggregation), and conversation sessions (proactive tutor greetings with TTS synthesis, custom scenario LLM greeting generation, session audio recording transcription via `/api/sessions/{id}/recordings`, payload validation and user isolation, single-query session listing aggregation, and week-1 hardening: startup secret/Fernet validation, login rate limiting and email normalisation, bcrypt length limits, assessment abandon/reclaim, idempotent session billing, per-turn lock ordering, TTS-failure resilience and export credential redaction). The frontend has no test suite — only ESLint (`npm run lint`).

### Maintenance

Reset a user's data (sessions, messages, corrections, vocab, scenarios, assessments, generated lessons, library lesson completions, learning paths, progress, settings) while preserving the account and its LLM provider configs — useful for testing:

```bash
python scripts/clear_user_data.py --email user@example.com --dry-run   # preview
python scripts/clear_user_data.py --email user@example.com
```

The script also resets the user's level to A1 and clears `assessment_completed`.

### Backups & data export

The SQLite file is the only copy of everything. Back it up before upgrading:

```bash
# Docker: consistent copy via SQLite's online backup API (safe while running)
docker compose exec backend sh -c 'sqlite3 /app/data/englishforge.db ".backup /app/data/backup-$(date -u +%Y%m%dT%H%M%SZ).db"'

# Local checkout
./scripts/backup_sqlite.sh                      # → backups/englishforge-<timestamp>.db
./scripts/backup_sqlite.sh backend/data/englishforge.db /var/backups/englishforge
```

`scripts/backup_sqlite.sh` keeps the last 14 files; copy the directory off-host for real disaster recovery (the volume alone is not a backup).

Users can export everything they own as JSON from `GET /api/settings/export` (Settings → not yet surfaced in the UI). The export includes sessions, messages, vocabulary, assessments, lessons, paths and progress; provider API keys are **never** included (only `has_api_key`).

### Session & security lifecycle

- The backend refuses to start if `JWT_SECRET_KEY` is missing, shorter than 32 bytes, or still the placeholder from `.env.example`.
- Failed logins are limited per email and per client IP (`AUTH_RATE_LIMIT_*`); registrations are limited per IP. Exceeding a budget returns `429` with `Retry-After`.
- `DELETE /api/assessment/{id}` abandons an in-progress attempt (soft delete) so a stuck mic/phase can never lock the account out; attempts with no activity for `ASSESSMENT_INACTIVE_DAYS` are reclaimed automatically.
- `PATCH /api/sessions/{id}/end` is idempotent: repeated calls never bill the same minutes twice, and time after the last message (plus `SESSION_END_GRACE_MINUTES`) is not counted, capped by `MAX_SESSION_MINUTES`.

### Database

SQLite is the only supported database (`sqlite+aiosqlite:///./data/englishforge.db`, set in `backend/app/config.py`). In Docker the file lives on the `backend_data` volume (`/app/data`). WAL mode + busy_timeout are applied automatically at startup so concurrent reads/writes don't hit "database is locked". The backend refuses to start if `DATABASE_URL` points anywhere else — deployments upgrading from the removed PostgreSQL stack must migrate their data to SQLite manually.

Schema handling (no manual migrations needed for routine changes):

- Tables are created with `Base.metadata.create_all()` on startup.
- Columns added to existing tables after first deployment are applied by the startup sync in `backend/app/main.py` (`_COLUMNS_TO_ADD`), guarded by an inspector check so it's safe to run on every launch.
- Partial unique indexes enforcing invariants (one in-progress assessment, one active learning path per user) are created at startup (`_INDEXES_TO_ADD`) — see ADR-007. Stored index definitions are compared with the expected SQL and replaced when the predicate changes (e.g. the assessment index gaining `abandoned_at IS NULL`).
- Alembic remains available for full table migrations: `alembic revision --autogenerate -m "description"` and `alembic upgrade head` (run from `backend/`).

## Documentation

- [`spec.md`](./spec.md) — product specification (domain, features, data model, integrations)
- [`DESIGN.md`](./DESIGN.md) — technical design (architecture, layers, key flows)
- [`ADR.md`](./ADR.md) — architecture decision records

## Environment Variables

See `.env.example` for the full list. Required:

| Variable | Required | Description |
|----------|----------|-------------|
| `JWT_SECRET_KEY` | **Yes** | Secret for JWT signing. Startup **fails** if it is missing, weak, or still the placeholder. Generate with `openssl rand -hex 32`. |
| `SETTINGS_ENCRYPTION_KEY` | Strongly recommended | Fernet key for provider API keys. Empty = derived from `JWT_SECRET_KEY`, so rotating the JWT secret makes stored keys unreadable. Validated at startup. |
| `ALLOW_INSECURE_DEFAULTS` | No (default: `false`) | CI/experiments only: downgrades the weak-secret startup check to a warning. |
| `AUTH_RATE_LIMIT_ATTEMPTS` / `AUTH_RATE_LIMIT_WINDOW_SECONDS` | No (default: 5 / 900) | Failed-login budget per email and per client IP. |
| `REGISTER_RATE_LIMIT_ATTEMPTS` / `REGISTER_RATE_LIMIT_WINDOW_SECONDS` | No (default: 5 / 3600) | Registration budget per client IP (successes count). |
| `TRUST_PROXY_HEADERS` | No (default: `true`) | Read the browser IP from `X-Forwarded-For` (set by the Next.js proxy). Disable only when the backend is exposed directly. |
| `ASSESSMENT_INACTIVE_DAYS` | No (default: `7`) | In-progress assessments with no activity for this long are abandoned automatically. |
| `MAX_SESSION_MINUTES` / `SESSION_END_GRACE_MINUTES` | No (default: 180 / 2) | Cap and grace period for a session's billable minutes. |
| `DATABASE_URL` | No (default: `sqlite+aiosqlite:///./data/englishforge.db`) | SQLAlchemy async URL. Only SQLite is supported — override the file path if needed. |
| `APP_PIN` | No | Optional PIN for extra protection when exposed outside the LAN. Currently only logged at startup when set — no middleware enforces it yet (TBD). |
| `PERSONAL_API_URL` | No | TTS/STT via personal-api. For Coolify deployments, this may be set by the platform. |
| `STT_MODE` | No | STT mode (default: web_speech) |
| `STT_LANGUAGE` | No (default: `en`) | Language hint sent on every personal-api `/v1/transcribe` call. `en` unlocks real Moonshine word timestamps (used by pronunciation fluency metrics); older personal-api servers ignore the field. |
| `TTS_MODEL` | No (default: `english_2026-04_24l`) | Pocket TTS model id pinned on every `/v1/speak` call (personal-api defaults to a Spanish model). Unknown ids are ignored — hot-swap safe. |
| `LLM_TIMEOUT_SECONDS` | No (default: `300`) | Per-request timeout for LLM providers. Reasoning models can take a while before producing output — keep generous, or unset to disable the timeout entirely. |

LLM API keys are configured in the app's Settings UI (encrypted in the DB). The `*_API_KEY` environment variables act as a **server-wide fallback** for users with no provider configured — leave them empty on a shared instance so accounts cannot spend the owner's quota. See the note under [LLM Providers](#llm-providers).

**Note on `.env` for Coolify**: The `docker-compose.yml` has `env_file:.env` marked as `required: false`. When deploying to Coolify, the `.env` file may be provided by the platform's environment configuration, so it's not strictly required in the compose file itself.

## License

Personal project — use freely.