# Architecture Decision Records

## ADR-001: Optional env_file for Coolify Deployments
- **Date**: 2024
- **Status**: Accepted
- **Context**: The docker-compose.yml originally had `env_file: .env` as a required field. When deploying to Coolify, the platform provides environment configuration through its own UI, making the local .env file redundant or conflicting.
- **Decision**: Changed `env_file: .env` to `env_file:` with `required: false` in docker-compose.yml, making the env_file optional. The .env file remains as a reference for local development.
- **Consequences**: 
  - Pros: Deployments to Coolify work without .env conflicts; platform-managed env takes precedence.
  - Cons: Local development still requires .env file; teams must remember to create one for local work.

## ADR-002: Frontend in Repo with Same-Origin API Proxy
- **Date**: 2024
- **Status**: Accepted
- **Context**: The frontend (Next.js) and backend (FastAPI) were initially separate concerns. For simpler deployment and CORS management, the frontend proxies API requests to the backend running in the same Docker network.
- **Decision**: Frontend is tracked in the repo root, with `API_BASE` configured via `NEXT_PUBLIC_API_URL` env var. By default, `/api/*` routes are same-origin within the Docker network.
- **Consequences**:
  - Pros: Simplified CORS (same-origin), single docker-compose, easier development workflow.
  - Cons: Frontend build artifacts included in repo; NEXT_PUBLIC_API_URL must be set for non-docker deployments.

## ADR-003: Sanitized Lesson Answers (Exercise Check Endpoint)
- **Date**: 2024
- **Status**: Accepted
- **Context**: The lesson library contains static lessons with exercise answers. The exercise check endpoint needs to validate user answers without exposing them.
- **Decision**: 
  - Lesson library responses strip `answer` fields from exercises (`_strip_answers` function).
  - Exercise check endpoint (`check_exercise`) computes correctness server-side and returns `correct`/`correct_answer`/`explanation` — the `correct_answer` is the stored answer, visible only to the authenticated user.
  - Generated lessons also strip answers from exercises in listing endpoints.
- **Consequences**:
  - Pros: Answers never leak to the frontend; grading happens server-side; suitable for self-hosted personal use.
  - Cons: Frontend cannot re-use answers for auto-grading; each exercise check requires a round-trip to the backend.

## ADR-004: Multi-User with JWT Auth
- **Date**: 2024
- **Status**: Accepted
- **Context**: The original design was single-user personal tooling. Recent commits added user registration/login, user models, and per-user resources.
- **Decision**: Added JWT auth flow (register/login/me endpoints), user models with `current_level` and `assessment_completed` columns, and made all user-scoped resources (sessions, messages, vocab, generated lessons) require authentication via `get_current_user` dependency.
- **Consequences**:
  - Pros: True multi-user capability; users have independent progress, vocab, and lessons; PIN protection optional for LAN exposure.
  - Cons: Additional database columns/migrations; auth state management in frontend; token expiry handling.

## ADR-005: Generated Lessons Persisted with Answers Stripped
- **Date**: 2024
- **Status**: Accepted
- **Context**: The lesson generation endpoint (`/api/lessons/generate`) calls an LLM to create personalized lessons with exercises.
- **Decision**: Generated lessons are persisted to the database (`GeneratedLesson` model). When listing or checking exercises, answers are stripped from the response (only `correct_answer` is returned via the check endpoint, never the stored answer in list views).
- **Consequences**:
  - Pros: Lessons survive navigation/reloads; answers remain server-side only; consistent with ADR-003 pattern.
  - Cons: Database storage required for each generated lesson; need for cleanup/ttl policy.

## ADR-006: Database Column Migration Sync on Startup
- **Date**: 2024
- **Status**: Accepted
- **Context**: The backend uses SQLAlchemy model definitions that may reference columns not yet present in existing databases (e.g., `current_level`, `assessment_completed` on `users`, `lessons_completed` on `progress_daily`). SQLAlchemy's `create_all` only creates missing tables — it never alters existing ones. Without explicit migration, the app would fail at runtime with column-not-found errors.
- **Decision**: Added a `_COLUMNS_TO_ADD` dict in `backend/app/main.py` mapping table names to `(column_name, column_def)` tuples. A startup event `_ensure_new_columns_sync` inspects the live schema and `ALTER TABLE`-s any missing columns with their declared defaults. This is safe to run on every startup since each column is only added if absent (guarded by inspector check).
- **Consequences**:
  - Pros: Zero-downtime schema upgrades; migrations run automatically on app restart; no manual Alembic revision needed for trivial column additions; safe to run in production.
  - Cons: Does not handle data migration or schema renames; only adds columns with defaults (no complex ALTER logic); if a column definition changes, the sync must be re-evaluated.

