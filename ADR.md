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
