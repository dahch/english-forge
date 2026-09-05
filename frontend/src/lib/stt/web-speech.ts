export interface STTResult {
  text: string | null
  error?: string
}

export interface STTProvider {
  start(): Promise<void>
  stop(): Promise<void>
  onResult(callback: (text: string) => void): void
  onError(callback: (error: string) => void): void
  isAvailable(): boolean
}

export class WebSpeechSTT implements STTProvider {
  private recognition: any = null
  private resultCallback: ((text: string) => void) | null = null
  private errorCallback: ((error: string) => void) | null = null

  isAvailable(): boolean {
    return typeof window !== "undefined" && ("SpeechRecognition" in window || "webkitSpeechRecognition" in window)
  }

  async start(): Promise<void> {
    if (!this.isAvailable()) {
      this.errorCallback?.("Web Speech API not available")
      return
    }

    const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition
    this.recognition = new SpeechRecognition()
    this.recognition.continuous = false
    this.recognition.interimResults = false
    this.recognition.lang = "en-US"

    this.recognition.onresult = (event: any) => {
      const transcript = event.results[0][0].transcript
      this.resultCallback?.(transcript)
    }

    this.recognition.onerror = (event: any) => {
      this.errorCallback?.(event.error || "Speech recognition error")
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
}