## ADR-007: Partial Unique Indexes at Startup for Invariants
- **Date**: 2026-09-06
- **Status**: Accepted
- **Context**: The assessment and learning path subsystems need to enforce that each user has at most one in-progress assessment and at most one active learning path. Previously these were only guarded by application-level checks (SELECT-before-INSERT), which are vulnerable to race conditions from double-clicks or concurrent requests.
- **Decision**: Added `_INDEXES_TO_ADD` list in `backend/app/main.py` containing two partial unique indexes created via `CREATE UNIQUE INDEX IF NOT EXISTS` on every startup:
  - `uq_assessments_user_in_progress` on `assessments (user_id) WHERE completed_at IS NULL` — ensures only one in-progress assessment per user
  - `uq_learning_paths_user_active` on `learning_paths (user_id) WHERE is_active` — ensures only one active learning path per user
  The startup sync wrapper uses `begin_nested()` savepoints so that if a concurrent instance adds the index first, the second instance gracefully skips it rather than failing.
- **Consequences**:
  - Pros: Race-condition-free atomic start-assessment and generate-path operations; idempotent startup; no need for application-level locking beyond what the index provides.
  - Cons: Index creation adds a brief startup latency; index name must be coordinated with application logic; if the index definition changes, the startup sync must be updated.

## ADR-008: Lesson LLM Output Normalization
- **Date**: 2026-09-06
- **Status**: Accepted
- **Context**: The LLM generating lessons can return various JSON irregularities: double-encoded strings, non-list values where lists are expected, exercises missing required keys. The previous code assumed well-formed JSON and would crash or persist broken data.
- **Decision**: Added `normalize_llm_lesson()` function in `backend/app/routers/lessons.py` that coerces LLM output into the expected shapes:
  - Double-encoded JSON strings are parsed back to their original type
  - Non-list values where lists are expected are converted to lists (or wrapped in a single-element list)
  - Exercises missing `question` keys are dropped
  - All exercise `question` values are stripped and validated as non-empty
  - A new `_serialize_generated_lesson()` function strips `answer` fields from exercises (ADR-003/005 contract), keeping only `question`, `type`, `options`, and `explanation` — the `answer` field is only available via the exercise check endpoint.
- **Consequences**:
  - Pros: Robustness against malformed LLM output; never persists unusable lessons; answers never leak to frontend; consistent exercise format across library and generated lessons.
  - Cons: Additional processing layer; some valid LLM variations may still not map perfectly (callers should handle empty exercises gracefully).

## ADR-009: Assessment Early-Finish Detection
- **Date**: 2026-09-06
- **Status**: Accepted
- **Context**: The assessment system needed a reliable way to detect when a student wants to end the assessment early, without false-triggering on common words like "finished" or "done" that appear in normal conversation.
- **Decision**: Replaced simple word-boundary regex (`_END_REQUEST_RE = re.compile(r"\b(finish|end|stop|terminar|done)\b", re.IGNORECASE)`) with a contextual check (`_wants_to_finish()`) that:
  - Strips trailing punctuation (`.,!?;:¡¿`) from the user's text
  - Only triggers on short utterances (≤4 words after stripping), preventing matches on phrases like "I'm done with work for today"
  - Uses a curated set of end-request words: `finish|end|stop|terminar|basta|done`
- **Consequences**:
  - Pros: Greatly reduced false positives from normal conversation; still catches intentional early-finish requests.
  - Cons: Slightly more restrictive; users with very short "basta" or "done" utterances are correctly handled; edge case of "basta" in longer sentences may need future refinement.

