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
import { useToast } from "@/components/feedback/toast"
import { api, ApiError } from "@/lib/api"
import { startRecording, type RecorderHandle } from "@/lib/recorder"
import { formatElapsed, useElapsed } from "@/lib/use-elapsed"
import { cn } from "@/lib/utils"
import type { Assessment, AssessmentMessage, DimensionScores } from "@/lib/types"
import { Mic, MicOff, Send, Sparkles, AlertCircle, Loader2, CheckCircle2, RefreshCw, Ear } from "lucide-react"

// Must match backend/app/routers/assessment.py.
const MAX_ASSESSMENT_QUESTIONS = 10
const MIN_ASSESSMENT_EXCHANGES = 6

const PHASE_LABELS: Record<string, string> = {
  mic_check: "Mic check",
  conversation: "Interview",
  listening: "Listening",
  speaking: "Pronunciation",
}

const PHASE_ORDER = ["mic_check", "conversation", "listening", "speaking"] as const
const PHASE_TITLES: Record<string, string> = {
  mic_check: "Mic check",
  conversation: "Interview",
  listening: "Listening",
  speaking: "Speaking",
}

function progressText(
  phase: string,
  questionsAsked: number,
  sectionStep: number,
  sectionTotal: number,
): string {
  switch (phase) {
    case "mic_check":
      return "Read the sentence aloud so we can check your microphone."
    case "conversation":
      return `Question ${Math.max(1, Math.min(questionsAsked, MAX_ASSESSMENT_QUESTIONS))} of ${MAX_ASSESSMENT_QUESTIONS}`
    case "listening":
      return `Audio item ${Math.min(sectionStep + 1, sectionTotal || 1)} of ${sectionTotal || 1} — replay as often as you need`
    case "speaking":
      return `Read-aloud item ${Math.min(sectionStep + 1, sectionTotal || 1)} of ${sectionTotal || 1}`
    default:
      return ""
  }
}

// Explicit section stepper: previously the only signal was a badge, so users
// could not tell whether they were on item 2 or 5, or how much was left.
function PhaseStepper({
  phase,
  questionsAsked,
  sectionStep,
  sectionTotal,
}: {
  phase: string
  questionsAsked: number
  sectionStep: number
  sectionTotal: number
}) {
  const foundIndex = PHASE_ORDER.indexOf(phase as (typeof PHASE_ORDER)[number])
  const currentIndex = foundIndex === -1 ? 0 : foundIndex

  return (
    <div className="border-b px-4 pb-2 pt-2">
      <ol className="flex items-center gap-1.5 overflow-x-auto text-[11px]">
        {PHASE_ORDER.map((step, index) => {
          const state = index < currentIndex ? "done" : index === currentIndex ? "current" : "upcoming"
          return (
            <li
              key={step}
              aria-current={state === "current" ? "step" : undefined}
              className="flex items-center gap-1.5"
            >
              {index > 0 && <span className="h-px w-3 bg-border" aria-hidden="true" />}
              <span
                className={cn(
                  "inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5",
                  state === "done" && "border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
                  state === "current" && "border-primary bg-primary text-primary-foreground",
                  state === "upcoming" && "border-border text-muted-foreground",
                )}
              >
                {state === "done" ? (
                  <CheckCircle2 className="h-3 w-3" aria-hidden="true" />
                ) : (
                  <span className="tabular-nums opacity-70">{index + 1}</span>
                )}
                {PHASE_TITLES[step]}
              </span>
            </li>
          )
        })}
      </ol>
      <p className="mt-1.5 text-xs text-muted-foreground" aria-live="polite">
        {progressText(phase, questionsAsked, sectionStep, sectionTotal)}
      </p>
    </div>
  )
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
            {/* Audio always playable — for audio-only items the play button IS
                the question; hiding the text must not block playback. */}
            <AudioButton assessmentId={assessmentId} messageId={msg.id} />
          </div>
        )}
      </div>
    </div>
  )
}

