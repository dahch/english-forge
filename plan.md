# Plan: EnglishForge — Full Functionality & Elimination of Facades

## Iteration 1 — Learning Path Architecture, Multi-Type Lessons & Generation Resilience
modules: backend/app/routers/learning_paths.py, backend/app/llm/prompts.py, frontend/src/app/(main)/learning-path/page.tsx, frontend/src/lib/api.ts

Refactor the learning path generation and lesson interaction to eliminate timeouts, empty stubs, and the mock "Complete" button:
1. In `backend/app/routers/learning_paths.py` and `backend/app/llm/prompts.py`, ensure path generation does not time out: generate curriculum plans with bounded token budgets and immediate structured fallbacks.
2. In `generate_path_lesson`, implement rich, specialized content generation tailored to each lesson type:
   - `conversation`: scenario roleplay context, target expressions, discussion objectives, and launchable session configuration.
   - `vocabulary`: target words with definitions, phonetic transcription (IPA), context sentences, and exportable items.
   - `grammar`: rule explanations, formulas, common pitfalls, and graded fill_blank/multiple_choice exercises.
   - `listening`: listening passage text for audio synthesis, comprehension questions, and transcript reveal.
   - `reading`/`writing`: short passage with reading comprehension questions and a writing prompt.
3. In `frontend/src/app/(main)/learning-path/page.tsx`:
   - Replace the card's shortcut "Complete" button with "Start Lesson / Open" (or "Review" if completed).
   - Render tailored UI according to the lesson type:
     - For `conversation`: Show context, target phrases, and a prominent "Start Conversation with Tutor" button that navigates directly to `/conversation` with this scenario loaded! When the session finishes, mark the lesson complete.
     - For `vocabulary`: Display word cards with audio pronunciation button and an "Add All to My Vocab (SRS)" button that persists them via `/api/vocab`.
     - For `listening`: Add an audio playback player and a "Show Transcript" toggle.
     - For `grammar`: Render interactive exercises with explanations.
   - "Mark Complete" is enabled once the learner has completed the interactive activity or exercises, persisting progress atomically.
   - Add endpoint `POST /api/learning-paths/{path_id}/lessons/{lesson_id}/start-conversation` or scenario link.

## Iteration 2 — Full SRS Spaced Repetition Flashcard Mode & Pronunciation Audio
modules: frontend/src/app/(main)/vocab/page.tsx, backend/app/routers/vocab.py, frontend/src/lib/api.ts

Transform the vocabulary page from a static table/quiz into a real Anki-style Spaced Repetition System:
1. In `frontend/src/app/(main)/vocab/page.tsx`, create a dedicated "Flashcard Review Mode" alongside the list and quiz:
   - Fetches cards that are due via `/api/vocab/review/due`.
   - Card Front: Word/expression + TTS Audio Listen button.
   - "Show Answer" button revealing Back: definition, IPA, context sentence, and user notes.
   - 4 Anki-style response rating buttons:
     - "Again" (Quality 0: reset interval)
     - "Hard" (Quality 2: small interval increase)
     - "Good" (Quality 4: standard SM-2 interval increase)
     - "Easy" (Quality 5: bonus interval increase)
   - Submits directly to `POST /api/vocab/{id}/review` with the corresponding quality score, updating next review date and daily stats.
2. Add a TTS audio pronunciation button on every card in the vocabulary list, calling a lightweight audio endpoint or using browser speech synthesis as immediate fallback.
3. Add a quick filter / review count badge on the Dashboard and Vocab page showing cards due today.

## Iteration 3 — Proactive Tutor Greetings, Universal Voice STT & Custom Scenarios
modules: backend/app/routers/sessions.py, backend/app/routers/scenarios.py, frontend/src/app/(main)/conversation/page.tsx, frontend/src/lib/api.ts

Elevate the conversational roleplay experience to match Praktika parity:
1. In `backend/app/routers/sessions.py`, upon session creation with a scenario (predefined or custom):
   - Automatically generate or retrieve the tutor's opening message in character (e.g. "Hello! Welcome to the Grand Hotel, how can I help you today?").
   - Synthesize the TTS audio for this greeting and persist it as the first message of the session.
   - When the user enters the chat, the tutor has already spoken and the audio plays automatically, creating an engaging real-world dialogue.
2. In `frontend/src/app/(main)/conversation/page.tsx`:
   - Add universal voice input: integrate `startRecording()` from `@/lib/recorder` so the microphone records an audio blob and sends it to the backend STT endpoint (Moonshine / faster-whisper) when Web Speech API is not supported or when the user prefers high-accuracy audio.
   - Add a modal / form to "Create Custom Scenario" (`POST /api/scenarios`), allowing users to describe any real-world situation and practice immediately.
   - Display scenario objective and target phrases in a collapsible header during conversation.

## Iteration 4 — Lessons Hub: On-Demand Generation by Topic/Level & Progress Tracking
modules: backend/app/routers/lessons.py, frontend/src/app/(main)/lessons/page.tsx, backend/app/lessons/data/

Remove artificial restrictions on lesson generation and expand the static curriculum:
1. In `backend/app/routers/lessons.py`:
   - Update `POST /api/lessons/generate` to accept an optional `topic` (string) and `level` (CEFR). If no prior conversation errors exist, generate the lesson based on the requested topic or level fundamentals rather than rejecting the request with HTTP 400!
   - Add completion tracking for library lessons (`user_library_progress` or user progress record) so static lessons can be marked completed and count toward daily goals.
2. In `frontend/src/app/(main)/lessons/page.tsx`:
   - Add a "Generate Lesson on Any Topic" dialog where learners can enter a topic (e.g. "Job Interview phrasal verbs", "Past continuous vs simple", "At the airport") or pick their CEFR level.
   - Add completion badges and "Mark Complete" for library lessons.
   - Connect active learning path lessons in a dedicated tab on the Lessons page so learners can access their path curriculum from both navigation routes.

## Iteration 5 — System Robustness: LLM Router Fallback, Facade Removal & Verification
modules: backend/app/llm/router.py, backend/app/routers/ws.py, frontend/src/app/(main)/settings/page.tsx

Clean up broken facades and ensure bulletproof system execution:
1. In `backend/app/llm/router.py`:
   - Add environment variable fallback: if no `ProviderConfig` exists in the database for the user, automatically fall back to `.env` keys (`FIREWORKS_API_KEY`, `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, `ANTHROPIC_API_KEY`) so out-of-the-box homelab setups work seamlessly without manual setup.
2. In `backend/app/routers/ws.py`:
   - Fix the database session leak where `db` is closed before turns are processed.
3. In `frontend/src/app/(main)/settings/page.tsx`:
   - Remove the non-existent `whisper_wasm` option from STT Mode dropdown.
   - Reflect real supported STT modes: Web Speech API (Browser native) and Homelab STT (Moonshine / Faster-Whisper server-side).
4. Run comprehensive end-to-end checks across all modules: verify path generation, interactive lessons, SRS flashcard reviews, conversation audio, and dashboard statistics.