## ADR-010: Tolerant LLM JSON Parsing with Retry
- **Date**: 2026-09-06
- **Status**: Accepted
- **Context**: LLMs routinely return JSON wrapped in markdown fences, preceded/followed by prose, with smart quotes or trailing commas, double-encoded strings, or a non-dict top-level shape. Every LLM-backed feature (conversation corrections, assessment messages, assessment analysis, lesson generation, learning path generation) previously assumed well-formed JSON and would crash or persist broken data.
- **Decision**: Centralized parsing in `parse_llm_json()` (`backend/app/llm/router.py`) with a layered fallback: strip code fences (`_strip_code_fences`) → try `json.loads` → repair smart quotes/trailing commas (`_repair_json`) → extract balanced `{...}` blocks respecting string literals (`_extract_balanced_objects`) → regex extraction of the conversational `reply`/`corrections`/`new_vocab` shape. `parse_llm_json` always returns a dict — a top-level JSON array is wrapped as `{"items": [...]}`. A strict variant, `parse_lesson_json()`, raises `ValueError` when no lesson-shaped object is found so lesson generation fails loudly instead of persisting an empty lesson. On top of parsing, the multi-step flows (assessment message, assessment analysis, learning path generation, lesson generation, path-lesson content generation) retry the entire LLM call up to 3 times, logging the raw response between attempts; the content-generating flows (lesson generation, learning path generation, path-lesson content generation) surface HTTP 503 only after all attempts fail. The conversation turn never fails the request: a total LLM outage degrades to a fixed recovery line so the conversation stays alive and the student's next message retries with full history. The LLM router additionally treats HTTP 200 with empty `content` as a provider failure: `reasoning_content` (the model's raw chain-of-thought) is deliberately **never** used as a reply — leaking it is worse than retrying — so the router retries the same provider once with a quadrupled token budget when `finish_reason="length"` (reasoning models can burn the whole budget thinking; any other empty content fails the provider immediately, since more tokens won't help), and only then fails over to the next provider. The assessment analysis goes one step further — after all failed attempts it completes with a deterministic fallback built from the measured evidence instead of erroring (the /complete request must never fail on an LLM outage; "Re-analyze Results" retries the LLM later). Learning path generation additionally validates the parsed shape (lessons list, allowed `lesson_type` values) per attempt and retries on unusable output.
- **Consequences**: 
  - Pros: Resilient to real-world LLM output across providers; no silent persistence of broken JSON; failures observable via raw-response logs; single parsing entry point keeps behavior consistent.
  - Cons: The conversational regex fallback can mask genuinely malformed responses (they degrade to plain text); retries add latency and cost on persistent failures; callers must choose correctly between the tolerant (`parse_llm_json`) and strict (`parse_lesson_json`) entry points.

## ADR-011: SQLite-Only Persistence with WAL Mode
- **Date**: 2026-09-06
- **Status**: Accepted
- **Context**: The stack originally planned a PostgreSQL container alongside the backend. For a self-hosted personal app, the separate database service was pure operational overhead (another container to back up, monitor and upgrade), while the real workload — a handful of users issuing short transactions — is well within SQLite's reach under WAL. Concurrent requests (e.g. a message POST racing TTS-job polling) still needed explicit tuning to avoid "database is locked" errors.
- **Decision**: Standardized on SQLite as the only supported database. `DATABASE_URL` defaults to `sqlite+aiosqlite:///./data/englishforge.db`; the file lives on the `backend_data` volume (`/app/data`) in Docker. At connection time the engine sets `PRAGMA journal_mode=WAL`, `busy_timeout=30000`, `synchronous=NORMAL` and `foreign_keys=ON` (without the last one, the `ondelete` CASCADE/SET NULL rules in `models.py` never fire). `backend/app/database.py` fails fast with a clear error if `DATABASE_URL` points anywhere other than SQLite (asyncpg was removed from requirements). The PostgreSQL service was dropped from Docker Compose; deployments upgrading from the removed stack must migrate their data to SQLite manually.
- **Consequences**:
  - Pros: One less container to operate; backups are a file copy; the startup schema sync (ADR-006/007) targets a single engine; WAL gives concurrent readers with a single writer — sufficient for the target workload.
  - Cons: Single-writer semantics — heavy parallel write bursts serialize through busy_timeout; no server-side users/extensions; running more than one backend instance against the same file is out of scope by design; legacy PostgreSQL deployments need a manual data migration.

## ADR-012: Assessment Phase State Machine as a Service with Evidence Integrity
- **Date**: 2026-09-06
- **Status**: Accepted
- **Context**: The v2 multi-skill assessment (mic_check → conversation → listening → speaking) grew to ~700 lines of phase-transition logic inside the HTTP router, mixing transport concerns with state transitions, item banking, pronunciation scoring and TTS caching — hard to test in isolation. At the same time, the pronunciation flow had no defense against duplicate or stale submissions (double-taps, retries against an already-advanced item), and nothing structurally prevented the client from influencing scores.
- **Decision**: Extracted the entire phase state machine into `backend/app/services/assessment_flow.py`: `handle_message()` dispatches on `assessments.phase` to per-phase handlers (`_handle_mic_check`, `_handle_conversation`, `_handle_listening`, `_handle_speaking`), with helpers for state reloading (`populate_existing` eager loading), item-bank bookkeeping, TTS caching and the completion analysis. The router keeps only HTTP concerns (request validation, auth, recording upload, cooldown guards). Evidence integrity is enforced in the service: pronunciation metrics are **always recomputed server-side** from (item text, transcript, STT word timestamps) — the client merely echoes the words returned by `/recordings`; submissions whose `item_id` no longer matches the current item, or whose item was already answered, are dropped without advancing the section. Legacy (pre-v2) rows default `phase` to `conversation` so they keep behaving as pure chat; new assessments are always created at `mic_check` by `start_assessment`.
- **Consequences**:
  - Pros: Phase transitions are exercisable without HTTP (`test_assessment_flow.py` runs the flow against in-memory SQLite); the client can never inject pronunciation scores; duplicate/stale submissions are idempotent; the router shrinks to transport-only.
  - Cons: An extra indirection layer between router and models; stale-submission dropping trusts the client's `item_id` echo (a client omitting it only degrades its own scoring — the server still recomputes every metric); legacy and new rows have different `phase` defaults, which the startup column sync documents. (The one-answer-per-item invariant is enforced atomically by the partial unique index `uq_assessment_messages_user_item` on `(assessment_id, item_id)` — `item_id` was promoted out of the metrics JSON; `/complete` also rejects an assessment still in `mic_check` so a bare start can't lock in a default level.)