export default function AssessmentPage() {
  const router = useRouter()
  const { toast } = useToast()
  const [assessment, setAssessment] = useState<Assessment | null>(null)
  const [messages, setMessages] = useState<AssessmentMessage[]>([])
  const [inputText, setInputText] = useState("")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")
  const [completed, setCompleted] = useState(false)
  const [generatingPath, setGeneratingPath] = useState(false)
  const [reanalyzing, setReanalyzing] = useState(false)
  // "Start over" confirmation: abandoning is destructive to the current
  // attempt (not to previous results), so it always asks first.
  const [showRestartConfirm, setShowRestartConfirm] = useState(false)
  const [restarting, setRestarting] = useState(false)
  // Analysis is a separate 35-45s operation; "loading" alone only disabled the
  // button with no explanation of what was happening.
  const [completing, setCompleting] = useState(false)
  const loadingSeconds = useElapsed(loading)
  const completingSeconds = useElapsed(completing)

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
  // Render mirror of lastSourceRef — refs can't be read during render, and
  // the voice-only phases need to enable Send only for voice transcripts.
  const [hasVoiceTranscript, setHasVoiceTranscript] = useState(false)

  const messagesEndRef = useRef<HTMLDivElement>(null)

  // Phases where typing would defeat the measurement (mic check + read-aloud
  // pronunciation): the input is voice-only and the STT transcript read-only.
  const phase = assessment?.phase ?? "conversation"
  const voiceOnlyPhase = phase === "mic_check" || phase === "speaking"

  useEffect(() => {
    return () => {
      recorderRef.current?.stop().catch(() => {})
    }
  }, [])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages])

  useEffect(() => {
    if (!showRestartConfirm) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !restarting) setShowRestartConfirm(false)
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [showRestartConfirm, restarting])

  useEffect(() => {
    let cancelled = false
    async function resume() {
      try {
        const a = await api.assessment.current()
        if (cancelled) return
        setAssessment(a)
        setMessages(a.messages)
        if (a.completed_at) setCompleted(true)
      } catch (err: unknown) {
        if (cancelled) return
        // 404 = no assessment yet (expected). Anything else is a real failure
        // and used to be swallowed, leaving the user on a blank "Start" screen.
        if (err instanceof ApiError && err.status === 404) return
        setError(
          err instanceof Error ? err.message : "Could not load your assessment. Try again.",
        )
      }
    }
    resume()
    return () => {
      cancelled = true
    }
  }, [])

  const applyResponse = (a: Assessment) => {
    setAssessment(a)
    setMessages(a.messages)
    return a
  }

  const start = async (): Promise<boolean> => {
    setLoading(true)
    setError("")
    try {
      applyResponse(await api.assessment.start())
      setCompleted(false)
      setInputText("")
      pendingWordsRef.current = null
      lastSourceRef.current = "text"
      setHasVoiceTranscript(false)
      return true
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to start assessment"
      setError(message)
      toast({ title: "Could not start the assessment", description: message, variant: "error" })
      return false
    } finally {
      setLoading(false)
    }
  }

  const completeAssessment = async (id: string) => {
    // The analysis can take 35-45s with reasoning models. "completing" drives
    // the staged banner (what is running + elapsed time); errors are toasted,
    // not just left as a line the user might miss.
    if (completing) return
    setLoading(true)
    setCompleting(true)
    setError("")
    try {
      const result = await api.assessment.complete(id)
      applyResponse(result)
      setCompleted(true)
      toast({
        title: "Assessment complete",
        description: result.estimated_level
          ? `Estimated level: ${result.estimated_level}`
          : undefined,
        variant: "success",
      })
    } catch (err: unknown) {
      try {
        const current = await api.assessment.current()
        if (current.completed_at) {
          // The server finished even though the response was lost.
          applyResponse(current)
          setCompleted(true)
          setError("")
          toast({
            title: "Results recovered",
            description: "The analysis finished on the server. Your results are ready.",
            variant: "success",
          })
          return
        }
      } catch {
        // Fall through to the original error.
      }
      const message =
        err instanceof Error ? err.message : "Failed to complete assessment"
      setError(message)
      toast({
        title: "Could not analyze the assessment",
        description: message,
        variant: "error",
      })
    } finally {
      setLoading(false)
      setCompleting(false)
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
    setHasVoiceTranscript(false)
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
      setHasVoiceTranscript(result.transcript.trim().length > 0)
      if (!result.transcript.trim()) {
        setError(
          voiceOnlyPhase
            ? "We couldn't hear anything — check your microphone and try recording again."
            : "We couldn't hear anything — check your microphone or type your answer."
        )
      }
    } catch (err: unknown) {
      const denied = err instanceof Error && err.message === "Microphone permission denied"
      setError(
        voiceOnlyPhase
          ? denied
            ? "Microphone permission denied — this answer needs your voice."
            : "Transcription failed — try recording again."
          : denied
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
        // recorder.onstop already triggers onStop → uploadBlob for both the
        // manual stop and the 30s auto-stop. Only await here to surface
        // recorder errors (e.g. no-data); do NOT upload again or every voice
        // answer would hit /recordings twice.
        await handle.stop()
      } catch {
        setError(voiceOnlyPhase ? "Recording failed — try again." : "Recording failed — you can type your answer instead.")
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
      // The backend may have finished generating even when the response was
      // lost (e.g. the dev server restarting kills the proxy mid-flight) —
      // the path row is already persisted. Recover via /current instead of
      // dead-ending on an error the user can't act on.
      try {
        await api.learningPath.current()
        router.push("/learning-path")
      } catch {
        setError(err instanceof Error ? err.message : "Failed to generate learning path")
      }
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

  // Abandon the in-progress attempt (backend frees the "one in-progress
  // assessment" invariant) and immediately start a clean one.
  const restartAssessment = async () => {
    if (!assessment || restarting) return
    setRestarting(true)
    setError("")
    try {
      await api.assessment.abandon(assessment.id)
      setShowRestartConfirm(false)
      setAssessment(null)
      setMessages([])
      setCompleted(false)
      const started = await start()
      if (started) {
        toast({
          title: "Assessment restarted",
          description: "Starting again with the microphone check.",
          variant: "success",
        })
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to restart the assessment")
    } finally {
      setRestarting(false)
    }
  }

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
    const bands = assessment.dimension_bands ?? {}
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
                    <span className="text-muted-foreground">
                      {Math.round(score)}/100
                      {typeof bands[dim] === "string" ? ` · ${bands[dim]}` : ""}
                    </span>
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
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setShowRestartConfirm(true)}
            disabled={loading || restarting}
            title="Discard this attempt and start again from the mic check"
          >
            <RefreshCw className="h-4 w-4 mr-1" />
            Start over
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => completeAssessment(assessment.id)}
            disabled={loading || phase === "mic_check"}
            title={
              phase === "mic_check"
                ? "Answer the mic check first — the analysis needs at least one recorded answer."
                : phase === "conversation" && assistantCount < MIN_ASSESSMENT_EXCHANGES
                  ? `At least ${MIN_ASSESSMENT_EXCHANGES} exchanges are recommended for a reliable level — finishing now lowers the confidence.`
                  : undefined
            }
          >
            Finish & See Results
          </Button>
        </div>
      </div>

      <PhaseStepper
        phase={phase}
        questionsAsked={assessment.questions_asked ?? assistantCount}
        sectionStep={assessment.section_step ?? 0}
        sectionTotal={assessment.section_total ?? 0}
      />

      {error && (
        <div className="flex items-center gap-2 p-3 rounded-md bg-destructive/10 text-destructive text-sm mx-4 mt-2">
          <AlertCircle className="h-4 w-4 shrink-0" />
          <span className="flex-1">{error}</span>
          <Button variant="ghost" size="sm" onClick={() => setError("")}>Dismiss</Button>
        </div>
      )}

      {completing && (
        <div
          role="status"
          aria-live="polite"
          className="mx-4 mt-2 rounded-md border border-primary/30 bg-primary/5 p-3 text-sm"
        >
          <div className="flex items-center gap-2">
            <Loader2 className="h-4 w-4 animate-spin text-primary" />
            <span className="font-medium">
              Analyzing your results… {formatElapsed(completingSeconds)}
            </span>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            Grammar · vocabulary · fluency · listening · pronunciation. This can take
            up to a minute — keep this tab open; results are saved server-side.
          </p>
        </div>
      )}

      <ScrollArea className="flex-1 p-4">
        <div className="max-w-2xl mx-auto space-y-4" role="log" aria-live="polite" aria-relevant="additions">
          {messages.map((msg, i) => (
            <MessageBubble
              key={msg.id}
              msg={msg}
              index={i}
              lastAnsweredIndex={lastAnsweredIndex}
              assessmentId={assessment.id}
            />
          ))}
          {(loading || transcribing) && !completing && (
            <div className="flex justify-start">
              <div className="bg-secondary rounded-2xl px-4 py-2">
                <div className="flex items-center gap-2">
                  <div className="flex gap-1">
                    <div className="w-2 h-2 bg-muted-foreground rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
                    <div className="w-2 h-2 bg-muted-foreground rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
                    <div className="w-2 h-2 bg-muted-foreground rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
                  </div>
                  <span className="text-xs text-muted-foreground">
                    {transcribing
                      ? "Transcribing your answer…"
                      : `Tutor is thinking… ${formatElapsed(loadingSeconds)}`}
                  </span>
                </div>
                {!transcribing && loadingSeconds >= 20 && (
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    Reasoning models can take up to a minute — keep this tab open.
                  </p>
                )}
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
              {phase === "listening" ? <Ear className="h-3 w-3 shrink-0" /> : <Mic className="h-3 w-3 shrink-0" />}
              {phase === "listening"
                ? "Answer with your voice (or type) — replay the audio as many times as you need."
                : phase === "speaking"
                  ? "Voice only — press record, read the sentence aloud, then send it as it comes out. It doesn't need to be perfect: we're measuring how you speak."
                  : "Voice only — press record, read the sentence aloud, then send it as it comes out. No need to be perfect; that's exactly what we're checking."}
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
                    : voiceOnlyPhase
                      ? "Recording only — press the mic and speak. Typing is disabled here."
                      : "Type your answer, or press the mic to record."
              }
              readOnly={voiceOnlyPhase}
              disabled={loading || isRecording || transcribing}
              className="flex-1 min-h-[80px] max-h-[200px]"
              rows={3}
            />
            <Button
              onClick={sendMessage}
              disabled={
                loading ||
                isRecording ||
                transcribing ||
                !inputText.trim() ||
                (voiceOnlyPhase && !hasVoiceTranscript)
              }
              size="icon"
            >
              {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
            </Button>
          </div>
        </div>
      </div>

      {showRestartConfirm && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="restart-assessment-title"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
          onClick={() => {
            if (!restarting) setShowRestartConfirm(false)
          }}
        >
          <Card className="w-full max-w-md" onClick={(event) => event.stopPropagation()}>
            <CardHeader>
              <CardTitle id="restart-assessment-title">Start this assessment over?</CardTitle>
              <CardDescription>
                The current attempt is discarded and a new one begins with the mic
                check. Previous completed results are kept.
              </CardDescription>
            </CardHeader>
            <CardContent className="flex justify-end gap-2">
              <Button
                variant="outline"
                onClick={() => setShowRestartConfirm(false)}
                disabled={restarting}
              >
                Cancel
              </Button>
              <Button onClick={restartAssessment} disabled={restarting}>
                {restarting ? (
                  <Loader2 className="h-4 w-4 animate-spin mr-1" />
                ) : (
                  <RefreshCw className="h-4 w-4 mr-1" />
                )}
                {restarting ? "Restarting..." : "Discard & restart"}
              </Button>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  )
}
