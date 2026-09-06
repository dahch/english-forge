# EnglishForge — App personal de práctica de inglés (estilo Praktika, BYOK, self-hosted)

> Documento de especificación + prompt listo para dárselo a un agente de código (Claude Code, Cursor, etc.) y que la construya de punta a punta.

---

## 1. Resumen del proyecto

Aplicación **personal, self-hosted y gratuita** para practicar y aprender inglés mediante conversación con un tutor IA, al estilo Praktika:

- Conversación por voz con un tutor IA (roleplay de escenarios reales).
- Corrección de gramática, vocabulario y pronunciación en tiempo real.
- Lecciones estructuradas y repaso de vocabulario con repetición espaciada (SRS).
- Progreso, rachas (streaks) y estimación de nivel CEFR (A1–C2).
- **BYOK** (Bring Your Own Key): tú pones las claves de los proveedores LLM que quieras usar.
- **TTS**: tu servidor Pocket en el homelab.
- **STT**: Whisper embebido (whisper.cpp/whisper-wasm) o Web Speech API / STT nativo del móvil, configurable.
- Sin backend de terceros, sin suscripción, sin telemetría — todo corre en tu infraestructura.

**Multi-usuario**: El sistema soporta múltiples usuarios con JWT auth, registro, y login. Cada usuario tiene sus propios ajustes, historial de conversaciones, vocabulario y progreso. El despliegue puede ser single-user o multi-user según necesidad — la arquitectura está diseñada para ambos.

---

## 2. Funcionalidades (paridad Praktika + mejoras)

### 2.1 Núcleo conversacional

- **Chat de voz en tiempo real** con un tutor IA: STT → LLM (persona + motor de corrección) → TTS → reproducción.
- **Escenarios de roleplay** predefinidos (entrevista de trabajo, pedir comida, hacer check-in en hotel, small talk, reunión de trabajo, llamada telefónica, etc.) y **escenarios personalizados** (el usuario describe la situación y el sistema genera el prompt del personaje).
- **Modo "free talk"**: conversación abierta sobre cualquier tema, con corrección activa.
- **Selector de nivel CEFR** (A1–C2) que ajusta vocabulario, velocidad de habla del tutor y tolerancia de corrección.
- **Personas del tutor**: distintos acentos/personalidades (ES: "profesor estricto", "amigo casual", "coach de negocios"), cada una es solo un system prompt distinto.

### 2.2 Motor de corrección

- Tras cada turno del usuario, el LLM devuelve (en JSON estructurado, además de la respuesta conversacional):
  - Transcripción "limpia" de lo que dijo.
  - Errores detectados (gramática, uso de palabras, naturalidad) con explicación breve en español.
  - Sugerencia de frase corregida/mejorada.
  - Nueva(s) palabra(s) o expresión(es) idiomáticas útiles relacionadas con el turno.
- Los errores se muestran como burbujas/inline debajo del mensaje del usuario (no interrumpen la conversación hablada, se acumulan para revisión post-sesión).
- **Resumen de sesión**: al terminar una conversación, pantalla con errores agrupados por categoría, puntuación de fluidez estimada y palabras nuevas para añadir al vocabulario.

### 2.3 Vocabulario y SRS

- Banco de palabras/expresiones personal, alimentado automáticamente desde las conversaciones (o añadido manualmente).
- Algoritmo de repetición espaciada tipo **SM-2** (igual que Anki) para repasos diarios.
- Tarjetas con: palabra, definición, pronunciación (IPA + audio TTS), frase de ejemplo, frase generada por el usuario en su última práctica.
- Modo "quiz" rápido (multiple choice / completar frase / escuchar y escribir).

### 2.4 Lecciones estructuradas

