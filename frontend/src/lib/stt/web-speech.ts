export interface STTResult {
  text: string | null
  error?: string
}

export interface STTProvider {
  start(): Promise<void>
  stop(): Promise<void>
  onResult(callback: (text: string) => void): void
  onError(callback: (error: string) => void): void
  onEnd(callback: () => void): void
  isAvailable(): boolean
}

interface SpeechRecognitionResultItem {
  transcript: string
}

interface SpeechRecognitionResultList {
  [index: number]: SpeechRecognitionResultItem[]
}

interface SpeechRecognitionEvent {
  results: SpeechRecognitionResultList
}

interface SpeechRecognitionErrorEvent {
  error?: string
}

interface ISpeechRecognition {
  continuous: boolean
  interimResults: boolean
  lang: string
  onresult: ((event: SpeechRecognitionEvent) => void) | null
  onerror: ((event: SpeechRecognitionErrorEvent) => void) | null
  onend: (() => void) | null
  start(): void
  stop(): void
}

type SpeechRecognitionConstructor = new () => ISpeechRecognition

export class WebSpeechSTT implements STTProvider {
  private recognition: ISpeechRecognition | null = null
  private resultCallback: ((text: string) => void) | null = null
  private errorCallback: ((error: string) => void) | null = null
  private endCallback: (() => void) | null = null

  isAvailable(): boolean {
    return typeof window !== "undefined" && ("SpeechRecognition" in window || "webkitSpeechRecognition" in window)
  }

  async start(): Promise<void> {
    if (!this.isAvailable()) {
      this.errorCallback?.("Web Speech API not available")
      return
    }

    const win = window as unknown as {
      SpeechRecognition?: SpeechRecognitionConstructor
      webkitSpeechRecognition?: SpeechRecognitionConstructor
    }
    const SpeechRecognition = win.SpeechRecognition || win.webkitSpeechRecognition
    if (!SpeechRecognition) {
      this.errorCallback?.("Web Speech API not available")
      return
    }

    this.recognition = new SpeechRecognition()
    this.recognition.continuous = false
    this.recognition.interimResults = false
    this.recognition.lang = "en-US"

    this.recognition.onresult = (event: SpeechRecognitionEvent) => {
      const transcript = event.results[0]?.[0]?.transcript || ""
      this.resultCallback?.(transcript)
    }

    this.recognition.onerror = (event: SpeechRecognitionErrorEvent) => {
      this.errorCallback?.(event.error || "Speech recognition error")
    }

    this.recognition.onend = () => {
      this.endCallback?.()
    }

    this.recognition.start()
  }

  async stop(): Promise<void> {
    this.recognition?.stop()
  }

  onResult(callback: (text: string) => void): void {
    this.resultCallback = callback
  }

  onError(callback: (error: string) => void): void {
    this.errorCallback = callback
  }

  onEnd(callback: () => void): void {
    this.endCallback = callback
  }
}
