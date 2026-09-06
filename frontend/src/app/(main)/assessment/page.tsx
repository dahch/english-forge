"use client"

import { useState, useEffect, useRef } from "react"
import { useRouter } from "next/navigation"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Progress } from "@/components/ui/progress"
import { ScrollArea } from "@/components/ui/scroll-area"
import { AudioButton } from "@/components/assessment/audio-button"
import { api } from "@/lib/api"
import { startRecording, type RecorderHandle } from "@/lib/recorder"
import type { Assessment, AssessmentMessage, DimensionScores } from "@/lib/types"
import { Mic, MicOff, Send, Sparkles, AlertCircle, Loader2, CheckCircle2, RefreshCw, Ear, BookOpen } from "lucide-react"

// Must match backend/app/routers/assessment.py.
const MAX_ASSESSMENT_QUESTIONS = 10
const MIN_ASSESSMENT_EXCHANGES = 6

const PHASE_LABELS: Record<string, string> = {
  mic_check: "Mic check",
  conversation: `Conversation (min ${MIN_ASSESSMENT_EXCHANGES}, max ${MAX_ASSESSMENT_QUESTIONS})`,
  listening: "Listening — audio only",
  speaking: "Pronunciation — read aloud",
}

const DIMENSION_LABELS: Record<string, string> = {
  grammar: "Grammar",
  vocabulary: "Vocabulary",
  fluency: "Fluency",
  listening: "Listening",
  pronunciation: "Pronunciation",
}

// A single chat bubble. Extracted as a component so the "reveal listening
// item text" state is per-message (hooks can't live inside a .map callback).
function MessageBubble({
  msg,
  index,
  lastAnsweredIndex,
  assessmentId,
}: {
  msg: AssessmentMessage
  index: number
  lastAnsweredIndex: number
  assessmentId: string
}) {
  const isUser = msg.role === "user"
  const isListeningItem = !isUser && msg.kind === "listening"

  // Audio-only items stay hidden until the student has answered (any later
  // user message exists). A manual reveal is the accessibility fallback.
  const [revealed, setRevealed] = useState(!isListeningItem)
  const hidden = isListeningItem && index > lastAnsweredIndex && !revealed

  if (isUser) {
    const source = (msg.metrics as { source?: string } | null)?.source
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-2xl px-4 py-2 bg-primary text-primary-foreground">
          {source === "voice" && (
            <span className="mr-2 inline-flex items-center text-xs opacity-70">
              <Mic className="h-3 w-3 mr-1 inline" />
              voice
            </span>
          )}
          <p className="text-sm whitespace-pre-wrap inline">{msg.text}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex justify-start">
      <div
        className={`max-w-[80%] rounded-2xl px-4 py-2 ${
          isListeningItem ? "bg-primary/10 border border-primary/40" : "bg-secondary"
        }`}
      >
        {hidden ? (
          <div className="flex items-center gap-2">
            <Ear className="h-4 w-4 text-primary shrink-0" />
            <span className="text-sm text-muted-foreground italic">
              Audio-only question — press play and answer with your voice
            </span>
            <Button variant="ghost" size="sm" className="h-6 text-xs" onClick={() => setRevealed(true)}>
              Show text
            </Button>
          </div>
        ) : (
          <p className="text-sm whitespace-pre-wrap">{msg.text}</p>
        )}
        {(msg.kind === "chat" || isListeningItem || msg.kind === "mic_check") && (
          <div className="flex items-center gap-1 mt-1">
            <AudioButton assessmentId={assessmentId} messageId={msg.id} disabled={hidden} />
          </div>
        )}
      </div>
    </div>
  )
}