- Generación de mini-lecciones de gramática/vocabulario bajo demanda por el LLM, basadas en los errores recurrentes del usuario ("veo que confundes present perfect vs past simple, aquí tienes una lección corta + 5 ejercicios").
- Biblioteca local de lecciones fijas para temas base (tiempos verbales, phrasal verbs, preposiciones, etc.) como fallback sin necesitar LLM.

### 2.5 Progreso y gamificación

- Dashboard: minutos hablados, palabras nuevas aprendidas, racha de días, nivel CEFR estimado (heurística basada en errores/complejidad de frases), gráfico de evolución.
- Streaks y XP simple (sin presión monetizable, solo motivación personal).
- Metas diarias configurables (ej. "10 min de conversación" o "20 tarjetas de repaso").

### 2.6 Pronunciación

- Puntuación de lectura en voz alta (sección *speaking* del assessment) con métricas objetivas (`app/services/pronunciation.py`): WER de palabras + tasa de error fonémica (PER vía phonemizer/espeak-ng — sin espeak-ng se degrada a solo WER) + fluidez a partir de los word timestamps de Moonshine (compuesto 60/25/15). No hay modelo comercial dedicado de scoring fonético; el PER fonémico sobre la transcripción STT es la aproximación.
- Cada ítem de *speaking* anuncia el punto fonético a practicar ("Focus: …") junto a la frase.

### 2.7 Fuera de alcance (explícitamente simplificado)

- Avatar animado / lipsync: **no es prioridad** (según lo indicado). Se deja un placeholder de avatar estático o un simple indicador de "hablando/escuchando", con arquitectura preparada por si luego se quiere añadir (Live2D / Ready Player Me) sin rehacer nada.
- Multiusuario, pagos, onboarding comercial: no aplica (uso personal).

---

## 3. Arquitectura técnica

```
├─────────────────────────────┐
│   Frontend (Next.js PWA)    │  ← funciona en navegador desktop y móvil (instalable como PWA)
│  - UI conversación/voz      │
│  - Dashboard, SRS, lecciones│
│  - STT: Web Speech API /    │
│    whisper-wasm en cliente  │
└──────────────┬──────────────┘
               │ REST + WebSocket (JWT)
┌──────────────▼──────────────┐
│   Backend (FastAPI, Python) │
│  - Orquestación LLM (BYOK)  │
│  - Motor de corrección      │
│  - SRS engine               │
│  - Progreso / analítica     │
│  - JWT auth, multi-user     │
│  - Proxy hacia Pocket (TTS) │
│  - (Opcional) STT server-side│
│    con faster-whisper       │
└──────────────┬──────────────┘
               │
    ┌───────────┼─────────────────┬───────────────┐
    ▼           ▼                 ▼               ▼
 SQLite     Pocket TTS      Proveedores LLM   Whisper local
 (única;    (tu homelab)    (OpenAI, Anthropic, (opcional,
  datos:                        DeepSeek, Fireworks, faster-whisper
  progreso,                     endpoint custom)    en backend)
  vocab, hist.)
```

- **Despliegue**: Docker Compose de un solo stack (`frontend`, `backend` — no hay servicio `db`; SQLite vive en el volumen `backend_data` montado en `/app/data`), pensado para correr en el mismo homelab que Pocket. En despliegues Coolify, el `env_file:.env` puede ser opcional ya que Coolify provee la configuración ambiental.
- **Multi-usuario**: JWT auth con registro y login. El PIN/password local es opcional para proteger acceso desde fuera de la LAN. Soporta modo single-user o multi-user según necesidad — la arquitectura está preparada para ambos.
- **PWA**: instalar en el móvil como app (icono, offline shell) sin pasar por app stores.

---

## 4. Integraciones LLM — BYOK multi-proveedor

Diseño de **adaptador único OpenAI-compatible** con overrides por proveedor, para que añadir uno nuevo sea solo config, no código:

