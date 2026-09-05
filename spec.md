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
- Comparación fonética aproximada: se usa la confianza/alineamiento de Whisper + comparación de la transcripción esperada vs. obtenida como proxy de pronunciación (no hay modelo dedicado de scoring fonético, se documenta como limitación).
- Opción de "repetir esta frase" con feedback de similitud.

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
 Postgres/    Pocket TTS      Proveedores LLM   Whisper local
 SQLite       (tu homelab)    (OpenAI, Anthropic, (opcional,
  (progreso,                    DeepSeek, Fireworks, faster-whisper
  vocab, hist.)                 endpoint custom)    en backend)
```

- **Despliegue**: Docker Compose de un solo stack (`frontend`, `backend`, `db`), pensado para correr en el mismo homelab que Pocket. En despliegues Coolify, el `env_file:.env` puede ser opcional ya que Coolify provee la configuración ambiental.
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

> Nota: no tengo certeza de qué API expone exactamente "ClinePass" en tu caso — lo modelo como **otro endpoint OpenAI-compatible configurable por URL/clave/modelo**, igual que "custom". Si en realidad es un proxy/router (tipo OpenRouter), encaja igual en este patrón sin cambios.

- Las claves se guardan **solo en el backend**, vía `.env` o pantalla de ajustes cifrada en la base de datos local (nunca se exponen al frontend).
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

-- progress_daily table
progress_daily(date, user_id, minutes_spoken, new_words, reviews_done, streak_count, lessons_completed, PRIMARY KEY (date, user_id))

-- settings table
settings(id, user_id, key, value, PRIMARY KEY (key, user_id))

-- tutor_profiles table
tutor_profiles(id, user_id, name, age, gender, personality, voice, created_at, updated_at)

-- assessments table
assessments(id, user_id, started_at, completed_at, estimated_level, confidence, strengths, weaknesses, recommendations, summary, created_at)

-- assessment_messages table
assessment_messages(id, assessment_id, role, text, created_at)

-- generated_lessons table
generated_lessons(id, user_id, title, topic, level, explanation, examples, exercises, based_on_errors, completed, completed_at, created_at)

-- learning_paths table
learning_paths(id, user_id, assessment_id, current_level, target_level, lessons_required, lessons_completed, created_at, completed_at, is_active)

  - **lessons_required** is capped to the actual number of lessons returned by the LLM
    (per ADR-005 and commit 5f860ef), so the path can advance even if the LLM
    returns fewer lessons than requested.

-- path_lessons table
path_lessons(id, path_id, lesson_type, topic, description, content, order, completed, completed_at, created_at)

-- provider_configs table
provider_configs(id, user_id, provider_name, api_key_enc, base_url, model, protocol, is_active, priority, task_routing, created_at, updated_at)
```

---

## 8. Flujo de una sesión de conversación

