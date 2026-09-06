# Cambios externos requeridos por Assessment v2

El scoring de pronunciación de english-forge necesita **word timestamps** del
STT (Moonshine). La transcripción ya funciona con personal-api tal como está;
sin timestamps el assessment pierde únicamente las métricas de fluidez (pausas
y muletillas). WER y PER funcionan desde el primer día.

**Este documento es el contrato de lo que english-forge espera de los servicios
externos.** El prompt de abajo está listo para ejecutarse en el repo de
`personal-api` (cualquier agente puede tomarlo tal cual).

## Servicios involucrados

| Servicio | Repo | ¿Requiere cambios? |
|---|---|---|
| personal-api | repo propio (Coolify) | **Sí** — exponer word timestamps en `/v1/transcribe` |
| Moonshine | moonshine-ai/moonshine | Solo si la versión instalada no soporta `word_timestamps` (el prompt lo verifica) |
| pocket-tts | kyutai-labs/pocket-tts | **No** — el TTS ya sirve tal cual; no tocar |

## Prompt para ejecutar en personal-api

```text
TAREA: Exponer word timestamps de Moonshine en el endpoint de transcripción.

CONTEXTO
- personal-api orquesta Moonshine (STT) y pocket-tts (TTS) detrás de RQ/Redis.
- english-forge llama POST /v1/transcribe, hace polling de GET /v1/jobs/{id}
  y consume result.text. Ese contrato NO debe romperse.
- english-forge ahora también consume result.words para puntuar pronunciación
  (pausas, muletillas, ritmo). Hoy ese campo no existe.

REQUERIMIENTOS (todos obligatorios, en este orden)

1. Verificar la versión del cliente de Moonshine usada por el worker-stt:
   el método de transcripción debe soportar word-level timestamps
   (en moonshine-python es el parámetro word_timestamps=True de transcribe()).
   Si la versión instalada no lo soporta, actualizar Moonshine a la versión
   mínima que lo soporte, manteniendo: ejecución CPU-only con los ONNX
   runtime, sin cambiar de modelo (misma variante/tamaño que la actual) y
   sin introducir dependencias con GPU. Registrar en el commit qué versión
   se tenía y a cuál se subió.

2. Modificar el worker de STT para que el resultado del job incluya un campo
   nuevo "words": lista de objetos {"word": str, "start": float, "end": float}
   con los tiempos en segundos relativos al inicio del audio. Especificación:
   - result.text sigue devolviendo el transcript completo, intacto.
   - result.words debe cubrir el mismo contenido que text, en orden.
   - Para audio vacío o silencio total: text="" y words=[].
   - Si Moonshine no puede producir timestamps para algún segmento, devolver
     words=[] en lugar de fallar el job (degradación, no error).

3. Compatibilidad: no renombrar ni eliminar campos existentes (job_id,
   status, result.text, result.error). Ningún otro endpoint debe cambiar.

4. Rendimiento: worker-stt sigue siendo UNA sola réplica. No subir réplicas
   ni concurrencia del worker: Moonshine satura ~6 núcleos y el host es un
   Mac mini 2018 de 6 cores que comparte carga con TTS y la app principal.
   Si la versión nueva de Moonshine es más lenta que la anterior en
   transcribir un clip de 30s, documentarlo en el README.

5. Documentación: actualizar el README de personal-api con el campo
   result.words (formato y ejemplo de respuesta JSON) y una nota indicando
   que english-forge lo usa para scoring de pronunciación.

6. Tests: si el repo tiene suite de tests, agregar uno que transcriba un
   WAV corto sintético (p.ej. 1s de tono o TTS local) y valide que
   result.words sea una lista de dicts con las claves word/start/end y que
   text siga presente. Si no hay infra de tests, validar manualmente con un
   curl + job y pegar la respuesta JSON de ejemplo en el README.

DEFINICIÓN DE HECHO
- POST /v1/transcribe → GET /v1/jobs/{id} devuelve result con text Y words.
- Los consumidores actuales (english-forge u otros) siguen funcionando sin
  cambios: campo nuevo es aditivo.
- Desplegado en Coolify y verificado con un audio real de ~10s.
```

## Verificación desde english-forge

Tras desplegar personal-api actualizado:

1. Reproducir cualquier mensaje del tutor en el assessment (botón ▶) — TTS no cambia.
2. Grabar una respuesta en la fase de pronunciación y comprobar que el
   `metrics` devuelto por `POST /api/assessment/{id}/recordings` incluye
   `fluency` con `long_pauses`/`fillers` y `timestamps_used: true`.
   Con personal-api sin actualizar: `timestamps_used: false` y `fluency: null`
   (el compuesto renormaliza — no es un error).

## Qué NO hacer

- No cambiar pocket-tts: english-forge solo llama `/v1/speak` y `/v1/voices`,
  que ya funcionan.
- No exponer el endpoint de transcripción públicamente: debe seguir
  alcanzable solo desde la red interna de Coolify.