```yaml
providers:
  openai:
    base_url: https://api.openai.com/v1
    api_key_env: OPENAI_API_KEY
    default_model: gpt-4.1-mini
  anthropic:
    protocol: anthropic          # usa /v1/messages en vez de /v1/chat/completions
    base_url: https://api.anthropic.com
    api_key_env: ANTHROPIC_API_KEY
    default_model: claude-sonnet-4-6
  deepseek:
    base_url: https://api.deepseek.com/v1
    api_key_env: DEEPSEEK_API_KEY
    default_model: deepseek-chat
  fireworks:
    base_url: https://api.fireworks.ai/inference/v1
    api_key_env: FIREWORKS_API_KEY
    default_model: accounts/fireworks/models/llama-v3p1-70b-instruct
  clinepass:                     # nombre genérico: cualquier endpoint OpenAI-compatible adicional
    base_url: ${CLINEPASS_BASE_URL}
    api_key_env: CLINEPASS_API_KEY
    default_model: ${CLINEPASS_MODEL}
  custom:                        # slot libre para cualquier otro proveedor OpenAI-compatible
    base_url: ${CUSTOM_BASE_URL}
    api_key_env: CUSTOM_API_KEY
    default_model: ${CUSTOM_MODEL}
```

> Nota: no tengo certeza de qué API expande exactamente "ClinePass" en tu caso — lo modelo como **otro endpoint OpenAI-compatible configurable por URL/clave/modelo**, igual que "custom". Si en realidad es un proxy/router (tipo OpenRouter), encaja igual en este patrón sin cambios.

- Las claves se guardan **solo en el backend**, vía la pantalla de ajustes cifrada en la base de datos local (nunca se exponen al frontend). El `LLMRouter` lee los proveedores exclusivamente de `provider_configs` — las variables `*_API_KEY` de `.env` no se consumen actualmente (TBD).
- Selector en la UI: qué proveedor/modelo usar por defecto para (a) conversación, (b) corrección, (c) generación de lecciones — pueden ser distintos (ej. modelo barato para corrección estructurada, modelo mejor para roleplay).
- Fallback automático: si el proveedor primario falla (rate limit, error), reintentar con el siguiente configurado.
- Tracking de uso: contador local de tokens/llamadas por proveedor (sin coste real porque BYOK, pero útil para saber consumo).

---

## 5. TTS — Pocket, vía tu "personal-api" existente (homelab)

Con lo que compartiste queda claro que **no hay que hablarle a Pocket TTS directamente**: ya tienes una `personal-api` (FastAPI + Coolify) que expone un endpoint propio y encola el trabajo real en RQ/Redis, ejecutado por los workers `worker-tts-1`/`worker-tts-2`. EnglishForge debe integrarse **contra esa `personal-api`**, no reinventar el acceso a Pocket.

Contrato real observado (`speak.py`, `tasks.py`):

```
POST {PERSONAL_API_URL}/v1/speak
Body: {"text": "...", "voice": "alba"}
→ 200 {"job_id": "...", "status": "queued"}

GET {PERSONAL_API_URL}/v1/jobs/{job_id}
→ {"job_id": "...", "status": "queued|started|finished|failed", "result": {...}}
```

El `result` final, cuando `status == "finished"`, trae `{"audio_base64": "..."}` (o `{"error": "..."}` si Pocket devolvió JSON de error con status 200 — hay que revisarlo, no basta con el status HTTP).

**Adaptador `TTSProvider` para EnglishForge** (`backend/app/integrations/tts_personal_api.py`):
1. `POST /v1/speak` con `{text, voice}` → obtiene `job_id`.
2. Poll a `GET /v1/jobs/{job_id}` cada ~500ms hasta `finished`/`failed`, con timeout total configurable (ej. 30s) — es un patrón **asíncrono por colas**, no una llamada síncrona directa, así que hay que diseñar la UI para mostrar "generando audio…" mientras se espera.
3. Decodificar `audio_base64` → bytes → servir al frontend.

