"use client"

import { useState, useEffect, useRef } from "react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { api, setActiveSessionId, getActiveSessionId } from "@/lib/api"
import { useToast } from "@/components/feedback/toast"
import { startRecording, RecorderError, type RecorderHandle } from "@/lib/recorder"
import { WebSpeechSTT } from "@/lib/stt/web-speech"
import { formatElapsed, useElapsed } from "@/lib/use-elapsed"
import type { Scenario, Message, Session, SessionSummary, CEFRLevel, User } from "@/lib/types"
import { CEFR_LEVELS } from "@/lib/types"
import {
  Mic,
  MicOff,
  Send,
  Volume2,
  StopCircle,
  Sparkles,
  AlertCircle,
  ChevronDown,
  ChevronUp,
  History,
  Loader2,
  Square,
  Plus,
  X,
  Info,
} from "lucide-react"

// Module-level so the React compiler lint doesn't see Date.now() called
// in component scope (it's impure — but this only runs in event handlers).
function makeTempMessageId() {
  return `temp-${Date.now()}`
}

// Unsent text is the most painful thing to lose on a 401 redirect, a refresh or
// a tab switch: keep a per-session draft in sessionStorage (scoped to the tab,
// wiped when the browser closes).
function draftKey(sessionId: string): string {
  return `ef_draft_${sessionId}`
}

function readDraft(sessionId: string): string {
  if (typeof window === "undefined") return ""
  return sessionStorage.getItem(draftKey(sessionId)) ?? ""
}