1. Usuario elige escenario + nivel CEFR (o "free talk").
2. Backend construye el system prompt del tutor (persona + nivel + objetivo del escenario + instrucción de devolver JSON con `reply`, `corrections[]`, `new_vocab[]`).
3. Usuario habla → STT (según modo configurado) → texto.
4. Texto + historial → LLM → respuesta JSON estructurada.
5. `reply` se manda a `personal-api` (`POST /v1/speak`) → se hace poll a `/v1/jobs/{job_id}` hasta tener el audio → se reproduce (la UI muestra un estado breve "generando audio…" mientras espera).
6. `corrections` y `new_vocab` se guardan y se muestran de forma no intrusiva (bubble discreta, sin interrumpir el audio).
7. Al finalizar sesión: resumen, nuevas tarjetas SRS creadas automáticamente, actualización de progreso/racha.
8. **Señal `is_complete`**: tras el último mensaje del usuario, si el LLM incluye `is_complete: true` en la respuesta JSON, el cliente asume que la fase de evaluación terminó y debe llamar a `POST /api/assessment/{id}/complete`. El campo `is_complete` se añade transitoriamente a `AssessmentResponse` (por defecto `False` para endpoints que no lo computes) y se propaga desde la respuesta del LLM (commit 005d371, 5c12cf2).

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
├── docker-compose.yml
├── .env.example
├── frontend/                # Next.js PWA
│   ├── app/
│   │   ├── (auth)/          # login, register
│   │   ├── (main)/
│   │   │   ├── assessment/
│   │   │   ├── conversation/
│   │   │   ├── dashboard/
│   │   │   ├── lessons/
│   │   │   ├── learning-path/
│   │   │   └── settings/
│   │   ├── lib/
│   │   │   ├── api.ts
│   │   │   ├── stt/
│   │   │   └── types.ts
│   │   └── components/
│   │       ├── layout/
│   │       │   └── sidebar.tsx
│   │       ├── ui/
│   │       │   ├── badge.tsx
│   │       │   ├── progress.tsx
│   │       │   ├── skeleton.tsx
│   │       │   └── ...
│   │       └── conversation/
│   │       ├── dashboard/
│   │       ├── lessons/
│   │       ├── settings/
│   │       └── vocab/
│   ├── public/
│   └── next-env.d.ts
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── llm/
│   │   │   ├── base.py              # interfaz ChatProvider
│   │   │   ├── openai_compatible.py
│   │   │   ├── anthropic.py
│   │   │   └── router.py            # selección + fallback
│   │   ├── integrations/
│   │   │   ├── tts_personal_api.py    # POST /v1/speak + poll /v1/jobs/{id} contra tu personal-api
│   │   │   ├── stt_personal_api.py    # idem, contra la cola stt-jobs / Moonshine
│   │   │   └── stt_whisper_server.py  # faster-whisper local, alternativa sin depender de colas
│   │   ├── models/                  # SQLAlchemy
│   │   │   ├── models.py            # User, TutorProfile, Scenario, Session, Message, Correction, VocabItem, Assessment, GeneratedLesson, LearningPath, PathLesson, ProgressDaily, Setting, ProviderConfig
│   │   ├── routers/                 # sessions, vocab, lessons, messages, assessment, ws, settings, tutor_profile, scenarios, dashboard, learning_paths
│   │   └── prompts/                 # templates de system prompts por persona/escenario
│   └── requirements.txt
└── README.md
```

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
- Migración a PostgreSQL para persistencia a largo plazo.
- Aplicación móvil real (Capacitor/Expo) para usar STT nativo.

---

## 11. Notas finales

- Con el código que compartiste, **el API de TTS y STT ya no son una suposición**: son los contratos reales de tu `personal-api` (`/v1/speak` y `/v1/transcribe`, ambos con polling en `/v1/jobs/{id}`, colas RQ, Pocket TTS y Moonshine por detrás). El documento y el prompt ya reflejan exactamente eso, incluyendo que `/v1/transcribe` espera multipart (no JSON con base64) y el dato real de que Moonshine satura CPU con una sola réplica.
- Sobre **"ClinePass"** como proveedor LLM sigue sin resolverse — lo mantengo modelado como endpoint OpenAI-compatible genérico configurable por URL/clave/modelo.
- Detalle de networking importante que añadí: `personal-api` publica `127.0.0.1:8003:8000` (solo accesible desde el host), así que el backend de EnglishForge tiene que unirse a la red externa `coolify` y hablarle por el nombre de servicio interno (`http://personal-api:8000`), no por ese puerto publicado.
- Mencionas que hoy usas el TTS "mediante un MCP que mira Hermes, y este MCP a su vez interactúa con personal-api". Para el **backend de la app** (no para mí como agente/asistente) lo natural es que EnglishForge le hable **directo por HTTP a `personal-api`** (como hice arriba) en vez de pasar por ese MCP — el MCP tiene sentido cuando quien consume la herramienta soy yo (un LLM en una conversación), no cuando es tu propio backend de aplicación llamando a otro servicio. Dime si en tu caso es distinto (ej. si personal-api solo es alcanzable a través del MCP por algún motivo de red/auth) y lo ajusto.
- Si luego quieres migrar a Postgres, empaquetar como app móvil real (Capacitor/Expo) para usar STT nativo, o añadir un avatar animado, la arquitectura ya deja los puntos de extensión preparados (interfaces `ChatProvider`, `TTSProvider`, componente de avatar placeholder).