Config:
```
PERSONAL_API_URL=http://personal-api:8000     # nombre de servicio en la red docker "coolify", NO 127.0.0.1:8003
TTS_DEFAULT_VOICE=alba                          # una de las 8 voces builtin sin auth
TTS_JOB_POLL_INTERVAL_MS=500
TTS_JOB_TIMEOUT_SECONDS=30
```

⚠️ **Importante sobre networking**: en tu `docker-compose.yml` de `personal-api`, el puerto se publica como `127.0.0.1:8003:8000` — eso solo es alcanzable desde el host, no desde otro contenedor. Para que el backend de EnglishForge le hable a `personal-api`, el contenedor de EnglishForge tiene que **unirse a la misma red externa `coolify`** y usar el nombre de servicio interno (`http://personal-api:8000`), no el puerto publicado en localhost.

⚠️ **Voces**: para practicar inglés lo lógico es usar voces en inglés, no las voces en español que ya usas para otras cosas. Antes de fijar una voz por defecto, hay que consultar `GET /v1/voices` en Pocket TTS (a través de `personal-api` si expone ese passthrough, o directo si el homelab lo permite) y confirmar el nombre exacto — no asumir que existe una voz concreta.

---

## 6. STT — combinando lo que ya tienes (Moonshine) con opciones sin infraestructura

Tu stack ya incluye un worker STT (`worker-stt`, cola `stt-jobs`) que llama a Moonshine (`MOONSHINE_URL`, endpoint `POST /transcribe` multipart, devuelve `{"text": "..."}`). Eso da un **cuarto modo** disponible además de los que ya habíamos planteado. Los 4 modos, todos intercambiables desde Settings:

1. **Vía tu `personal-api` + Moonshine** (mismo patrón de colas que TTS): el frontend graba el audio, lo manda al backend de EnglishForge, y este reenvía el archivo tal cual a `personal-api`. Contrato real confirmado:
   ```
   POST {PERSONAL_API_URL}/v1/transcribe   multipart/form-data, campo "audio" (archivo)
   → 200 {"job_id": "...", "status": "queued"}

   GET {PERSONAL_API_URL}/v1/jobs/{job_id}
   → {"job_id": "...", "status": "queued|started|finished|failed", "result": {"text": "..."} }
   ```
   `personal-api` es quien codifica el audio a base64 internamente antes de encolarlo — EnglishForge solo tiene que mandar el archivo de audio por multipart, igual que un `<input type="file">`, no un JSON con base64.
   - **Dato de capacidad real de tu homelab** (de los comentarios del propio `worker_stt.py`): Moonshine satura ~6 de 6 cores con solo 5 peticiones concurrentes, por eso `worker-stt` corre con una sola réplica. Esto refuerza la decisión de más abajo: no conviene depender de este modo para el turno conversacional en vivo (además de la latencia del polling, compite por el único worker con cualquier otra transcripción que esté corriendo en el homelab en ese momento).
2. **Nativo del navegador/móvil** (`Web Speech API`): cero infraestructura, gratis, mejor latencia (no depende de colas), calidad variable según navegador.
3. **Whisper embebido en cliente** (`whisper.cpp` WASM, modelo `tiny`/`base`): 100% offline en el navegador, útil si en algún momento no tienes el homelab accesible (fuera de la LAN/VPN).
4. **Whisper server-side directo** (`faster-whisper` en el propio backend de EnglishForge, sin pasar por Moonshine/colas): opción de respaldo si prefieres no depender de RQ para esto.

Recomendación por defecto para una app de **conversación en tiempo real**: **Web Speech API** cuando esté disponible (latencia mínima, la conversación fluye mejor sin esperar polling de colas), con **Moonshine vía `personal-api`** como opción de mayor precisión para cuando quieras revisar/corregir con más cuidado (ej. en el resumen de sesión, donde la latencia importa menos). El modelo de colas (async + polling) es coherente para TTS y para tareas de fondo, pero añade latencia perceptible en un ida-y-vuelta conversacional hablado — por eso no lo pondría como default para el turno de STT en vivo, sino como alternativa configurable.

