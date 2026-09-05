"use client"

import { useState, useEffect, useRef } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { api } from "@/lib/api"
import { WebSpeechSTT } from "@/lib/stt/web-speech"
import type { Scenario, Message, Session, SessionSummary, CEFRLevel } from "@/lib/types"
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
} from "lucide-react"

export default function ConversationPage() {
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
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const sttRef = useRef<WebSpeechSTT | null>(null)

  // Stop recognition when leaving the page — never leave the mic hot
  useEffect(() => {
    return () => {
      sttRef.current?.stop()
    }
  }, [])

  useEffect(() => {
    api.scenarios.list().then(setScenarios).catch((err: unknown) => {
      setError(err instanceof Error ? err.message : "Failed to load scenarios")
    })
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
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to start session")
    }
  }

  const endSession = async () => {
    if (!session) return
    setError("")
    try {
      const s = await api.sessions.end(session.id)
      setSummary(s)
      setSession(null)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to end session")
    }
  }

  const sendMessage = async () => {
    if (!session || !inputText.trim() || loading) return
    const text = inputText.trim()
    const tempId = `temp-${Date.now()}`

    // Optimistic append — rolled back on failure
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
        // Replace the optimistic entry and append the assistant reply
        const withoutTemp = prev.filter((m) => m.id !== tempId)
        return [...withoutTemp, turn.assistant_message]
      })
      if (turn.audio_url) {
        playAudio(turn.audio_url)
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

  const playAudio = (url: string) => {
    const audio = new Audio(url)
    audio.play().catch(() => setError("Could not play audio"))
  }

  const toggleCorrections = (msgId: string) => {
    setExpandedCorrections((prev) => {
      const next = new Set(prev)
      if (next.has(msgId)) next.delete(msgId)
      else next.add(msgId)
      return next
    })
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
        <Button onClick={() => { setSummary(null); setMessages([]) }} className="w-full">
          Start New Session
        </Button>
      </div>
    )
  }

  if (!session) {
    return (
      <div className="max-w-lg mx-auto p-6 space-y-6">
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <Sparkles className="h-6 w-6 text-primary" />
          New Conversation
        </h1>
        {error && (
          <div className="flex items-center gap-2 p-3 rounded-md bg-destructive/10 text-destructive text-sm">
            <AlertCircle className="h-4 w-4" />
            {error}
          </div>
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
        <Button variant="destructive" size="sm" onClick={endSession}>
          <StopCircle className="h-4 w-4 mr-1" />
          End Session
        </Button>
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
                    onClick={() => playAudio(msg.audio_url!)}
                    className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                  >
                    <Volume2 className="h-3 w-3" />
                    Play audio
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
            disabled={loading}
          >
            {isListening ? <MicOff className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
          </Button>
          <Input
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && sendMessage()}
            placeholder={isListening ? "Listening..." : "Type your message..."}
            disabled={loading || isListening}
            className="flex-1"
          />
          <Button onClick={sendMessage} disabled={loading || !inputText.trim()} size="icon">
            <Send className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  )
}