function formatTimer(seconds: number): string {
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins}:${secs.toString().padStart(2, "0")}`
}

const MAX_RECORD_SECONDS = 30

export default function ConversationPage() {
  const { toast } = useToast()
  const [user, setUser] = useState<User | null>(null)
  const [scenarios, setScenarios] = useState<Scenario[]>([])
  const [selectedScenario, setSelectedScenario] = useState<string>("")
  const [cefrLevel, setCefrLevel] = useState<CEFRLevel>("A1")
  const [session, setSession] = useState<Session | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [inputText, setInputText] = useState("")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")
  const [summary, setSummary] = useState<SessionSummary | null>(null)
  const [expandedCorrections, setExpandedCorrections] = useState<Set<string>>(new Set())
  const [recentSessions, setRecentSessions] = useState<Session[]>([])
  const [showHistory, setShowHistory] = useState(false)
  const [pageLoading, setPageLoading] = useState(true)
  const [playingAudioId, setPlayingAudioId] = useState<string | null>(null)
  const [showBriefing, setShowBriefing] = useState(false)

  // Custom scenario creation modal state
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [newScenarioName, setNewScenarioName] = useState("")
  const [newScenarioDesc, setNewScenarioDesc] = useState("")
  const [newScenarioLevel, setNewScenarioLevel] = useState<CEFRLevel>("A1")
  const [isCreatingScenario, setIsCreatingScenario] = useState(false)
  const [createModalError, setCreateModalError] = useState("")

  // Universal Voice Recording state
  const [isRecording, setIsRecording] = useState(false)
  const [recordSeconds, setRecordSeconds] = useState(0)
  const [isTranscribing, setIsTranscribing] = useState(false)
  const [isListeningFallback, setIsListeningFallback] = useState(false)

  // Elapsed time for the tutor's turn: the HTTP call can take 60s+ with
  // reasoning models, and bare dots look frozen.
  const thinkingSeconds = useElapsed(loading)

  const messagesEndRef = useRef<HTMLDivElement>(null)
  const sttRef = useRef<WebSpeechSTT | null>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const recorderHandleRef = useRef<RecorderHandle | null>(null)
  const cancelRecordingRef = useRef<boolean>(false)
  const levelBarRef = useRef<HTMLDivElement>(null)
  const utteranceRef = useRef<SpeechSynthesisUtterance | null>(null)

  // Escape key listener to close custom scenario modal [REV-003]
  useEffect(() => {
    if (!showCreateModal) return
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setShowCreateModal(false)
        setCreateModalError("")
      }
    }
    window.addEventListener("keydown", handleKeyDown)
    return () => window.removeEventListener("keydown", handleKeyDown)
  }, [showCreateModal])

  // Stop recognition, recording and audio when leaving the page
  useEffect(() => {
    return () => {
      cancelRecordingRef.current = true
      if (recorderHandleRef.current) {
        recorderHandleRef.current.stop().catch(() => {})
        recorderHandleRef.current = null
      }
      sttRef.current?.stop()
      if (audioRef.current) {
        audioRef.current.pause()
        audioRef.current.onended = null
        audioRef.current.onerror = null
        audioRef.current.src = ""
        audioRef.current = null
      }
      if (typeof window !== "undefined" && "speechSynthesis" in window) {
        window.speechSynthesis.cancel()
        utteranceRef.current = null
      }
    }
  }, [])

  // Audio playback controls [REV-011]
  const stopAudio = () => {
    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current.onended = null
      audioRef.current.onerror = null
      audioRef.current.src = ""
      audioRef.current = null
    }
    if (typeof window !== "undefined" && "speechSynthesis" in window) {
      window.speechSynthesis.cancel()
      utteranceRef.current = null
    }
    setPlayingAudioId(null)
  }

  const playServerAudio = (msgId: string, url: string) => {
    stopAudio()
    const audio = new Audio(url)
    audio.onended = () => setPlayingAudioId(null)
    audio.onerror = () => {
      setPlayingAudioId(null)
      setError("Could not play server audio.")
    }
    audioRef.current = audio
    setPlayingAudioId(msgId)
    audio.play().catch((err: unknown) => {
      setPlayingAudioId(null)
      if (err instanceof DOMException && err.name === "NotAllowedError") return
      setError("Could not play server audio.")
    })
  }

  // Play browser TTS with utteranceRef to prevent Chrome GC mid-sentence [REV-005]
  const playBrowserTTS = (msgId: string, text: string) => {
    stopAudio()
    if (typeof window === "undefined" || !("speechSynthesis" in window)) {
      setError("Speech synthesis is not supported in this browser.")
      return
    }
    const utterance = new SpeechSynthesisUtterance(text)
    utteranceRef.current = utterance
    utterance.lang = "en-US"
    utterance.onend = () => {
      utteranceRef.current = null
      setPlayingAudioId(null)
    }
    utterance.onerror = () => {
      utteranceRef.current = null
      setPlayingAudioId(null)
    }
    setPlayingAudioId(msgId)
    window.speechSynthesis.speak(utterance)
  }

  const toggleAudioPlayback = (msg: Message) => {
    if (playingAudioId === msg.id) {
      stopAudio()
      return
    }
    if (msg.audio_url) {
      playServerAudio(msg.id, msg.audio_url)
    } else {
      playBrowserTTS(msg.id, msg.text)
    }
  }

  // Load a session's messages into the chat view.
  const loadSession = async (sessionId: string) => {
    setLoading(true)
    try {
      const [s, msgs] = await Promise.all([
        api.sessions.get(sessionId),
        api.sessions.messages(sessionId),
      ])
      setSession(s)
      setMessages(msgs)
      setCefrLevel((s.cefr_level as CEFRLevel) || "A1")
      setSummary(null)
      setActiveSessionId(s.id)
      setInputText(readDraft(s.id))
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load session")
      setActiveSessionId(null)
    } finally {
      setLoading(false)
    }
  }

  // Initial load: user profile, scenarios, and active/recent session
  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const [u, sc] = await Promise.all([api.auth.me(), api.scenarios.list()])
        if (cancelled) return
        setUser(u)
        setCefrLevel((u.current_level as CEFRLevel) || "A1")
        setScenarios(sc)

        const sessions = await api.sessions.list()
        if (cancelled) return
        setRecentSessions(sessions)

        const activeId = getActiveSessionId()
        if (activeId) {
          const active = sessions.find((s) => s.id === activeId && !s.ended_at)
          if (active) {
            await loadSession(active.id)
          } else {
            setActiveSessionId(null)
          }
        }
      } catch (err: unknown) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load")
      } finally {
        if (!cancelled) setPageLoading(false)
      }
    }
    load()
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages])

  // Persist the draft shortly after typing stops (no state updates here).
  useEffect(() => {
    if (!session?.id) return
    const key = draftKey(session.id)
    const timer = setTimeout(() => {
      if (inputText.trim()) sessionStorage.setItem(key, inputText)
      else sessionStorage.removeItem(key)
    }, 400)
    return () => clearTimeout(timer)
  }, [inputText, session?.id])

  const startSession = async () => {
    setError("")
    setLoading(true)
    try {
      const scenarioId = selectedScenario && selectedScenario !== "free_talk" ? selectedScenario : null
      const s = await api.sessions.create(scenarioId, cefrLevel)
      setSession(s)
      setActiveSessionId(s.id)
      setSummary(null)
      setInputText(readDraft(s.id))
      setRecentSessions((prev) => [s, ...prev])

      // Immediately populate initial proactive greeting if present in response [REV-009],
      // or fallback to fetching session messages
      let msgs: Message[] = []
      if (s.initial_message) {
        msgs = [s.initial_message]
      } else {
        msgs = await api.sessions.messages(s.id)
      }
      setMessages(msgs)

      // Auto-play greeting audio if available (or trigger TTS if appropriate)
      const greetingMsg = msgs.find((m) => m.role === "assistant")
      if (greetingMsg) {
        if (greetingMsg.audio_url) {
          playServerAudio(greetingMsg.id, greetingMsg.audio_url)
        } else if (typeof window !== "undefined" && "speechSynthesis" in window) {
          playBrowserTTS(greetingMsg.id, greetingMsg.text)
        }
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to start session")
    } finally {
      setLoading(false)
    }
  }

  const endSession = async () => {
    if (!session) return
    setError("")
    setLoading(true)
    stopAudio()
    try {
      const s = await api.sessions.end(session.id)
      setSummary(s)
      setSession(null)
      setActiveSessionId(null)
      const sessions = await api.sessions.list()
      setRecentSessions(sessions)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to end session")
    } finally {
      setLoading(false)
    }
  }

  const sendMessage = async () => {
    if (!session || !inputText.trim() || loading || isTranscribing) return
    const text = inputText.trim()
    const tempId = makeTempMessageId()

    const userMsg: Message = {
      id: tempId,
      session_id: session.id,
      role: "user",
      text,
      audio_url: null,
      created_at: new Date().toISOString(),
      corrections: [],
    }
    setMessages((prev) => [...prev, userMsg])
    setInputText("")
    setLoading(true)
    setError("")

    try {
      const turn = await api.messages.send(session.id, text)
      sessionStorage.removeItem(draftKey(session.id))
      setMessages((prev) => {
        const withoutTemp = prev.filter((m) => m.id !== tempId)
        return [...withoutTemp, turn.user_message, turn.assistant_message]
      })
      if (turn.audio_url) {
        playServerAudio(turn.assistant_message.id, turn.audio_url)
      }
    } catch (err: unknown) {
      setMessages((prev) => prev.filter((m) => m.id !== tempId))
      setInputText(text)
      const message = err instanceof Error ? err.message : "Failed to send message"
      setError(message)
      toast({
        title: "Message not sent",
        description: `${message} Your text was restored so you can retry.`,
        variant: "error",
      })
    } finally {
      setLoading(false)
    }
  }

  // Universal Voice Recording handlers
  const handleUploadRecording = async (blob: Blob) => {
    if (!session) return
    setIsTranscribing(true)
    setError("")
    try {
      const res = await api.sessions.uploadRecording(session.id, blob)
      const transcript = (res.transcript || "").trim()
      if (transcript) {
        setInputText((prev) => {
          const trimmed = prev.trim()
          return trimmed ? `${trimmed} ${transcript}` : transcript
        })
      } else {
        setError("No speech detected. Please speak clearly into your microphone.")
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to transcribe audio recording")
    } finally {
      setIsTranscribing(false)
      setRecordSeconds(0)
      if (levelBarRef.current) {
        levelBarRef.current.style.width = "8%"
      }
    }
  }

  const startWebSpeechFallback = () => {
    const stt = new WebSpeechSTT()
    if (!stt.isAvailable()) {
      setError("Speech recognition is not available in this browser. Try Chrome or Edge.")
      return
    }
    setIsListeningFallback(true)
    stt.onResult((text) => {
      setInputText((prev) => {
        const trimmed = prev.trim()
        return trimmed ? `${trimmed} ${text.trim()}` : text.trim()
      })
      setIsListeningFallback(false)
    })
    stt.onError((msg) => {
      setIsListeningFallback(false)
      setError(msg)
    })
    // Ensure listening state resets when recognition finishes [REV-004]
    stt.onEnd(() => {
      setIsListeningFallback(false)
    })
    sttRef.current = stt
    stt.start()
  }

  const handleStartRecording = async () => {
    if (!session || !!session.ended_at || loading || isTranscribing) return
    setError("")
    cancelRecordingRef.current = false

    try {
      const handle = await startRecording({
        maxSeconds: MAX_RECORD_SECONDS,
        onTick: (elapsed) => setRecordSeconds(elapsed),
        // Directly update level bar DOM element without 60 FPS React re-renders [REV-002]
        onLevel: (lvl) => {
          if (levelBarRef.current) {
            levelBarRef.current.style.width = `${Math.min(100, Math.max(8, lvl * 100))}%`
          }
        },
        onStop: async (blob) => {
          setIsRecording(false)
          recorderHandleRef.current = null
          if (levelBarRef.current) {
            levelBarRef.current.style.width = "8%"
          }
          if (cancelRecordingRef.current) {
            cancelRecordingRef.current = false
            return
          }
          await handleUploadRecording(blob)
        },
      })
      recorderHandleRef.current = handle
      setIsRecording(true)
      setRecordSeconds(0)
      if (levelBarRef.current) {
        levelBarRef.current.style.width = "8%"
      }
    } catch (err: unknown) {
      if (err instanceof RecorderError && err.code === "unsupported") {
        startWebSpeechFallback()
      } else if (err instanceof RecorderError && err.code === "permission") {
        setError("Microphone permission denied. Please allow microphone access in your browser.")
      } else {
        const stt = new WebSpeechSTT()
        if (stt.isAvailable()) {
          startWebSpeechFallback()
        } else {
          setError(err instanceof Error ? err.message : "Failed to start recording")
        }
      }
    }
  }

  const handleStopRecording = async () => {
    if (!recorderHandleRef.current) return
    cancelRecordingRef.current = false
    try {
      await recorderHandleRef.current.stop()
    } catch (err: unknown) {
      setIsRecording(false)
      recorderHandleRef.current = null
      setError(err instanceof Error ? err.message : "Recording failed")
    }
  }

  const handleCancelRecording = () => {
    cancelRecordingRef.current = true
    setIsRecording(false)
    setRecordSeconds(0)
    if (levelBarRef.current) {
      levelBarRef.current.style.width = "8%"
    }
    if (recorderHandleRef.current) {
      recorderHandleRef.current.stop().catch(() => {})
      recorderHandleRef.current = null
    }
  }

  // Custom scenario creation handler
  const handleCreateCustomScenario = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!newScenarioName.trim() || !newScenarioDesc.trim()) {
      setCreateModalError("Please provide both a scenario name and description.")
      return
    }
    setIsCreatingScenario(true)
    setCreateModalError("")
    try {
      const created = await api.scenarios.create(
        newScenarioName.trim(),
        newScenarioDesc.trim(),
        newScenarioLevel
      )
      const refreshed = await api.scenarios.list()
      setScenarios(refreshed)
      setSelectedScenario(created.id)
      setCefrLevel((created.cefr_level as CEFRLevel) || newScenarioLevel)
      setShowCreateModal(false)
      setNewScenarioName("")
      setNewScenarioDesc("")
    } catch (err: unknown) {
      setCreateModalError(err instanceof Error ? err.message : "Failed to create scenario")
    } finally {
      setIsCreatingScenario(false)
    }
  }

  const toggleCorrections = (msgId: string) => {
    setExpandedCorrections((prev) => {
      const next = new Set(prev)
      if (next.has(msgId)) next.delete(msgId)
      else next.add(msgId)
      return next
    })
  }

  const newSession = () => {
    stopAudio()
    setSession(null)
    setMessages([])
    setInputText("")
    setSummary(null)
    setActiveSessionId(null)
    setShowBriefing(false)
  }

  if (pageLoading) {
    return (
      <div className="max-w-2xl mx-auto p-6 space-y-4">
        <Skeleton className="h-8 w-48" />
        <Card>
          <CardContent className="p-6 space-y-4">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-32" />
          </CardContent>
        </Card>
      </div>
    )
  }

  if (summary) {
    return (
      <div className="max-w-2xl mx-auto p-6 space-y-6">
        <h1 className="text-2xl font-bold">Session Summary</h1>
        <Card>
          <CardContent className="p-6 space-y-4">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div className="text-center">
                <div className="text-2xl font-bold">{summary.duration_minutes}</div>
                <div className="text-xs text-muted-foreground">Minutes</div>
              </div>
              <div className="text-center">
                <div className="text-2xl font-bold">{summary.total_messages}</div>
                <div className="text-xs text-muted-foreground">Messages</div>
              </div>
              <div className="text-center">
                <div className="text-2xl font-bold">{summary.total_corrections}</div>
                <div className="text-xs text-muted-foreground">Corrections</div>
              </div>
              <div className="text-center">
                <div className="text-2xl font-bold">{summary.new_words_learned}</div>
                <div className="text-xs text-muted-foreground">New Words</div>
              </div>
            </div>
            {Object.keys(summary.corrections_by_type).length > 0 && (
              <div>
                <h3 className="font-semibold mb-2">Errors by Type</h3>
                <div className="flex flex-wrap gap-2">
                  {Object.entries(summary.corrections_by_type).map(([type, count]) => (
                    <Badge key={type} variant="secondary">
                      {type}: {count}
                    </Badge>
                  ))}
                </div>
              </div>
            )}
            {summary.new_vocab_list.length > 0 && (
              <div>
                <h3 className="font-semibold mb-2">New Vocabulary</h3>
                <div className="space-y-2">
                  {summary.new_vocab_list.map((v, i) => (
                    <div key={i} className="text-sm">
                      <span className="font-medium">{v.word}</span>
                      <span className="text-muted-foreground"> — {v.definition}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </CardContent>
        </Card>
        <Button onClick={newSession} className="w-full">
          Start New Session
        </Button>
      </div>
    )
  }

  if (!session) {
    return (
      <div className="max-w-2xl mx-auto p-6 space-y-6">
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Sparkles className="h-6 w-6 text-primary" />
            New Conversation
          </h1>
          <Button variant="ghost" size="sm" onClick={() => setShowHistory(!showHistory)} className="gap-1">
            <History className="h-4 w-4" />
            {showHistory ? "Hide" : "Recent"}
          </Button>
        </div>
        {error && (
          <div className="flex items-center gap-2 p-3 rounded-md bg-destructive/10 text-destructive text-sm">
            <AlertCircle className="h-4 w-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}
        {showHistory && (
          <Card>
            <CardHeader>
              <CardTitle className="text-lg">Recent Sessions</CardTitle>
            </CardHeader>
            <CardContent className="p-4 space-y-2">
              {recentSessions.length === 0 ? (
                <p className="text-sm text-muted-foreground">No sessions yet.</p>
              ) : (
                recentSessions.slice(0, 10).map((s) => (
                  <button
                    key={s.id}
                    onClick={() => loadSession(s.id)}
                    className="w-full text-left flex items-center justify-between p-3 rounded-lg bg-secondary hover:bg-secondary/80 transition-colors"
                  >
                    <div>
                      <p className="text-sm font-medium">{s.scenario_name || "Free Talk"}</p>
                      <p className="text-xs text-muted-foreground">
                        {new Date(s.started_at).toLocaleString()} · {s.message_count} messages · {s.cefr_level}
                      </p>
                    </div>
                    {s.ended_at ? (
                      <Badge variant="outline" className="text-xs">Ended</Badge>
                    ) : (
                      <Badge className="text-xs">Active</Badge>
                    )}
                  </button>
                ))
              )}
            </CardContent>
          </Card>
        )}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-4">
            <CardTitle>Choose your scenario</CardTitle>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setShowCreateModal(true)}
              className="gap-1 text-xs"
            >
              <Plus className="h-3.5 w-3.5" />
              Create Custom Scenario
            </Button>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">Scenario</label>
              <Select value={selectedScenario} onValueChange={setSelectedScenario}>
                <SelectTrigger>
                  <SelectValue placeholder="Select a scenario (or Free Talk)" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="free_talk">Free Talk (No specific scenario)</SelectItem>
                  {scenarios.map((s) => (
                    <SelectItem key={s.id} value={s.id}>
                      {s.name} {s.is_custom ? "(custom)" : ""}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">CEFR Level</label>
              <Select value={cefrLevel} onValueChange={(v) => setCefrLevel(v as CEFRLevel)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {CEFR_LEVELS.map((l) => (
                    <SelectItem key={l} value={l}>{l}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {user && (
                <p className="text-xs text-muted-foreground">
                  Your current level is <strong>{user.current_level}</strong>. You can still pick a different level to challenge yourself.
                </p>
              )}
            </div>
            <Button onClick={startSession} className="w-full" size="lg" disabled={loading}>
              {loading ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin mr-2" />
                  Preparing Tutor Greeting...
                </>
              ) : (
                "Start Conversation"
              )}
            </Button>
          </CardContent>
        </Card>

        {/* Custom Scenario Creation Modal */}
        {showCreateModal && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-background/80 backdrop-blur-sm animate-in fade-in duration-200">
            <div
              role="dialog"
              aria-modal="true"
              aria-labelledby="custom-scenario-title"
              className="relative w-full max-w-lg bg-card border rounded-xl shadow-2xl p-6 space-y-4"
            >
              <div className="flex items-center justify-between border-b pb-3">
                <h2 id="custom-scenario-title" className="text-lg font-semibold flex items-center gap-2">
                  <Sparkles className="h-5 w-5 text-primary" />
                  Create Custom Scenario
                </h2>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8"
                  aria-label="Close dialog"
                  onClick={() => {
                    setShowCreateModal(false)
                    setCreateModalError("")
                  }}
                >
                  <X className="h-4 w-4" />
                </Button>
              </div>

              {createModalError && (
                <div className="flex items-center gap-2 p-3 rounded-md bg-destructive/10 text-destructive text-sm">
                  <AlertCircle className="h-4 w-4 shrink-0" />
                  <span>{createModalError}</span>
                </div>
              )}

              <form onSubmit={handleCreateCustomScenario} className="space-y-4">
                <div className="space-y-1.5">
                  <Label htmlFor="scenario-name">Scenario Name *</Label>
                  <Input
                    id="scenario-name"
                    placeholder="e.g. Salary Negotiation with Department Head"
                    value={newScenarioName}
                    onChange={(e) => setNewScenarioName(e.target.value)}
                    required
                  />
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="scenario-desc">Situation / Objective *</Label>
                  <Textarea
                    id="scenario-desc"
                    placeholder="Describe the situation, your role, and the tutor's role (e.g. You are having an annual review with your manager and want to pitch for a raise based on delivered projects)."
                    value={newScenarioDesc}
                    onChange={(e) => setNewScenarioDesc(e.target.value)}
                    rows={4}
                    required
                  />
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="scenario-level">CEFR Level</Label>
                  <Select
                    value={newScenarioLevel}
                    onValueChange={(v) => setNewScenarioLevel(v as CEFRLevel)}
                  >
                    <SelectTrigger id="scenario-level">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {CEFR_LEVELS.map((lvl) => (
                        <SelectItem key={lvl} value={lvl}>
                          {lvl}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="flex justify-end gap-2 pt-2 border-t">
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => {
                      setShowCreateModal(false)
                      setCreateModalError("")
                    }}
                    disabled={isCreatingScenario}
                  >
                    Cancel
                  </Button>
                  <Button
                    type="submit"
                    disabled={isCreatingScenario || !newScenarioName.trim() || !newScenarioDesc.trim()}
                  >
                    {isCreatingScenario ? (
                      <>
                        <Loader2 className="h-4 w-4 animate-spin mr-1.5" />
                        Creating...
                      </>
                    ) : (
                      "Create Scenario"
                    )}
                  </Button>
                </div>
              </form>
            </div>
          </div>
        )}
      </div>
    )
  }

  const activeScenario = scenarios.find((s) => s.id === session?.scenario_id)

  return (
    <div className="flex flex-col h-[calc(100vh-0px)] md:h-screen">
      {/* Header with Collapsible Scenario Briefing */}
      <div className="border-b bg-card">
        <div className="flex items-center justify-between p-4">
          <div className="flex items-center gap-3">
            <button
              onClick={() => setShowBriefing(!showBriefing)}
              className="flex items-center gap-2 text-left group hover:opacity-90 transition-opacity"
              title="Toggle scenario briefing and objectives"
            >
              <div className="flex flex-col">
                <div className="flex items-center gap-2">
                  <h2 className="font-semibold text-sm sm:text-base group-hover:text-primary transition-colors">
                    {session.scenario_name || "Conversation Practice"}
                  </h2>
                  <Badge variant="outline" className="text-xs font-normal">
                    {cefrLevel}
                  </Badge>
                  {showBriefing ? (
                    <ChevronUp className="h-4 w-4 text-muted-foreground" />
                  ) : (
                    <ChevronDown className="h-4 w-4 text-muted-foreground" />
                  )}
                </div>
                <span className="text-xs text-muted-foreground">
                  {messages.length} message{messages.length === 1 ? "" : "s"} · Click for briefing
                </span>
              </div>
            </button>
          </div>

          <div className="flex items-center gap-2">
            {playingAudioId && (
              <Button variant="outline" size="sm" onClick={stopAudio} className="gap-1">
                <Square className="h-4 w-4" />
                Stop Audio
              </Button>
            )}
            <Button variant="outline" size="sm" onClick={newSession}>
              New
            </Button>
            {!session.ended_at && (
              <Button variant="destructive" size="sm" onClick={endSession} disabled={loading}>
                {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <StopCircle className="h-4 w-4 mr-1" />}
                End Session
              </Button>
            )}
          </div>
        </div>

        {/* Collapsible Scenario Briefing / Objectives panel */}
        {showBriefing && (
          <div className="px-4 pb-4 pt-1 border-t bg-muted/30 text-xs sm:text-sm space-y-2 animate-in slide-in-from-top-2 duration-200">
            <div className="flex items-start gap-2">
              <Info className="h-4 w-4 text-primary shrink-0 mt-0.5" />
              <div className="space-y-1.5 flex-1">
                <div>
                  <span className="font-semibold text-foreground">Situation & Objective: </span>
                  <span className="text-muted-foreground">
                    {activeScenario?.system_prompt || (
                      session.scenario_name
                        ? `Practice realistic English conversation in character for ${session.scenario_name}.`
                        : "Open-ended free-talk conversation to practice natural communication, ask questions, and build fluency."
                    )}
                  </span>
                </div>
                <div>
                  <span className="font-semibold text-foreground">Target CEFR Level: </span>
                  <Badge variant="secondary" className="text-xs py-0 h-5">
                    {cefrLevel}
                  </Badge>
                </div>
                <div>
                  <span className="font-semibold text-foreground">Conversation Tips: </span>
                  <span className="text-muted-foreground">
                    {cefrLevel === "A1" || cefrLevel === "A2"
                      ? "Keep your sentences simple and clear. Don't worry about mistakes — your tutor will guide and correct you naturally."
                      : cefrLevel === "B1" || cefrLevel === "B2"
                      ? "Try to expand your thoughts with reasoning, conjunctions (e.g. 'because', 'however'), and varied vocabulary."
                      : "Strive for natural idioms, nuanced expressions, and precise phrasing to polish your fluency to near-native."}
                  </span>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      {error && (
        <div className="flex items-center gap-2 mx-4 mt-2 p-3 rounded-md bg-destructive/10 text-destructive text-sm">
          <AlertCircle className="h-4 w-4 shrink-0" />
          <span className="flex-1">{error}</span>
          <Button variant="ghost" size="sm" onClick={() => setError("")}>Dismiss</Button>
        </div>
      )}

      {session.ended_at && (
        <div className="mx-4 mt-2 flex flex-wrap items-center gap-2 rounded-md border bg-secondary/60 p-3 text-sm">
          <Info className="h-4 w-4 shrink-0 text-muted-foreground" />
          <span className="flex-1">
            This session has ended. Corrections and vocabulary are saved — start a new
            session to keep practicing.
          </span>
          <Button variant="outline" size="sm" onClick={newSession}>
            New session
          </Button>
        </div>
      )}

      <ScrollArea className="flex-1 p-4">
        <div className="max-w-2xl mx-auto space-y-4" role="log" aria-live="polite" aria-relevant="additions">
          {messages.length === 0 && (
            <div className="text-center py-12 text-muted-foreground">
              <Mic className="h-12 w-12 mx-auto mb-4 opacity-50" />
              <p>Start the conversation! Type or speak in English.</p>
            </div>
          )}
          {messages.map((msg) => (
            <div key={msg.id} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
              <div className={`max-w-[80%] space-y-1`}>
                <div
                  className={`rounded-2xl px-4 py-2 ${
                    msg.role === "user"
                      ? "bg-primary text-primary-foreground"
                      : "bg-secondary"
                  }`}
                >
                  <p className="text-sm">{msg.text}</p>
                </div>
                {msg.corrections.length > 0 && (
                  <div>
                    <button
                      onClick={() => toggleCorrections(msg.id)}
                      className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                    >
                      <AlertCircle className="h-3 w-3" />
                      {msg.corrections.length} correction{msg.corrections.length > 1 ? "s" : ""}
                      {expandedCorrections.has(msg.id) ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                    </button>
                    {expandedCorrections.has(msg.id) && (
                      <div className="mt-1 space-y-1">
                        {msg.corrections.map((c) => (
                          <div key={c.id} className="text-xs bg-destructive/10 rounded-lg p-2 border border-destructive/20">
                            <div className="flex items-center gap-2">
                              <Badge variant="destructive" className="text-[10px]">{c.error_type}</Badge>
                            </div>
                            <div className="mt-1">
                              <span className="line-through text-destructive">{c.original_fragment}</span>
                              {" → "}
                              <span className="text-emerald-600 dark:text-emerald-400 font-medium">{c.correction}</span>
                            </div>
                            <p className="text-muted-foreground mt-1">{c.explanation}</p>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
                {/* Audio playback for all assistant messages: server audio or browser TTS */}
                {msg.role === "assistant" && (
                  <button
                    onClick={() => toggleAudioPlayback(msg)}
                    className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
                    title={msg.audio_url ? "Play server audio" : "Listen (browser speech)"}
                  >
                    {playingAudioId === msg.id ? (
                      <>
                        <Square className="h-3 w-3 text-red-500 fill-current" />
                        <span className="text-red-500">Stop audio</span>
                      </>
                    ) : (
                      <>
                        <Volume2 className="h-3 w-3" />
                        <span>{msg.audio_url ? "Play audio" : "Listen"}</span>
                      </>
                    )}
                  </button>
                )}
              </div>
            </div>
          ))}
          {loading && (
            <div className="flex justify-start">
              <div className="bg-secondary rounded-2xl px-4 py-2">
                <div className="flex items-center gap-2">
                  <div className="flex gap-1">
                    <div className="w-2 h-2 bg-muted-foreground rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
                    <div className="w-2 h-2 bg-muted-foreground rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
                    <div className="w-2 h-2 bg-muted-foreground rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
                  </div>
                  <span className="text-xs text-muted-foreground">
                    {isTranscribing
                      ? "Transcribing your voice…"
                      : `Tutor is thinking… ${formatElapsed(thinkingSeconds)}`}
                  </span>
                </div>
                {!isTranscribing && thinkingSeconds >= 20 && (
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    Still working — reasoning models can take up to a minute. Your
                    message is safe.
                  </p>
                )}
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>
      </ScrollArea>

      {/* Input area with Universal Voice Recording indicators */}
      <div className="border-t p-4 space-y-2 bg-background">
        {/* Visual active recording bar */}
        {isRecording && (
          <div className="max-w-2xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-3 p-3 rounded-lg border bg-secondary/80 animate-in fade-in duration-200">
            <div className="flex items-center gap-3 w-full sm:w-auto">
              <div className="relative flex items-center justify-center">
                <span className="animate-ping absolute inline-flex h-4 w-4 rounded-full bg-red-400 opacity-75" />
                <span className="relative inline-flex rounded-full h-3 w-3 bg-red-600" />
              </div>
              <div className="flex flex-col">
                <span className="text-xs font-semibold text-foreground">
                  Recording: {formatTimer(recordSeconds)} / {formatTimer(MAX_RECORD_SECONDS)}
                </span>
                <div className="w-36 h-1.5 bg-muted rounded-full overflow-hidden mt-1">
                  <div
                    ref={levelBarRef}
                    className="h-full bg-red-500 transition-all duration-100 ease-out"
                    style={{ width: "8%" }}
                  />
                </div>
              </div>
            </div>
            <div className="flex items-center gap-2 w-full sm:w-auto justify-end">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={handleCancelRecording}
                className="text-xs h-8"
              >
                Cancel
              </Button>
              <Button
                type="button"
                variant="default"
                size="sm"
                onClick={handleStopRecording}
                className="text-xs h-8 gap-1.5 bg-red-600 hover:bg-red-700 text-white"
              >
                <Square className="h-3 w-3 fill-current" />
                Stop & Transcribe
              </Button>
            </div>
          </div>
        )}

        {/* Transcribing indicator [REV-003] */}
        {isTranscribing && (
          <div
            role="status"
            aria-live="polite"
            className="max-w-2xl mx-auto flex items-center gap-2 p-2 rounded-lg bg-secondary text-xs text-muted-foreground animate-pulse"
          >
            <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
            <span>Transcribing audio with speech-to-text...</span>
          </div>
        )}

        <div className="max-w-2xl mx-auto flex gap-2">
          <Button
            type="button"
            variant={isRecording || isListeningFallback ? "destructive" : "outline"}
            size="icon"
            onClick={
              isRecording
                ? handleStopRecording
                : isListeningFallback
                ? () => {
                    sttRef.current?.stop()
                    setIsListeningFallback(false)
                  }
                : handleStartRecording
            }
            disabled={loading || isTranscribing || !!session.ended_at}
            title={isRecording ? "Stop & transcribe recording" : isListeningFallback ? "Stop listening" : "Record voice answer"}
          >
            {isRecording || isListeningFallback ? <MicOff className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
          </Button>

          <Textarea
            id="chat-input"
            aria-label="Type your message in English"
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault()
                sendMessage()
              }
            }}
            placeholder={
              session.ended_at
                ? "This session has ended. Start a new one to continue."
                : isRecording
                ? "Recording voice... Click 'Stop & Transcribe' when done."
                : isListeningFallback
                ? "Listening..."
                : isTranscribing
                ? "Transcribing audio..."
                : "Type your message or record your voice..."
            }
            disabled={loading || isRecording || isListeningFallback || isTranscribing || !!session.ended_at}
            className="flex-1 min-h-[80px] max-h-[200px]"
            rows={3}
          />

          <Button
            type="button"
            aria-label="Send message"
            onClick={sendMessage}
            disabled={loading || !inputText.trim() || isRecording || isTranscribing || !!session.ended_at}
            size="icon"
          >
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          </Button>
        </div>
      </div>
    </div>
  )
}