---

## 7. Modelo de datos

The data model has evolved beyond the initial simplified SQL. The following is the **current full model** (SQLAlchemy ORM definitions in `backend/app/models/models.py`):

```sql
-- users table (extended columns added via startup migration)
users(id, email, hashed_password, display_name, current_level, assessment_completed, created_at)

-- sessions table
sessions(id, user_id, scenario_id, started_at, ended_at, cefr_level, provider_used)

-- messages table
messages(id, session_id, role, text, audio_url, created_at)

-- corrections table
corrections(id, message_id, error_type, original_fragment, correction, explanation)

-- vocab_items table
vocab_items(id, word, definition, example, ipa, ease_factor, interval_days, next_review_at, last_reviewed_at, source_message_id, created_at)

-- scenarios table
scenarios(id, user_id, name, system_prompt, cefr_level, is_custom, created_at)

-- progress_daily table (PK is id; uniqueness per date+user via unique constraint)
progress_daily(id, date, user_id, minutes_spoken, new_words, reviews_done, streak_count, lessons_completed, UNIQUE (date, user_id))

-- settings table (PK is id; uniqueness per key+user via unique constraint)
settings(id, user_id, key, value, UNIQUE (key, user_id))

-- tutor_profiles table
tutor_profiles(id, user_id, name, age, gender, personality, voice, created_at, updated_at)

-- assessments table (no created_at — ordering uses started_at + id)
assessments(id, user_id, started_at, completed_at, estimated_level, confidence, strengths, weaknesses, recommendations, summary, phase, section_step, dimension_scores)

-- assessment_messages table
assessment_messages(id, assessment_id, role, text, kind, audio_url, metrics, created_at)

-- generated_lessons table
generated_lessons(id, user_id, title, topic, level, explanation, examples, exercises, based_on_errors, completed, completed_at, created_at)

-- learning_paths table
learning_paths(id, user_id, assessment_id, current_level, target_level, lessons_required, lessons_completed, created_at, completed_at, is_active)

-- path_lessons table (no created_at; `order` is a reserved word in SQL but quoted by SQLAlchemy)
path_lessons(id, path_id, lesson_type, topic, description, content, order, completed, completed_at)

-- provider_configs table
provider_configs(id, user_id, provider_name, api_key_enc, base_url, model, protocol, is_active, priority, task_routing, created_at, updated_at)
```

> **Nota sobre columnas adicionales**: `current_level` y `assessment_completed` en `users`, y `lessons_completed` en `progress_daily`, son columnas que pueden no estar presentes en bases de datos existentes. El backend incluye un mecanismo de **sincronización al inicio** (`_COLUMNS_TO_ADD` en `backend/app/main.py`) que se ejecuta en cada arranque y añade columnas faltantes con sus valores por defecto mediante `ALTER TABLE`. Esto es seguro porque cada columna solo se añade si está ausente (comprobada por el inspector). Además, se crean índices parciales únicos al inicio (`uq_assessments_user_in_progress` y `uq_learning_paths_user_active`) para garantizar invariantes como "solo un assessment en progreso por usuario" y "solo una learning path activa por usuario".

> **Nota**: `learning_paths.lessons_required` se ajusta (cap) al número real de lecciones devueltas por el LLM, para que el path siempre pueda avanzar aunque el LLM devuelva menos lecciones de las solicitadas.

> **Nota**: `path_lessons.content` se guarda como string JSON en la BD; la API lo parsea a un objeto (`{focus, lesson_type}`) mediante un `field_validator` y devuelve `null` si el JSON es inválido o no es un objeto, para que una fila corrupta no rompa la respuesta completa del path.

---

## 8. Flujo de una sesión de conversación

