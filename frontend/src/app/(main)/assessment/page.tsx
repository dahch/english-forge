"use client"

import { useState, useEffect, useRef } from "react"
import { useRouter } from "next/navigation"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Progress } from "@/components/ui/progress"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import type { Assessment, AssessmentMessage } from "@/lib/types"
import { WebSpeechSTT } from "@/lib/stt/web-speech"
import { Mic, MicOff, Send, Sparkles, AlertCircle, Loader2, CheckCircle2 } from "lucide-react"

// Must match MAX_ASSESSMENT_EXCHANGES in backend/app/routers/assessment.py.
const MAX_ASSESSMENT_QUESTIONS = 10

export default function AssessmentPage() {
  const router = useRouter()
  const [assessment, setAssessment] = useState<Assessment | null>(null)
  const [messages, setMessages] = useState<AssessmentMessage[]>([])
  const [inputText, setInputText] = useState("")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")
  const [isListening, setIsListening] = useState(false)
  const [completed, setCompleted] = useState(false)
  const [generatingPath, setGeneratingPath] = useState(false)
  const sttRef = useRef<WebSpeechSTT | null>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    return () => {
      sttRef.current?.stop()
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
        // If the assessment is already completed, show the results screen
        if (a.completed_at) {
          setCompleted(true)
        }
      } catch {
        // No assessment found, user will start manually
      }
    }
    resume()
  }, [])

  const start = async () => {
    setLoading(true)
    setError("")
    try {
      const a = await api.assessment.start()
      setAssessment(a)
      setMessages(a.messages)
      setCompleted(false)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to start assessment")
    } finally {
      setLoading(false)
    }
  }

  const sendMessage = async () => {
    if (!assessment || !inputText.trim() || loading) return
    const text = inputText.trim()
    setInputText("")
    setLoading(true)
    setError("")

    try {
      const a = await api.assessment.send(assessment.id, text)
      setMessages(a.messages)
      if (a.is_complete) {
        await completeAssessment(a.id)
      }
    } catch (err: unknown) {
      setInputText(text)
      setError(err instanceof Error ? err.message : "Failed to send message")
    } finally {
      setLoading(false)
    }
  }

  const completeAssessment = async (id: string) => {
    setLoading(true)
    try {
      const a = await api.assessment.complete(id)
      setAssessment(a)
      setCompleted(true)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to complete assessment")
    } finally {
      setLoading(false)
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

  const toggleListening = () => {
    if (isListening) {
      sttRef.current?.stop()
      setIsListening(false)
      return
    }
    const stt = new WebSpeechSTT()
    if (!stt.isAvailable()) {
      setError("Web Speech API not available. Try Chrome or Edge.")
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

  const assistantCount = messages.filter((m) => m.role === "assistant").length

  if (completed && assessment) {
    return (
      <div className="max-w-2xl mx-auto p-6 space-y-6">
        <div className="text-center space-y-2">
          <CheckCircle2 className="h-16 w-16 text-green-400 mx-auto" />
          <h1 className="text-3xl font-bold">Assessment Complete</h1>
          <p className="text-muted-foreground">Your estimated level is</p>
          <div className="text-6xl font-bold text-primary">{assessment.estimated_level}</div>
          <p className="text-sm text-muted-foreground">Confidence: {Math.round((assessment.confidence || 0) * 100)}%</p>
        </div>

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
                {assessment.strengths?.map((s, i) => <li key={i}>{s}</li>) || <li className="text-muted-foreground">None detected</li>}
              </ul>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle className="text-lg text-destructive">Weaknesses</CardTitle>
            </CardHeader>
            <CardContent>
              <ul className="list-disc list-inside text-sm">
                {assessment.weaknesses?.map((w, i) => <li key={i}>{w}</li>) || <li className="text-muted-foreground">None detected</li>}
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

        <Button onClick={generatePath} disabled={generatingPath} className="w-full" size="lg">
          {generatingPath ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Sparkles className="h-4 w-4 mr-1" />}
          {generatingPath ? "Generating path..." : "Generate My Learning Path"}
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
            Have a 10-minute conversation with your tutor. We&apos;ll analyze your English level (A1-C2) and build a personalized learning path.
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
          <Badge variant="secondary">{Math.min(assistantCount, MAX_ASSESSMENT_QUESTIONS)} / {MAX_ASSESSMENT_QUESTIONS}</Badge>
        </div>
        <Button variant="outline" size="sm" onClick={() => completeAssessment(assessment.id)} disabled={loading}>
          Finish & See Results
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
          {messages.map((msg) => (
            <div key={msg.id} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
              <div className={`max-w-[80%] rounded-2xl px-4 py-2 ${msg.role === "user" ? "bg-primary text-primary-foreground" : "bg-secondary"}`}>
                <p className="text-sm">{msg.text}</p>
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
          <Button variant={isListening ? "destructive" : "outline"} size="icon" onClick={toggleListening} disabled={loading}>
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
            placeholder={isListening ? "Listening..." : "Type your answer..."}
            disabled={loading || isListening}
            className="flex-1 min-h-[80px] max-h-[200px]"
            rows={3}
          />
          <Button onClick={sendMessage} disabled={loading || !inputText.trim()} size="icon">
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          </Button>
        </div>
      </div>
    </div>
  )
}
