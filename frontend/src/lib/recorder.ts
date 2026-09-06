"use client"

// MediaRecorder wrapper for assessment voice answers. Records up to
// `maxSeconds` (hard stop) and returns the raw Blob for upload. Unlike the
// Web Speech API, this produces real audio the backend can score — required
// for pronunciation measurement.

export interface RecorderHandle {
  stop: () => Promise<Blob>
}

export class RecorderError extends Error {
  code: "permission" | "unsupported" | "no-data"

  constructor(code: "permission" | "unsupported" | "no-data", message: string) {
    super(message)
    this.code = code
  }
}

export async function startRecording(
  options: {
    maxSeconds?: number
    onTick?: (elapsedSeconds: number) => void
    onLevel?: (level: number) => void
    // Fired once when recording ends — manually (stop()) or automatically at
    // the hard stop — with the captured blob. Lets the page start the
    // upload/transcribe flow without waiting for another click.
    onStop?: (blob: Blob) => void
  } = {}
): Promise<RecorderHandle> {
  const { maxSeconds = 30, onTick, onLevel, onStop } = options

  if (
    typeof window === "undefined" ||
    !navigator.mediaDevices?.getUserMedia ||
    typeof MediaRecorder === "undefined"
  ) {
    throw new RecorderError("unsupported", "Audio recording is not supported in this browser")
  }

  let stream: MediaStream
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true })
  } catch {
    throw new RecorderError("permission", "Microphone permission denied")
  }

  const mimeCandidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"]
  const mimeType = mimeCandidates.find((m) => MediaRecorder.isTypeSupported(m))
  const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream)

  const chunks: Blob[] = []
  recorder.ondataavailable = (e) => {
    if (e.data.size > 0) chunks.push(e.data)
  }

  // Audio level meter for UI feedback (cosmetic — failures ignored).
  let audioContext: AudioContext | null = null
  if (onLevel) {
    try {
      audioContext = new AudioContext()
      const source = audioContext.createMediaStreamSource(stream)
      const analyser = audioContext.createAnalyser()
      analyser.fftSize = 256
      source.connect(analyser)
      const data = new Uint8Array(analyser.frequencyBinCount)
      const meterLoop = () => {
        if (!audioContext) return
        analyser.getByteFrequencyData(data)
        const avg = data.reduce((a, b) => a + b, 0) / data.length
        onLevel(Math.min(1, avg / 80))
        requestAnimationFrame(meterLoop)
      }
      requestAnimationFrame(meterLoop)
    } catch {
      audioContext = null
    }
  }

  const startedAt = Date.now()
  const tick = onTick
    ? setInterval(() => onTick((Date.now() - startedAt) / 1000), 200)
    : null

  const stopped = new Promise<Blob>((resolve, reject) => {
    recorder.onstop = () => {
      cleanup()
      const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" })
      if (blob.size === 0) reject(new RecorderError("no-data", "No audio captured"))
      else {
        resolve(blob)
        onStop?.(blob)
      }
    }
    recorder.onerror = () => {
      cleanup()
      reject(new RecorderError("no-data", "Recording failed"))
    }
  })

  recorder.start(250)
  const hardStop = setTimeout(() => {
    if (recorder.state !== "inactive") recorder.stop()
  }, maxSeconds * 1000)

  function cleanup() {
    clearTimeout(hardStop)
    if (tick) clearInterval(tick)
    stream.getTracks().forEach((t) => t.stop())
    audioContext?.close().catch(() => {})
    audioContext = null
  }

  return {
    stop: () => {
      if (recorder.state !== "inactive") recorder.stop()
      return stopped
    },
  }
}