1. Usuario elige escenario + nivel CEFR (o "free talk").
2. Backend construye el system prompt del tutor (persona + nivel + objetivo del escenario + instrucción de devolver JSON con `reply`, `corrections[]`, `new_vocab[]`).
3. Usuario habla → STT (según modo configurado) → texto.
4. Texto + historial → LLM → respuesta JSON estructurada.
5. `reply` se manda a `personal-api` (`POST /v1/speak`) → se hace poll a `/v1/jobs/{job_id}` hasta tener el audio → se reproduce (la UI muestra un estado breve "generando audio…" mientras espera).
6. `corrections` y `new_vocab` se guardan y se muestran de forma no intrusiva (bubble discreta, sin interrumpir el audio).
7. Al finalizar sesión: resumen, nuevas tarjetas SRS creadas automáticamente, actualización de progreso/racha.
8. **Assessment multisección (v2)**: el assessment ya no es solo chat — es un flujo de fases controlado por el servidor (`assessments.phase`, máquina de estados en `app/services/assessment_flow.py`): `mic_check` (calibración de micrófono con una frase fija) → `conversation` (entrevista conversacional, mínimo 6 intercambios y máximo 10) → `listening` (ítems de comprensión **solo en audio**, texto oculto, con stop adaptativo tras 2 fallos seguidos) → `speaking` (frases para leer en voz alta puntuadas determinísticamente). El LLM **no puede** terminar la entrevista antes del mínimo (`MIN_ASSESSMENT_EXCHANGES`); solo el cierre explícito del usuario (`wants_to_finish()` o el botón *Finish & See Results*) o el tope de 10 la cortan. El `is_complete` transitorio de `AssessmentResponse` ahora significa "todas las secciones terminadas — llamar a `POST /api/assessment/{id}/complete`".
9. **Puntuación determinista**: el nivel final y la confianza **ya no los decide el LLM**. `listening` se puntúa por aciertos en los ítems (grading LLM por ítem con fallback por keywords), `pronunciation` con métricas objetivas por grabación (WER de palabras + PER fonémico vía phonemizer/espeak-ng + fluidez desde word timestamps de Moonshine; compuesto 60/25/15), y `grammar`/`vocabulary`/`fluency` con rúbrica LLM 0-100 sobre la transcripción. La agregación (`app/services/assessment_scoring.py`) mapea cada dimensión a banda CEFR (0-20 A1 … 86-100 C2), toma la mediana conservadora como nivel final y calcula la confianza a partir de cobertura de dimensiones, volumen de evidencia y dispersión. Strengths/weaknesses son etiquetas derivadas de dimensiones medidas — el LLM solo escribe resumen y recomendaciones, y nunca puede afirmar dimensiones sin evidencia.
10. **Re-análisis**: `POST /api/assessment/{id}/reanalyze` re-ejecuta el análisis sobre la misma conversación ya completada (actualiza nivel estimado, fortalezas, debilidades, recomendaciones y resumen). Lleva una guarda anti-abuso por proceso de 30 s por assessment (HTTP 429 si se repite antes de que expire) y devuelve HTTP 503 si la salida del LLM no tiene la forma esperada de un análisis.
11. **Audio del assessment**: los mensajes del tutor se sintetizan on-demand (`GET /api/assessment/{id}/messages/{message_id}/audio`, TTS pocket-tts con caché en `audio_url` — la data URI nunca se serializa en respuestas). Las grabaciones del alumno (`POST /api/assessment/{id}/recordings`) se transcriben por Moonshine vía personal-api (semáforo global de 1 job por saturación de CPU) con fallback a faster-whisper in-process; el audio nunca se persiste — solo transcript + word timestamps. El cliente hace eco de los word timestamps (`words`) y del ítem respondido (`item_id`) en `POST /{id}/message`; el servidor **siempre recalcula** las métricas de pronunciación y descarta envíos duplicados/desactualizados (el cliente jamás inyecta puntuaciones). Los cambios requeridos en personal-api están especificados en `docs/personal-api-changes.md`.