## ADR-013: Pin Pocket TTS Model and STT Language on Every personal-api Call
- **Date**: 2026-09-06
- **Status**: Accepted
- **Context**: The personal-api server defaults to a **Spanish** Pocket TTS model, so english-forge's English tutor audio could silently change voice/model if the server's default drifts. On the STT side, real Moonshine word timestamps (the input to the pronunciation fluency metrics in `pronunciation.py`) are only produced when the transcription language is `en`; Moonshine otherwise defaults to `es` with degraded timestamps. Both new personal-api parameters (`model` on `/v1/speak`, `language` on `/v1/transcribe`) are optional and ignored by older servers.
- **Decision**: english-forge sends the parameters itself on **every** call: `/v1/speak` always carries `model` (`TTS_MODEL`, default `english_2026-04_24l`) and `/v1/transcribe` always carries `language` (`STT_LANGUAGE`, default `en`). Unknown model ids are ignored by Pocket TTS (hot-swap safe). Contract tests in `backend/tests/test_personal_api_payloads.py` pin the wire format; the external contract is documented in `docs/personal-api-changes.md`.
- **Consequences**:
  - Pros: TTS output is stable regardless of personal-api default changes; word timestamps (and therefore fluency scoring) are guaranteed when the personal-api deployment is current; fully backward compatible — stale personal-api servers simply ignore the extra fields.
  - Cons: The pinned model id must match a Pocket TTS model on the server (unknown ids are silently ignored, so a typo degrades quietly rather than erroring); requires an up-to-date personal-api for the STT language behavior to have any effect.

## ADR-014: Interactive Path Lessons with Lazy Content Generation and Server-Side Grading
- **Date**: 2026-09-07
- **Status**: Accepted
- **Context**: Learning-path lessons were created as stubs (`lesson_type`/`topic`/`description` plus `focus` metadata in `path_lessons.content`) with only a "mark complete" button — completing a path step never actually taught anything. Generating full interactive content for every lesson up-front, at path-generation time, would multiply LLM cost and latency for lessons the learner may never open.
- **Decision**: Path-lesson content (explanation, examples, 3–5 fill-blank/multiple-choice exercises) is generated **lazily and idempotently** on first open via `POST /api/learning-paths/{path_id}/lessons/{lesson_id}/generate` — up to 3 LLM attempts, shape-validated through `parse_lesson_json` + `normalize_llm_lesson` (ADR-008/010), then merged into the existing `path_lessons.content` JSON so the focus/lesson_type metadata is preserved. When the path was built from an assessment, the prompt includes the learner's measured weaknesses so the content is personalized. Grading happens through `POST .../lessons/{lesson_id}/exercise` (submission by exercise index; case/trim-insensitive comparison) — exercise answers live only in the stored content JSON and are stripped from every response, extending the ADR-003/005 answer-stripping contract to path lessons. Related async-correctness rule: entities reloaded after core UPDATEs use `populate_existing` on the re-select instead of `db.expire()` — expired attribute access lazy-loads synchronously outside the greenlet and raises MissingGreenlet (production 500s).
- **Consequences**: 
  - Pros: LLM cost is paid only for lessons actually opened; content is personalized to assessment weaknesses; answers never reach the client; the detail view renders instantly from the stub card while content loads in.
  - Cons: The first open of a lesson pays LLM latency (shown as a loading skeleton) and surfaces HTTP 503 if generation fails (retry re-opens the lesson); a lesson can still be marked complete from the path-card shortcut without opening it; callers must never use `db.expire()` on rows that are serialized afterwards.
