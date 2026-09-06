"use client"

import { useState, useEffect, useRef } from "react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { api, setActiveSessionId, getActiveSessionId } from "@/lib/api"
import { WebSpeechSTT } from "@/lib/stt/web-speech"
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
} from "lucide-react"

// Module-level so the React compiler lint doesn't see Date.now() called
// in component scope (it's impure — but this only runs in event handlers).
function makeTempMessageId() {
  return `temp-${Date.now()}`
}

export default function ConversationPage() {
  const [user, setUser] = useState<User | null>(null)
  const [scenarios, setScenarios] = useState<Scenario[]>([])
  const [selectedScenario, setSelectedScenario] = useState<string>("")
  const [cefrLevel, setCefrLevel] = useState<CEFRLevel>("B1")
  const [session, setSession] = useState<Session | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [inputText, setInputText] = useState("")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")
  const [isListening, setIsListening] = useState(false)
  const [summary, setSummary] = useState<SessionSummary | null>(null)
  const [expandedCorrections, setExpandedCorrections] = useState<Set<string>>(new Set())
  const [recentSessions, setRecentSessions] = useState<Session[]>([])
  const [showHistory, setShowHistory] = useState(false)
  const [pageLoading, setPageLoading] = useState(true)
  const [playingAudioId, setPlayingAudioId] = useState<string | null>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const sttRef = useRef<WebSpeechSTT | null>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)

  // Stop recognition and audio when leaving the page
  useEffect(() => {
    return () => {
      sttRef.current?.stop()
      if (audioRef.current) {
        audioRef.current.pause()
        audioRef.current = null
      }
    }
  }, [])

  // Load a session's messages into the chat view.
  const loadSession = async (sessionId: string) => {
    setLoading(true)
    try {
      const [s, msgs] = await Promise.all([api.sessions.get(sessionId), api.sessions.messages(sessionId)])
      setSession(s)
      setMessages(msgs)
      setCefrLevel((s.cefr_level as CEFRLevel) || "B1")
      setSummary(null)
      setActiveSessionId(s.id)
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
        setCefrLevel((u.current_level as CEFRLevel) || "B1")
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
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages])

  const startSession = async () => {
    setError("")
    try {
      const s = await api.sessions.create(selectedScenario || null, cefrLevel)
      setSession(s)
      setMessages([])
      setSummary(null)
      setActiveSessionId(s.id)
      setRecentSessions((prev) => [s, ...prev])
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to start session")
    }
  }

  const endSession = async () => {
    if (!session) return
    setError("")
    setLoading(true)
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
    if (!session || !inputText.trim() || loading) return
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
      setMessages((prev) => {
        const withoutTemp = prev.filter((m) => m.id !== tempId)
        return [...withoutTemp, turn.user_message, turn.assistant_message]
      })
      if (turn.audio_url) {
        playAudio(turn.assistant_message.id, turn.audio_url)
      }
    } catch (err: unknown) {
      setMessages((prev) => prev.filter((m) => m.id !== tempId))
      setInputText(text)
      setError(err instanceof Error ? err.message : "Failed to send message")
    } finally {
      setLoading(false)
    }
  }

  const toggleListening = () => {
    if (isListening) {
      sttRef.current?.stop()
      setIsListening(false)
      return
    }

    const stt = new WebSpeechSTT()
    if (!stt.isAvailable()) {
      setError("Web Speech API not available in this browser. Try Chrome or Edge.")
      return
    }

    stt.onResult((text) => {
      setInputText(text)
      setIsListening(false)
    })
    stt.onError((msg) => {
      setIsListening(false)
      setError(msg)
    })

    sttRef.current = stt
    stt.start()
    setIsListening(true)
  }

  const playAudio = (msgId: string, url: string) => {
    // Stop any currently playing audio before starting a new one
    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current = null
    }
    const audio = new Audio(url)
    audio.onended = () => setPlayingAudioId(null)
    audio.onerror = () => {
      setPlayingAudioId(null)
      setError("Could not play audio")
    }
    audioRef.current = audio
    setPlayingAudioId(msgId)
    audio.play().catch((err: unknown) => {
      setPlayingAudioId(null)
      // Autoplay policy: play() right after the awaited send can outlive the
      // click's transient activation (NotAllowedError) — especially on the
      // first message while TTS warms up. The per-message play button works
      // with a fresh gesture, so no error banner for that case.
      if (err instanceof DOMException && err.name === "NotAllowedError") return
      setError("Could not play audio")
    })
  }

  const stopAudio = () => {
    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current = null
    }
    setPlayingAudioId(null)
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
    setSession(null)
    setMessages([])
    setSummary(null)
    setActiveSessionId(null)
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
            <AlertCircle className="h-4 w-4" />
            {error}
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
          <CardHeader>
            <CardTitle>Choose your scenario</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">Scenario</label>
              <Select value={selectedScenario} onValueChange={setSelectedScenario}>
                <SelectTrigger>
                  <SelectValue placeholder="Select a scenario" />
                </SelectTrigger>
                <SelectContent>
                  {scenarios.map((s) => (
                    <SelectItem key={s.id} value={s.id}>
                      {s.name} {s.is_custom && "(custom)"}
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
            <Button onClick={startSession} className="w-full" size="lg">
              Start Conversation
            </Button>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-[calc(100vh-0px)] md:h-screen">
      <div className="flex items-center justify-between p-4 border-b">
        <div className="flex items-center gap-2">
          <Badge>{cefrLevel}</Badge>
          <span className="text-sm text-muted-foreground">
            {messages.length} messages
          </span>
        </div>
        <div className="flex gap-2">
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

      {error && (
        <div className="flex items-center gap-2 mx-4 mt-2 p-3 rounded-md bg-destructive/10 text-destructive text-sm">
          <AlertCircle className="h-4 w-4 shrink-0" />
          <span className="flex-1">{error}</span>
          <Button variant="ghost" size="sm" onClick={() => setError("")}>Dismiss</Button>
        </div>
      )}

      <ScrollArea className="flex-1 p-4">
        <div className="max-w-2xl mx-auto space-y-4">
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
                              <span className="text-green-400">{c.correction}</span>
                            </div>
                            <p className="text-muted-foreground mt-1">{c.explanation}</p>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
                {msg.audio_url && (
                  <button
                    onClick={() => playingAudioId === msg.id ? stopAudio() : playAudio(msg.id, msg.audio_url!)}
                    className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                  >
                    {playingAudioId === msg.id ? (
                      <>
                        <Square className="h-3 w-3" />
                        Stop audio
                      </>
                    ) : (
                      <>
                        <Volume2 className="h-3 w-3" />
                        Play audio
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
        <div className="max-w-2xl mx-auto flex gap-2">
          <Button
            variant={isListening ? "destructive" : "outline"}
            size="icon"
            onClick={toggleListening}
            disabled={loading || !!session.ended_at}
          >
            {isListening ? <MicOff className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
          </Button>
          <Textarea
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault()
                sendMessage()
              }
            }}
            placeholder={session.ended_at ? "This session has ended. Start a new one to continue." : isListening ? "Listening..." : "Type your message..."}
            disabled={loading || isListening || !!session.ended_at}
            className="flex-1 min-h-[80px] max-h-[200px]"
            rows={3}
          />
          <Button onClick={sendMessage} disabled={loading || !inputText.trim() || !!session.ended_at} size="icon">
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          </Button>
        </div>
      </div>
    </div>
  )
}