### Ejemplo de contrato JSON que debe devolver el LLM (usado igual en todos los proveedores vía prompt + parsing tolerante):

```json
{
  "reply": "That sounds great! What time do you usually have lunch?",
  "corrections": [
    {
      "error_type": "verb_tense",
      "original": "I go to lunch yesterday",
      "correction": "I went to lunch yesterday",
      "explanation": "Yesterday es pasado, usa 'went' (past simple de 'go')."
    }
  ],
  "new_vocab": [
    {"word": "to grab lunch", "definition": "ir a comer algo rápido", "example": "Let's grab lunch at noon."}
  ]
}
```

---

## 9. Estructura de carpetas propuesta

```
english-forge/
├ docker-compose.yml
├ .env.example
├ frontend/                # Next.js 16 PWA (React 19, Tailwind 4)
│   ├── src/
│   │   ├── app/
│   │   │   ├── (auth)/          # login, register
│   │   │   └── (main)/
│   │   │       ├── assessment/
│   │   │       ├── conversation/
│   │   │       ├── dashboard/
│   │   │       ├── lessons/
│   │   │       ├── learning-path/
│   │   │       ├── settings/
│   │   │       └── vocab/
│   │   ├── components/
│   │   │   ├── layout/          # sidebar
│   │   │   ├── ui/              # badge, button, card, input, select, tabs, textarea, ...
│   │   │   └── service-worker-registrar.tsx
│   │   └── lib/
│   │       ├── api.ts           # cliente REST tipado (auth, sessions, vocab, lessons, assessment, learningPath, dashboard, settings)
│   │       ├── stt/             # web-speech.ts (única implementación STT cliente)
│   │       ├── types.ts
│   │       └── utils.ts
│   ├── public/
│   └── next-env.d.ts
├ backend/
│   ├── app/
│   │   ├── main.py              # lifespan: create_all + _COLUMNS_TO_ADD + _INDEXES_TO_ADD
│   │   ├── config.py            # Settings (pydantic-settings), DATABASE_URL default SQLite
│   │   ├── database.py / security.py / dependencies.py / utils.py
│   │   ├── llm/
│   │   │   ├── base.py              # interfaz ChatProvider
│   │   │   ├── openai_compatible.py
│   │   │   ├── anthropic_adapter.py
│   │   │   ├── router.py            # selección + fallback + parse_llm_json (JSON tolerante)
│   │   │   └── prompts.py           # system prompts + framework CEFR del learning path
│   │   ├── integrations/
│   │   │   ├── tts_personal_api.py    # POST /v1/speak + poll /v1/jobs/{id} contra tu personal-api
│   │   │   ├── stt_personal_api.py    # idem, contra la cola stt-jobs / Moonshine
│   │   │   ├── stt_whisper_server.py  # faster-whisper local, alternativa sin depender de colas
│   │   │   └── crypto.py              # cifrado Fernet de API keys
│   │   ├── services/
│   │   │   ├── assessment_flow.py     # máquina de estados de fases del assessment + análisis
│   │   │   ├── assessment_bank.py     # bancos de ítems listening/speaking
│   │   │   ├── assessment_scoring.py  # agregación determinista CEFR
│   │   │   ├── pronunciation.py       # WER + PER fonémico + fluidez
│   │   │   └── stt.py                 # STT compartido (semáforo global + fallback whisper)
│   │   ├── models/
│   │   │   └── models.py            # User, TutorProfile, Scenario, Session, Message, Correction, VocabItem, Assessment, AssessmentMessage, GeneratedLesson, LearningPath, PathLesson, ProgressDaily, Setting, ProviderConfig
│   │   ├── schemas/                 # auth, session, vocab, settings
│   │   ├── routers/                 # auth, sessions, messages, vocab, scenarios, settings, dashboard, lessons, ws, tutor_profile, assessment, learning_paths
│   │   ├── lessons/                 # biblioteca estática de lecciones (fallback sin LLM)
│   │   └── srs/
│   │       └── sm2.py               # motor de repetición espaciada SM-2
│   ├── alembic/                     # migraciones completas (opcional)
│   ├── tests/                       # pytest: llm router, lecciones, assessment (lógica, flujo de fases, scoring), pronunciación, learning paths
│   └── requirements.txt
└── scripts/
    └── clear_user_data.py
```

