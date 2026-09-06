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
- **Decision**: Centralized parsing in `parse_llm_json()` (`backend/app/llm/router.py`) with a layered fallback: strip code fences (`_strip_code_fences`) → try `json.loads` → repair smart quotes/trailing commas (`_repair_json`) → extract balanced `{...}` blocks respecting string literals (`_extract_balanced_objects`) → regex extraction of the conversational `reply`/`corrections`/`new_vocab` shape. `parse_llm_json` always returns a dict — a top-level JSON array is wrapped as `{"items": [...]}`. A strict variant, `parse_lesson_json()`, raises `ValueError` when no lesson-shaped object is found so lesson generation fails loudly instead of persisting an empty lesson. On top of parsing, the multi-step flows (assessment message, assessment analysis, learning path generation, lesson generation) retry the entire LLM call up to 3 times, logging the raw response between attempts, and surface HTTP 503 only after all attempts fail. Learning path generation additionally validates the parsed shape (lessons list, allowed `lesson_type` values) per attempt and retries on unusable output.
- **Consequences**:
  - Pros: Resilient to real-world LLM output across providers; no silent persistence of broken JSON; failures observable via raw-response logs; single parsing entry point keeps behavior consistent.
  - Cons: The conversational regex fallback can mask genuinely malformed responses (they degrade to plain text); retries add latency and cost on persistent failures; callers must choose correctly between the tolerant (`parse_llm_json`) and strict (`parse_lesson_json`) entry points.