export default function AssessmentPage() {
  const router = useRouter()
  const [assessment, setAssessment] = useState<Assessment | null>(null)
  const [messages, setMessages] = useState<AssessmentMessage[]>([])
  const [inputText, setInputText] = useState("")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")
  const [completed, setCompleted] = useState(false)
  const [generatingPath, setGeneratingPath] = useState(false)
  const [reanalyzing, setReanalyzing] = useState(false)

  // Voice recording state (MediaRecorder → /recordings → editable transcript)
  const [isRecording, setIsRecording] = useState(false)
  const [recordElapsed, setRecordElapsed] = useState(0)
  const [transcribing, setTranscribing] = useState(false)
  const recorderRef = useRef<RecorderHandle | null>(null)
  // STT word timestamps from the last recording — echoed to /message so the
  // server can recompute pronunciation metrics deterministically. Scores are
  // never accepted from the client.
  const pendingWordsRef = useRef<{ word: string; start: number; end: number }[] | null>(null)
  const lastSourceRef = useRef<"text" | "voice">("text")

  const messagesEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    return () => {
      recorderRef.current?.stop().catch(() => {})
    }
  }, [])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages])

  useEffect(() => {
    async function resume() {
      try {
        const a = await api.assessment.current()
        setAssessment(a)
        setMessages(a.messages)
        if (a.completed_at) setCompleted(true)
      } catch {
        // No assessment found, user will start manually
      }
    }
    resume()
  }, [])

  const applyResponse = (a: Assessment) => {
    setAssessment(a)
    setMessages(a.messages)
    return a
  }

  const start = async () => {
    setLoading(true)
    setError("")
    try {
      applyResponse(await api.assessment.start())
      setCompleted(false)
      setInputText("")
      pendingWordsRef.current = null
      lastSourceRef.current = "text"
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to start assessment")
    } finally {
      setLoading(false)
    }
  }

  const completeAssessment = async (id: string) => {
    setLoading(true)
    try {
      applyResponse(await api.assessment.complete(id))
      setCompleted(true)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to complete assessment")
    } finally {
      setLoading(false)
    }
  }

  const sendMessage = async () => {
    if (!assessment || !inputText.trim() || loading) return
    const text = inputText.trim()
    const source = lastSourceRef.current
    const words = pendingWordsRef.current
    // The banked item this answer addresses — the last pending assistant
    // item message (listening/speaking) carries its item_id in metrics.
    const pendingItem = [...messages]
      .reverse()
      .find((m) => m.role === "assistant" && (m.kind === "listening" || m.kind === "speaking"))
    const itemId =
      pendingItem && typeof pendingItem.metrics?.item_id === "string"
        ? pendingItem.metrics.item_id
        : null
    setInputText("")
    pendingWordsRef.current = null
    lastSourceRef.current = "text"
    setLoading(true)
    setError("")

    try {
      const a = applyResponse(await api.assessment.send(assessment.id, text, source, words, itemId))
      if (a.is_complete && !a.completed_at) {
        await completeAssessment(a.id)
      }
    } catch (err: unknown) {
      setInputText(text)
      setError(err instanceof Error ? err.message : "Failed to send message")
    } finally {
      setLoading(false)
    }
  }

  // Shared by manual stop and the 30s auto-stop: upload → STT → editable
  // transcript. Words are kept to echo back to /message.
  const uploadBlob = async (blob: Blob) => {
    if (!assessment) return
    setTranscribing(true)
    setError("")
    try {
      const result = await api.assessment.uploadRecording(assessment.id, blob)
      pendingWordsRef.current = result.words
      lastSourceRef.current = "voice"
      setInputText((prev) => (prev.trim() ? prev : result.transcript))
      if (!result.transcript.trim()) {
        setError("We couldn't hear anything — check your microphone or type your answer.")
      }
    } catch (err: unknown) {
      setError(
        err instanceof Error && err.message === "Microphone permission denied"
          ? "Microphone permission denied. You can type your answer instead."
          : "Transcription failed — you can type your answer instead."
      )
    } finally {
      setTranscribing(false)
      setRecordElapsed(0)
    }
  }

  const toggleRecording = async () => {
    if (isRecording) {
      const handle = recorderRef.current
      recorderRef.current = null
      setIsRecording(false)
      if (!handle || !assessment) return
      try {
        await uploadBlob(await handle.stop())
      } catch {
        // uploadBlob already surfaces the error; recorder errors (no-data)
        // fall through to a generic message.
        setError("Recording failed — you can type your answer instead.")
        setRecordElapsed(0)
      }
      return
    }

    setError("")
    try {
      recorderRef.current = await startRecording({
        maxSeconds: 30,
        onTick: setRecordElapsed,
        onStop: (blob) => {
          recorderRef.current = null
          setIsRecording(false)
          void uploadBlob(blob)
        },
      })
      setIsRecording(true)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not start recording")
    }
  }

  const generatePath = async () => {
    if (!assessment) return
    setGeneratingPath(true)
    setError("")
    try {
      await api.learningPath.generate(assessment.id)
      router.push("/learning-path")
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to generate learning path")
    } finally {
      setGeneratingPath(false)
    }
  }

  const reanalyzeAssessment = async () => {
    if (!assessment) return
    setReanalyzing(true)
    setError("")
    try {
      applyResponse(await api.assessment.reanalyze(assessment.id))
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to re-analyze assessment")
    } finally {
      setReanalyzing(false)
    }
  }

  const phase = assessment?.phase ?? "conversation"
  const assistantCount = messages.filter((m) => m.role === "assistant" && m.kind === "chat").length
  const lastAnsweredIndex = (() => {
    // Index of the last user message — listening item texts before it can be revealed.
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === "user") return i
    }
    return -1
  })()

  if (completed && assessment) {
    const dims: [string, number][] = Object.entries(
      (assessment.dimension_scores ?? {}) as DimensionScores
    ).filter(([, v]) => typeof v === "number") as [string, number][]
    return (
      <div className="max-w-2xl mx-auto p-6 space-y-6">
        <div className="text-center space-y-2">
          <CheckCircle2 className="h-16 w-16 text-green-400 mx-auto" />
          <h1 className="text-3xl font-bold">Assessment Complete</h1>
          <p className="text-muted-foreground">Your estimated level is</p>
          <div className="text-6xl font-bold text-primary">{assessment.estimated_level}</div>
          <p className="text-sm text-muted-foreground">Confidence: {Math.round((assessment.confidence || 0) * 100)}%</p>
        </div>

        {error && (
          <div className="flex items-center gap-2 p-3 rounded-md bg-destructive/10 text-destructive text-sm">
            <AlertCircle className="h-4 w-4 shrink-0" />
            <span className="flex-1">{error}</span>
            <Button variant="ghost" size="sm" onClick={() => setError("")}>Dismiss</Button>
          </div>
        )}

        {dims.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle>Skill breakdown</CardTitle>
              <CardDescription>Measured from the conversation, listening items and spoken recordings.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {dims.map(([dim, score]) => (
                <div key={dim}>
                  <div className="flex justify-between text-sm mb-1">
                    <span>{DIMENSION_LABELS[dim] ?? dim}</span>
                    <span className="text-muted-foreground">{Math.round(score)}/100</span>
                  </div>
                  <Progress value={score} />
                </div>
              ))}
            </CardContent>
          </Card>
        )}

        <Card>
          <CardHeader>
            <CardTitle>Summary</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm">{assessment.summary}</p>
          </CardContent>
        </Card>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-lg text-green-400">Strengths</CardTitle>
            </CardHeader>
            <CardContent>
              <ul className="list-disc list-inside text-sm">
                {assessment.strengths?.map((s, i) => <li key={i}>{DIMENSION_LABELS[s] ?? s}</li>) || <li className="text-muted-foreground">None detected</li>}
              </ul>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle className="text-lg text-destructive">Weaknesses</CardTitle>
            </CardHeader>
            <CardContent>
              <ul className="list-disc list-inside text-sm">
                {assessment.weaknesses?.map((w, i) => <li key={i}>{DIMENSION_LABELS[w] ?? w}</li>) || <li className="text-muted-foreground">None detected</li>}
              </ul>
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Recommended Focus</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="list-disc list-inside text-sm space-y-1">
              {assessment.recommendations?.map((r, i) => <li key={i}>{r}</li>) || <li className="text-muted-foreground">No recommendations</li>}
            </ul>
          </CardContent>
        </Card>

        <Button onClick={generatePath} disabled={generatingPath || reanalyzing} className="w-full" size="lg">
          {generatingPath ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Sparkles className="h-4 w-4 mr-1" />}
          {generatingPath ? "Generating path..." : "Generate My Learning Path"}
        </Button>

        <Button onClick={reanalyzeAssessment} disabled={generatingPath || reanalyzing} variant="outline" className="w-full">
          {reanalyzing ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <RefreshCw className="h-4 w-4 mr-1" />}
          {reanalyzing ? "Re-analyzing conversation..." : "Re-analyze Results"}
        </Button>

        <Button onClick={start} variant="outline" className="w-full">
          <Mic className="h-4 w-4 mr-1" />
          Retake Assessment
        </Button>
      </div>
    )
  }

  if (!assessment) {
    return (
      <div className="max-w-2xl mx-auto p-6 space-y-6">
        <div className="text-center space-y-4">
          <Sparkles className="h-12 w-12 text-primary mx-auto" />
          <h1 className="text-2xl font-bold">Initial Assessment</h1>
          <p className="text-muted-foreground">
            A ~10 minute voice conversation with your tutor: an interview, audio-only
            listening questions and read-aloud pronunciation items. We&apos;ll measure
            your real level (A1-C2) and build a personalized learning path.
          </p>
          <p className="text-sm text-muted-foreground flex items-center justify-center gap-2">
            <Mic className="h-4 w-4" /> Microphone recommended — typing works as a fallback.
          </p>
          <Button onClick={start} size="lg" disabled={loading}>
            {loading ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Mic className="h-4 w-4 mr-1" />}
            {loading ? "Starting..." : "Start Assessment"}
          </Button>
          {error && (
            <div className="flex items-center gap-2 p-3 rounded-md bg-destructive/10 text-destructive text-sm">
              <AlertCircle className="h-4 w-4" />
              {error}
            </div>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-[calc(100vh-0px)] md:h-screen">
      <div className="flex items-center justify-between p-4 border-b">
        <div className="flex items-center gap-2">
          <h1 className="font-semibold">Assessment</h1>
          <Badge variant="secondary">{PHASE_LABELS[phase] ?? phase}</Badge>
          {phase === "conversation" && (
            <Badge variant="outline">{Math.min(assistantCount, MAX_ASSESSMENT_QUESTIONS)} / {MAX_ASSESSMENT_QUESTIONS}</Badge>
          )}
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => completeAssessment(assessment.id)}
          disabled={loading}
          title={
            phase === "conversation" && assistantCount < MIN_ASSESSMENT_EXCHANGES
              ? `At least ${MIN_ASSESSMENT_EXCHANGES} exchanges are recommended for a reliable level — finishing now lowers the confidence.`
              : undefined
          }
        >
          Finish & See Results
        </Button>
      </div>

      {error && (
        <div className="flex items-center gap-2 p-3 rounded-md bg-destructive/10 text-destructive text-sm mx-4 mt-2">
          <AlertCircle className="h-4 w-4 shrink-0" />
          <span className="flex-1">{error}</span>
          <Button variant="ghost" size="sm" onClick={() => setError("")}>Dismiss</Button>
        </div>
      )}

      <ScrollArea className="flex-1 p-4">
        <div className="max-w-2xl mx-auto space-y-4">
          {messages.map((msg, i) => (
            <MessageBubble
              key={msg.id}
              msg={msg}
              index={i}
              lastAnsweredIndex={lastAnsweredIndex}
              assessmentId={assessment.id}
            />
          ))}
          {(loading || transcribing) && (
            <div className="flex justify-start">
              <div className="bg-secondary rounded-2xl px-4 py-2">
                <div className="flex gap-1">
                  <div className="w-2 h-2 bg-muted-foreground rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
                  <div className="w-2 h-2 bg-muted-foreground rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
                  <div className="w-2 h-2 bg-muted-foreground rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
                </div>
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>
      </ScrollArea>

      <div className="border-t p-4">
        <div className="max-w-2xl mx-auto space-y-2">
          {(phase === "mic_check" || phase === "speaking" || phase === "listening") && (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              {phase === "listening" ? <Ear className="h-3 w-3" /> : <BookOpen className="h-3 w-3" />}
              {phase === "listening"
                ? "Answer with your voice (or type) — replay the audio as many times as you need."
                : phase === "speaking"
                  ? "Press record, read the sentence aloud, then send — we score pronunciation from the recording."
                  : "Press record and read the sentence aloud to check your microphone."}
            </div>
          )}
          <div className="flex gap-2">
            <Button
              variant={isRecording ? "destructive" : "outline"}
              size="icon"
              onClick={toggleRecording}
              disabled={loading || transcribing}
              title={isRecording ? "Stop recording" : "Record your answer"}
            >
              {transcribing ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : isRecording ? (
                <MicOff className="h-4 w-4" />
              ) : (
                <Mic className="h-4 w-4" />
              )}
            </Button>
            <Textarea
              value={inputText}
              onChange={(e) => {
                setInputText(e.target.value)
                if (lastSourceRef.current === "voice" && pendingWordsRef.current) {
                  // The transcript was edited — the STT word timestamps no
                  // longer match. Drop them; the server recomputes WER/PER
                  // from the edited text (fluency is then unavailable).
                  pendingWordsRef.current = null
                  lastSourceRef.current = "text"
                }
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault()
                  sendMessage()
                }
              }}
              placeholder={
                isRecording
                  ? `Recording… ${recordElapsed.toFixed(0)}s (max 30s)`
                  : transcribing
                    ? "Transcribing your answer…"
                    : "Type your answer, or press the mic to record."
              }
              disabled={loading || isRecording || transcribing}
              className="flex-1 min-h-[80px] max-h-[200px]"
              rows={3}
            />
            <Button onClick={sendMessage} disabled={loading || isRecording || transcribing || !inputText.trim()} size="icon">
              {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