---

## 10. Plan de fases

**Phase 1 — MVP funcional** ✅
- Conversación por texto con 1 escenario, 1 proveedor LLM, corrección básica.
- CRUD de vocabulario manual + SRS.

**Phase 2 — Voz completa** ✅
- STT (Web Speech API por defecto, Moonshine vía personal-api como alternativa).
- TTS (Pocket TTS vía personal-api con colas RQ).
- Resumen de sesión con corrections/new_vocab automáticos.

**Phase 3 — Multi-proveedor + ajustes** ✅
- Pantalla de settings BYOK con 5 proveedores configurables (OpenAI, Anthropic, DeepSeek, Fireworks, ClinePass/Custom), fallback, selección de modelo por tarea.

**Phase 4 — Progreso y lecciones** ✅
- Dashboard, streaks, estimación CEFR heurística.
- Generación de mini-lecciones basadas en errores recurrentes (CRUD de lecciones generadas, endpoint `/api/lessons/generate`).
- Learning paths con progression automática.
- Evaluación de ejercicios con respuestas nunca expuestas al frontend (ADR-003/005).

**Phase 5 (opcional, futura)**
- Avatar animado simple (placeholder ya preparado en Fase 2).
- Scoring de pronunciación más fino.
- Migración a PostgreSQL para persistencia a largo plazo (actualmente SQLite en WAL).
- Aplicación móvil real (Capacitor/Expo) para usar STT nativo.

---

## 11. Notas finales

- Con el código que compartiste, **el API de TTS y STT ya no son una suposición**: son los contratos reales de tu `personal-api` (`/v1/speak` y `/v1/transcribe`, ambos con polling en `/v1/jobs/{id}`, colas RQ, Pocket TTS y Moonshine por detrás). El documento y el prompt ya reflejan exactamente eso, incluyendo que `/v1/transcribe` espera multipart (no JSON con base64) y el dato real de que Moonshine satura CPU con una sola réplica.
- Sobre **"ClinePass"** como proveedor LLM sigue sin resolverse — lo mantengo modelado como endpoint OpenAI-compatible genérico configurable por URL/clave/modelo.
- Detalle de networking importante que añadí: `personal-api` publica `127.0.0.1:8003:8000` (solo accesible desde el host), así que el backend de EnglishForge tiene que unirse a la red externa `coolify` y hablarle por el nombre de servicio interno (`http://personal-api:8000`), no por ese puerto publicado.
- Mencionas que hoy usas el TTS "mediante un MCP que mira Hermes, y este MCP a su vez interactúa con personal-api". Para el **backend de la app** (no para mí como agente/asistente) lo natural es que EnglishForge le hable **directo por HTTP a `personal-api`** (como hice arriba) en vez de pasar por ese MCP — el MCP tiene sentido cuando quien consume la herramienta soy yo (un LLM en una conversación), no cuando es tu propio backend de aplicación llamando a otro servicio. Dime si en tu caso es distinto (ej. si personal-api solo es alcanzable a través del MCP por algún motivo de red/auth) y lo ajusto.
- Si luego quieres empaquetar como app móvil real (Capacitor/Expo) para usar STT nativo, o añadir un avatar animado, la arquitectura ya deja los puntos de extensión preparados (interfaces `ChatProvider`, `TTSProvider`, componente de avatar placeholder).