"use client"

import { useState, useEffect, useRef } from "react"
import { useRouter } from "next/navigation"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Progress } from "@/components/ui/progress"
import { Skeleton } from "@/components/ui/skeleton"
import { api, ApiError, setActiveSessionId } from "@/lib/api"
import { useToast } from "@/components/feedback/toast"
import { formatElapsed, useElapsed } from "@/lib/use-elapsed"
import type { LearningPath, PathLesson, PathLessonDetail, VocabItemPayload } from "@/lib/types"
import {
  MapPin,
  CheckCircle2,
  Lock,
  Sparkles,
  AlertCircle,
  Loader2,
  Trophy,
  XCircle,
  MessageSquare,
  Volume2,
  VolumeX,
  Plus,
  Eye,
  EyeOff,
  PenLine,
  ArrowRight,
  BookOpen,
  RefreshCw,
} from "lucide-react"

const typeIcons: Record<string, string> = {
  vocabulary: "🔤",
  grammar: "📝",
  conversation: "💬",
  listening: "🎧",
  reading: "📖",
  writing: "✍️",
}

export default function LearningPathPage() {
  const router = useRouter()
  const { toast } = useToast()
  const [path, setPath] = useState<LearningPath | null>(null)
  const [loading, setLoading] = useState(true)
  // Separate flag so the skeleton can say "generating" (vs "loading").
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState("")
  const [advancing, setAdvancing] = useState(false)
  const [completing, setCompleting] = useState<string | null>(null)

  // Lesson detail view state
  const [activeLesson, setActiveLesson] = useState<PathLessonDetail | null>(null)
  const [lessonLoading, setLessonLoading] = useState(false)
  const [exerciseAnswers, setExerciseAnswers] = useState<Record<number, string>>({})
  const [exerciseResults, setExerciseResults] = useState<
    Record<number, { correct: boolean; correct_answer: string; explanation: string }>
  >({})
  // A failed request is NOT a wrong answer: keep it in its own state so the UI
  // can offer "Retry" instead of painting the network error as incorrect.
  const [exerciseErrors, setExerciseErrors] = useState<Record<number, string>>({})
  const [checking, setChecking] = useState<Record<number, boolean>>({})
  const generatingSeconds = useElapsed(generating)
  const lessonSeconds = useElapsed(lessonLoading)

  // Multi-type interactive state
  const [startingConversation, setStartingConversation] = useState(false)
  const [addingVocab, setAddingVocab] = useState(false)
  const [addedVocabCount, setAddedVocabCount] = useState<number | null>(null)
  const [isPlayingAudio, setIsPlayingAudio] = useState(false)
  const [showTranscript, setShowTranscript] = useState(false)
  const [hasEngaged, setHasEngaged] = useState(false)

  // Persistent reference to SpeechSynthesisUtterance to prevent garbage collection desync
  const utteranceRef = useRef<SpeechSynthesisUtterance | null>(null)

  // Clean up any ongoing TTS speech when switching lessons or leaving page
  useEffect(() => {
    return () => {
      if (typeof window !== "undefined" && "speechSynthesis" in window) {
        window.speechSynthesis.cancel()
      }
      utteranceRef.current = null
      setIsPlayingAudio(false)
    }
  }, [activeLesson])

  // Automatically mark engaged when viewing vocabulary items
  useEffect(() => {
    if (activeLesson?.lesson_type === "vocabulary") {
      const items = activeLesson.content?.items || activeLesson.content?.vocab_items || []
      if (items.length > 0) {
        setHasEngaged(true)
      }
    }
  }, [activeLesson])

  const generatePath = async () => {
    setLoading(true)
    setGenerating(true)
    setError("")
    try {
      const p = await api.learningPath.generate()
      setPath(p)
      if (p.generation_source === "curriculum") {
        toast({
          title: "Base curriculum generated",
          description:
            "The model didn't answer, so a default plan was used. Try generating again for a personalized one.",
          variant: "info",
        })
      } else if (p.generation_source === "llm") {
        toast({
          title: "Learning path ready",
          description: `${p.lessons_required} lessons from ${p.current_level} to ${p.target_level}.`,
          variant: "success",
        })
      }
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to generate path"
      setError(message)
      toast({ title: "Could not generate the path", description: message, variant: "error" })
    } finally {
      setLoading(false)
      setGenerating(false)
    }
  }

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const p = await api.learningPath.current()
        if (!cancelled) setPath(p)
      } catch (err: unknown) {
        if (cancelled) return
        if (err instanceof ApiError && err.status === 404) setPath(null)
        else setError(err instanceof Error ? err.message : "Failed to load learning path")
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => {
      cancelled = true
    }
  }, [])

  // Open the lesson detail: loads (or lazily generates) the interactive content
  const openLesson = async (lesson: PathLesson) => {
    if (!path) return
    setError("")
    setExerciseAnswers({})
    setExerciseResults({})
    setExerciseErrors({})
    setAddedVocabCount(null)
    setIsPlayingAudio(false)
    setShowTranscript(false)
    setHasEngaged(false)
    setActiveLesson(lesson)
    setLessonLoading(true)
    try {
      const detail = await api.learningPath.lessonDetail(path.id, lesson.id)
      setActiveLesson(detail)
      if (detail.lesson_type === "vocabulary") {
        const items = detail.content?.items || detail.content?.vocab_items || []
        if (items.length > 0) {
          setHasEngaged(true)
        }
      }
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to load lesson"
      setError(message)
      toast({ title: "Could not open the lesson", description: message, variant: "error" })
      setActiveLesson(null)
    } finally {
      setLessonLoading(false)
    }
  }

  const checkExercise = async (index: number, answerOverride?: string) => {
    if (!path || !activeLesson) return
    const answer = answerOverride ?? (exerciseAnswers[index] || "")
    if (!answer.trim()) return

    setChecking((prev) => ({ ...prev, [index]: true }))
    setExerciseErrors((prev) => ({ ...prev, [index]: "" }))
    try {
      const result = await api.learningPath.checkLessonExercise(path.id, activeLesson.id, index, answer)
      setExerciseResults((prev) => ({ ...prev, [index]: result }))
      setHasEngaged(true)
    } catch (err: unknown) {
      setExerciseErrors((prev) => ({
        ...prev,
        [index]: err instanceof Error ? err.message : "Could not check the answer. Try again.",
      }))
    } finally {
      setChecking((prev) => ({ ...prev, [index]: false }))
    }
  }

  const completeFromDetail = async () => {
    if (!path || !activeLesson || activeLesson.completed) return
    setCompleting(activeLesson.id)
    try {
      const p = await api.learningPath.completeLesson(path.id, activeLesson.id)
      setPath(p)
      setActiveLesson(null)
      toast({ title: "Lesson completed", description: activeLesson.topic, variant: "success" })
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to complete lesson"
      setError(message)
      toast({ title: "Could not complete the lesson", description: message, variant: "error" })
    } finally {
      setCompleting(null)
    }
  }

  // Conversation launch
  const handleStartConversation = async () => {
    if (!path || !activeLesson) return
    setStartingConversation(true)
    setError("")
    try {
      const res = await api.learningPath.startLessonConversation(path.id, activeLesson.id)
      setActiveSessionId(res.session_id)
      setHasEngaged(true)
      router.push("/conversation")
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to launch conversation session")
      setStartingConversation(false)
    }
  }

  // Vocabulary batch save
  const handleAddAllVocab = async () => {
    if (!activeLesson?.content) return
    const items = activeLesson.content.items || activeLesson.content.vocab_items || []
    if (!items.length) return
    setAddingVocab(true)
    try {
      const res = await api.learningPath.addVocabBatch(items)
      setAddedVocabCount(res.count)
      setHasEngaged(true)
      toast({
        title: res.count ? `${res.count} words added to your vocabulary` : "No new words to add",
        description: res.count ? "They're scheduled for spaced repetition." : undefined,
        variant: res.count ? "success" : "info",
      })
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to add vocabulary items"
      setError(message)
      toast({ title: "Could not add vocabulary", description: message, variant: "error" })
    } finally {
      setAddingVocab(false)
    }
  }

  // TTS Speech synthesis for listening passage or single word
  const speakText = (text: string, isPassage: boolean = false) => {
    if (typeof window === "undefined" || !("speechSynthesis" in window)) {
      setError("Speech synthesis is not supported in this browser.")
      return
    }
    window.speechSynthesis.cancel()
    setIsPlayingAudio(false)

    const utterance = new SpeechSynthesisUtterance(text)
    utteranceRef.current = utterance
    utterance.lang = "en-US"
    utterance.rate = isPassage ? 0.92 : 0.88

    utterance.onend = () => {
      utteranceRef.current = null
      if (isPassage) {
        setIsPlayingAudio(false)
      }
    }
    utterance.onerror = () => {
      utteranceRef.current = null
      if (isPassage) {
        setIsPlayingAudio(false)
      }
    }

    if (isPassage) {
      setIsPlayingAudio(true)
    }
    // Playing word audio or passage audio marks hasEngaged = true
    setHasEngaged(true)

    window.speechSynthesis.speak(utterance)
  }

  const stopPassageAudio = () => {
    if (typeof window !== "undefined" && "speechSynthesis" in window) {
      window.speechSynthesis.cancel()
    }
    utteranceRef.current = null
    setIsPlayingAudio(false)
  }

  const advance = async () => {
    if (!path) return
    setAdvancing(true)
    try {
      const p = await api.learningPath.advance(path.id)
      setPath(p)
      toast({
        title: "Level up!",
        description: `Your new path goes from ${p.current_level} to ${p.target_level}.`,
        variant: "success",
      })
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to advance"
      setError(message)
      toast({ title: "Could not advance", description: message, variant: "error" })
    } finally {
      setAdvancing(false)
    }
  }

  if (loading) {
    return (
      <div className="max-w-3xl mx-auto p-6 space-y-6">
        <Skeleton className="h-8 w-48" />
        {generating ? (
          <p className="text-sm text-muted-foreground" role="status" aria-live="polite">
            Generating your curriculum… {formatElapsed(generatingSeconds)}. This can
            take up to 30 seconds.
          </p>
        ) : (
          <Skeleton className="h-4 w-3/4" />
        )}
        <Card>
          <CardContent className="p-4 space-y-3">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </CardContent>
        </Card>
      </div>
    )
  }

  if (!path) {
    return (
      <div className="max-w-2xl mx-auto p-6 text-center space-y-6">
        <MapPin className="h-16 w-16 text-primary mx-auto" />
        <h1 className="text-2xl font-bold">No Active Learning Path</h1>
        <p className="text-muted-foreground">
          Complete the initial assessment first, or generate a path manually to start your journey from A1 to C2.
        </p>
        {error && (
          <div className="flex items-center gap-2 p-3 rounded-md bg-destructive/10 text-destructive text-sm">
            <AlertCircle className="h-4 w-4" />
            {error}
          </div>
        )}
        <div className="flex gap-2 justify-center">
          <Button onClick={() => router.push("/assessment")}>Go to Assessment</Button>
          <Button variant="outline" onClick={generatePath}>
            <Sparkles className="h-4 w-4 mr-1" />
            Generate Path from Current Level
          </Button>
        </div>
      </div>
    )
  }

  // --- Specialized Lesson Detail View ---
  if (path && activeLesson) {
    const content = activeLesson.content
    const exercises = content?.exercises ?? []
    const examples = content?.examples ?? []
    const vocabItems: VocabItemPayload[] = content?.items || content?.vocab_items || []
    const hasExercises = exercises.length > 0
    const allExercisesAnswered = hasExercises && exercises.every((_, i) => exerciseResults[i])

    // Engaging condition: exercises answered, or conversation launched / expressions reviewed, or vocab added / listening played
    const canComplete =
      activeLesson.completed ||
      (hasExercises ? allExercisesAnswered : hasEngaged || activeLesson.lesson_type === "conversation")

    return (
      <div className="max-w-3xl mx-auto p-4 sm:p-6 space-y-6">
        {/* Navigation & Status Header */}
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <Button variant="ghost" onClick={() => setActiveLesson(null)}>
            ← Back to Path
          </Button>
          {activeLesson.completed ? (
            <div className="flex items-center gap-1.5 text-xs font-semibold text-green-500 bg-green-500/10 px-3 py-1.5 rounded-full border border-green-500/20">
              <CheckCircle2 className="h-4 w-4" />
              <span>Lesson Completed</span>
            </div>
          ) : (
            <Button
              size="sm"
              onClick={completeFromDetail}
              disabled={completing === activeLesson.id || !canComplete}
              title={!canComplete ? "Engage with the lesson activities to mark complete" : undefined}
            >
              {completing === activeLesson.id ? (
                <Loader2 className="h-4 w-4 animate-spin mr-1" />
              ) : (
                <CheckCircle2 className="h-4 w-4 mr-1" />
              )}
              Mark Complete
            </Button>
          )}
        </div>

        {error && (
          <div className="flex items-center gap-2 p-3 rounded-md bg-destructive/10 text-destructive text-sm">
            <AlertCircle className="h-4 w-4 shrink-0" />
            <span className="flex-1">{error}</span>
            <Button variant="ghost" size="sm" onClick={() => setError("")}>
              Dismiss
            </Button>
          </div>
        )}

        {/* Lesson Title & Type Badge */}
        <div className="border-b pb-4">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-3xl">{typeIcons[activeLesson.lesson_type] || "📚"}</span>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-2xl font-bold">{activeLesson.topic}</h1>
                <Badge variant="secondary" className="capitalize">
                  {activeLesson.lesson_type}
                </Badge>
              </div>
              <p className="text-sm text-muted-foreground mt-1">{activeLesson.description}</p>
            </div>
          </div>
        </div>

        {lessonLoading || !content ? (
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground" role="status" aria-live="polite">
              {lessonLoading
                ? `Building this lesson… ${formatElapsed(lessonSeconds)}`
                : "Preparing the lesson…"}
            </p>
            <Skeleton className="h-28 w-full" />
            <Skeleton className="h-28 w-full" />
            <Skeleton className="h-40 w-full" />
          </div>
        ) : (
          <>
            {/* 1. CONVERSATION LESSON TYPE */}
            {activeLesson.lesson_type === "conversation" && (
              <div className="space-y-6">
                <Card>
                  <CardHeader>
                    <CardTitle className="text-lg flex items-center gap-2">
                      <MessageSquare className="h-5 w-5 text-primary" />
                      Roleplay Scenario & Objective
                    </CardTitle>
                    <CardDescription>
                      Level {path.current_level} · Practice real-world communication
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-4 text-sm">
                    {content.explanation && (
                      <p className="text-muted-foreground">{content.explanation}</p>
                    )}
                    <div className="bg-secondary/40 p-4 rounded-lg space-y-2 border">
                      <span className="font-semibold text-xs tracking-wider uppercase text-muted-foreground">
                        Scenario Context
                      </span>
                      <p>{content.scenario_context || activeLesson.description}</p>
                    </div>
                    {content.goal && (
                      <div className="bg-primary/5 p-4 rounded-lg space-y-1 border border-primary/20">
                        <span className="font-semibold text-xs tracking-wider uppercase text-primary">
                          Your Objective
                        </span>
                        <p className="font-medium">{content.goal}</p>
                      </div>
                    )}
                    {content.tutor_role && (
                      <p className="text-xs text-muted-foreground">
                        <strong>Tutor Character:</strong> {content.tutor_role}
                      </p>
                    )}
                  </CardContent>
                </Card>

                {content.target_expressions && content.target_expressions.length > 0 && (
                  <Card>
                    <CardHeader>
                      <CardTitle className="text-lg">Key Expressions to Use</CardTitle>
                      <CardDescription>Try to incorporate these expressions during the dialogue</CardDescription>
                    </CardHeader>
                    <CardContent>
                      <div className="flex flex-wrap gap-2">
                        {content.target_expressions.map((expr, i) => (
                          <Badge
                            key={i}
                            role="button"
                            tabIndex={0}
                            onKeyDown={(e) => {
                              if (e.key === "Enter" || e.key === " ") {
                                e.preventDefault()
                                speakText(expr)
                              }
                            }}
                            variant="outline"
                            className="text-sm py-1.5 px-3 bg-background cursor-pointer hover:border-primary transition-colors flex items-center gap-1.5 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
                            onClick={() => speakText(expr)}
                            title="Click to hear pronunciation"
                          >
                            <Volume2 className="h-3.5 w-3.5 text-muted-foreground" />
                            <span>{expr}</span>
                          </Badge>
                        ))}
                      </div>
                    </CardContent>
                  </Card>
                )}

                <Card className="border-primary/40 bg-gradient-to-br from-primary/5 via-background to-secondary/30">
                  <CardContent className="p-6 flex flex-col sm:flex-row items-center justify-between gap-4">
                    <div className="space-y-1 text-center sm:text-left">
                      <h3 className="font-semibold text-base">Ready to speak with your AI Tutor?</h3>
                      <p className="text-sm text-muted-foreground">
                        Launches a live conversation session loaded with this scenario and character.
                      </p>
                    </div>
                    <Button
                      size="lg"
                      className="gap-2 shrink-0 font-semibold"
                      onClick={handleStartConversation}
                      disabled={startingConversation}
                    >
                      {startingConversation ? (
                        <Loader2 className="h-5 w-5 animate-spin" />
                      ) : (
                        <MessageSquare className="h-5 w-5" />
                      )}
                      Start Conversation with Tutor
                    </Button>
                  </CardContent>
                </Card>
              </div>
            )}

            {/* 2. VOCABULARY LESSON TYPE */}
            {activeLesson.lesson_type === "vocabulary" && (
              <div className="space-y-6">
                <Card>
                  <CardHeader className="flex flex-row items-start justify-between gap-4">
                    <div>
                      <CardTitle className="text-lg">Vocabulary Focus</CardTitle>
                      <CardDescription>
                        {content.explanation || `Core terms and collocations for ${activeLesson.topic}`}
                      </CardDescription>
                    </div>
                    {vocabItems.length > 0 && (
                      <Button
                        size="sm"
                        variant={addedVocabCount !== null ? "secondary" : "default"}
                        onClick={handleAddAllVocab}
                        disabled={addingVocab || addedVocabCount !== null}
                        className="gap-1.5 shrink-0"
                      >
                        {addingVocab ? (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        ) : addedVocabCount !== null ? (
                          <CheckCircle2 className="h-4 w-4 text-green-500" />
                        ) : (
                          <Plus className="h-4 w-4" />
                        )}
                        {addedVocabCount !== null
                          ? `Saved ${addedVocabCount} to Vocab (SRS)`
                          : "Add All to My Vocab (SRS)"}
                      </Button>
                    )}
                  </CardHeader>
                </Card>

                <div className="grid gap-3 sm:grid-cols-2">
                  {vocabItems.map((item, i) => (
                    <Card key={i} className="hover:border-primary/40 transition-colors">
                      <CardContent className="p-4 space-y-2">
                        <div className="flex items-center justify-between">
                          <span className="font-bold text-base">{item.word}</span>
                          <div className="flex items-center gap-1.5">
                            {item.ipa && (
                              <Badge variant="outline" className="text-xs text-muted-foreground font-mono">
                                {item.ipa}
                              </Badge>
                            )}
                            <Button
                              size="icon"
                              variant="ghost"
                              className="h-7 w-7"
                              onClick={() => speakText(item.word)}
                              title="Listen to pronunciation"
                            >
                              <Volume2 className="h-4 w-4" />
                            </Button>
                          </div>
                        </div>
                        <p className="text-sm text-foreground/90">{item.definition}</p>
                        {item.example && (
                          <p className="text-xs text-muted-foreground italic bg-secondary/50 p-2 rounded">
                            &ldquo;{item.example}&rdquo;
                          </p>
                        )}
                      </CardContent>
                    </Card>
                  ))}
                </div>
              </div>
            )}

            {/* 3. LISTENING LESSON TYPE */}
            {activeLesson.lesson_type === "listening" && (
              <div className="space-y-6">
                <Card>
                  <CardHeader>
                    <CardTitle className="text-lg flex items-center gap-2">
                      <Volume2 className="h-5 w-5 text-primary" />
                      Listening Passage
                    </CardTitle>
                    <CardDescription>
                      {content.explanation || "Listen to the spoken audio and answer the questions below."}
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <div className="p-6 bg-secondary/30 rounded-xl border flex flex-col sm:flex-row items-center justify-between gap-4">
                      <div>
                        <h4 className="font-semibold text-sm">Spoken Passage Audio</h4>
                        <p className="text-xs text-muted-foreground">
                          Synthesized Text-to-Speech playback at natural pacing.
                        </p>
                      </div>
                      <div className="flex items-center gap-2">
                        {isPlayingAudio ? (
                          <Button variant="destructive" onClick={stopPassageAudio} className="gap-2">
                            <VolumeX className="h-4 w-4" /> Stop Audio
                          </Button>
                        ) : (
                          <Button
                            onClick={() => speakText(content.passage || content.transcript || "", true)}
                            className="gap-2"
                          >
                            <Volume2 className="h-4 w-4" /> Play Passage Audio
                          </Button>
                        )}
                      </div>
                    </div>

                    <div className="border rounded-lg p-3 space-y-2">
                      <div className="flex items-center justify-between">
                        <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                          Transcript
                        </span>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 text-xs gap-1"
                          onClick={() => {
                            setShowTranscript(!showTranscript)
                            setHasEngaged(true)
                          }}
                        >
                          {showTranscript ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                          {showTranscript ? "Hide Transcript" : "Show Transcript"}
                        </Button>
                      </div>
                      {showTranscript ? (
                        <div className="text-sm leading-relaxed p-3 bg-secondary/30 rounded border whitespace-pre-wrap">
                          {content.transcript || content.passage}
                        </div>
                      ) : (
                        <p className="text-xs text-muted-foreground italic">
                          Try answering the questions by listening first before revealing the transcript!
                        </p>
                      )}
                    </div>
                  </CardContent>
                </Card>
              </div>
            )}

            {/* 4. GRAMMAR / READING / WRITING COMMON SECTIONS */}
            {["grammar", "reading", "writing"].includes(activeLesson.lesson_type) && (
              <div className="space-y-6">
                {content.explanation && (
                  <Card>
                    <CardHeader>
                      <CardTitle className="text-lg">Explanation</CardTitle>
                      <CardDescription>
                        Targeted at your level: {path.current_level} → {path.target_level}
                      </CardDescription>
                    </CardHeader>
                    <CardContent>
                      <p className="text-sm leading-relaxed">{content.explanation}</p>
                    </CardContent>
                  </Card>
                )}

                {content.formula && (
                  <Card className="border-primary/30 bg-primary/5">
                    <CardHeader className="pb-2">
                      <CardTitle className="text-sm font-semibold uppercase tracking-wider text-primary">
                        Usage Formula / Structure
                      </CardTitle>
                    </CardHeader>
                    <CardContent>
                      <p className="font-mono text-sm font-semibold text-foreground">{content.formula}</p>
                    </CardContent>
                  </Card>
                )}

                {content.passage && (
                  <Card>
                    <CardHeader>
                      <CardTitle className="text-lg flex items-center gap-2">
                        <BookOpen className="h-5 w-5 text-primary" />
                        Reading Passage
                      </CardTitle>
                    </CardHeader>
                    <CardContent>
                      <div className="text-sm leading-relaxed whitespace-pre-wrap bg-secondary/20 p-4 rounded-lg border">
                        {content.passage}
                      </div>
                    </CardContent>
                  </Card>
                )}

                {content.writing_prompt && (
                  <Card className="border-primary/30">
                    <CardHeader>
                      <CardTitle className="text-lg flex items-center gap-2">
                        <PenLine className="h-5 w-5 text-primary" />
                        Writing Exercise
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-3">
                      <p className="text-sm font-medium">{content.writing_prompt}</p>
                      <p className="text-xs text-muted-foreground">
                        Draft your response in your personal notebook or practicing with tutor chat.
                      </p>
                    </CardContent>
                  </Card>
                )}

                {examples.length > 0 && (
                  <Card>
                    <CardHeader>
                      <CardTitle className="text-lg">Examples in Context</CardTitle>
                    </CardHeader>
                    <CardContent>
                      <ul className="space-y-2">
                        {examples.map((ex, i) => (
                          <li
                            key={i}
                            className="text-sm bg-secondary/60 rounded-lg p-3 border flex items-center justify-between"
                          >
                            <span>{ex}</span>
                            <Button
                              size="icon"
                              variant="ghost"
                              className="h-6 w-6 shrink-0"
                              onClick={() => speakText(ex)}
                              title="Listen"
                            >
                              <Volume2 className="h-3.5 w-3.5 text-muted-foreground" />
                            </Button>
                          </li>
                        ))}
                      </ul>
                    </CardContent>
                  </Card>
                )}
              </div>
            )}

            {/* 5. INTERACTIVE EXERCISES (Applies to Vocabulary, Listening, Grammar, Reading/Writing) */}
            {exercises.length > 0 && (
              <div className="space-y-4 pt-2">
                <div className="flex items-center justify-between">
                  <h2 className="text-xl font-semibold">Comprehension & Practice Exercises</h2>
                  <span className="text-xs text-muted-foreground">
                    {exercises.filter((_, i) => exerciseResults[i]?.correct).length} of {exercises.length} correct
                  </span>
                </div>

                {exercises.map((ex, i) => {
                  const result = exerciseResults[i]
                  const options = Array.isArray(ex.options) ? ex.options : undefined
                  const isMultipleChoice = ex.type === "multiple_choice" && !!options?.length
                  return (
                    <Card key={i}>
                      <CardContent className="p-4 space-y-3">
                        <p className="font-medium text-sm sm:text-base">{ex.question}</p>
                        {!result ? (
                          <div className="space-y-2">
                          {isMultipleChoice ? (
                            <div className="flex flex-wrap gap-2">
                              {options!.map((opt) => (
                                <Button
                                  key={opt}
                                  size="sm"
                                  variant={exerciseAnswers[i] === opt ? "default" : "outline"}
                                  disabled={checking[i]}
                                  onClick={() => {
                                    setExerciseAnswers((prev) => ({ ...prev, [i]: opt }))
                                    checkExercise(i, opt)
                                  }}
                                >
                                  {opt}
                                </Button>
                              ))}
                            </div>
                          ) : (
                            <div className="flex gap-2">
                              <Input
                                aria-label="Your answer for exercise"
                                placeholder="Type your answer"
                                value={exerciseAnswers[i] || ""}
                                onChange={(e) => setExerciseAnswers((prev) => ({ ...prev, [i]: e.target.value }))}
                                onKeyDown={(e) => e.key === "Enter" && checkExercise(i)}
                              />
                              <Button
                                size="sm"
                                onClick={() => checkExercise(i)}
                                disabled={!exerciseAnswers[i] || checking[i]}
                              >
                                {checking[i] ? <Loader2 className="h-4 w-4 animate-spin" /> : "Check"}
                              </Button>
                            </div>
                          )}
                          {exerciseErrors[i] && (
                            <div
                              role="alert"
                              className="flex items-center gap-2 text-xs text-destructive"
                            >
                              <AlertCircle className="h-3.5 w-3.5 shrink-0" />
                              <span className="flex-1">{exerciseErrors[i]}</span>
                              <Button
                                size="sm"
                                variant="outline"
                                onClick={() => checkExercise(i)}
                                disabled={checking[i]}
                              >
                                Retry
                              </Button>
                            </div>
                          )}
                          </div>
                        ) : (
                          <div
                            className={`flex items-start gap-2 text-sm p-3 rounded-md ${
                              result.correct
                                ? "bg-green-500/10 text-green-500 border border-green-500/20"
                                : "bg-destructive/10 text-destructive border border-destructive/20"
                            }`}
                          >
                            {result.correct ? (
                              <CheckCircle2 className="h-4 w-4 mt-0.5 shrink-0" />
                            ) : (
                              <XCircle className="h-4 w-4 mt-0.5 shrink-0" />
                            )}
                            <div>
                              <p className="font-medium">{result.explanation}</p>
                              {!result.correct && result.correct_answer && (
                                <p className="text-muted-foreground text-xs mt-1">
                                  Correct answer: <strong>{result.correct_answer}</strong>
                                </p>
                              )}
                            </div>
                          </div>
                        )}
                      </CardContent>
                    </Card>
                  )
                })}
              </div>
            )}
          </>
        )}
      </div>
    )
  }

  // --- Curriculum Path Main View ---
  const percent = Math.min(100, Math.round((path.lessons_completed / path.lessons_required) * 100))
  const canAdvance = path.lessons_completed >= path.lessons_required

  return (
    <div className="max-w-3xl mx-auto p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <MapPin className="h-6 w-6 text-primary" />
            Your Learning Path
          </h1>
          <p className="text-sm text-muted-foreground">
            From <strong>{path.current_level}</strong> to <strong>{path.target_level}</strong> ·{" "}
            {path.lessons_completed} of {path.lessons_required} lessons completed
          </p>
        </div>
        {canAdvance && (
          <Button onClick={advance} disabled={advancing}>
            {advancing ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Trophy className="h-4 w-4 mr-1" />}
            Advance to {path.target_level}
          </Button>
        )}
      </div>

      {path.generation_source === "curriculum" && (
        <div
          role="status"
          className="flex flex-wrap items-center gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-700 dark:text-amber-300"
        >
          <AlertCircle className="h-4 w-4 shrink-0" />
          <span className="flex-1">
            This plan is the default curriculum — the model didn&apos;t answer when it
            was generated.
          </span>
          <Button size="sm" variant="outline" onClick={generatePath} disabled={loading}>
            {loading ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5 mr-1" />
            )}
            {loading ? "Regenerating…" : "Regenerate"}
          </Button>
        </div>
      )}

      <Progress value={percent} />

      {error && (
        <div className="flex items-center gap-2 p-3 rounded-md bg-destructive/10 text-destructive text-sm">
          <AlertCircle className="h-4 w-4" />
          {error}
          <Button variant="ghost" size="sm" className="ml-auto" onClick={() => setError("")}>
            Dismiss
          </Button>
        </div>
      )}

      <div className="space-y-3">
        {path.path_lessons.map((lesson, idx) => {
          const isLocked = !lesson.completed && idx > 0 && !path.path_lessons[idx - 1]?.completed
          return (
            <Card
              key={lesson.id}
              role="button"
              tabIndex={isLocked ? -1 : 0}
              onKeyDown={(e) => {
                if (!isLocked && (e.key === "Enter" || e.key === " ")) {
                  e.preventDefault()
                  openLesson(lesson)
                }
              }}
              aria-disabled={isLocked}
              className={`hover:border-primary/50 transition-colors ${
                lesson.completed ? "opacity-75" : isLocked ? "opacity-50" : "cursor-pointer"
              }`}
              onClick={() => {
                if (!isLocked) openLesson(lesson)
              }}
            >
              <CardContent className="p-4 flex items-start gap-4">
                <div className="text-2xl pt-0.5">{typeIcons[lesson.lesson_type] || "📚"}</div>
                <div className="flex-1">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-semibold text-base">{lesson.topic}</span>
                      <Badge variant="secondary" className="text-[10px] capitalize">
                        {lesson.lesson_type}
                      </Badge>
                    </div>
                    {lesson.completed ? (
                      <div className="flex items-center gap-1.5 text-xs font-semibold text-green-500 bg-green-500/10 px-2.5 py-1 rounded-full border border-green-500/20 shrink-0">
                        <CheckCircle2 className="h-3.5 w-3.5" />
                        <span>Completed</span>
                      </div>
                    ) : isLocked ? (
                      <div className="flex items-center gap-1 text-xs text-muted-foreground bg-muted px-2.5 py-1 rounded-full shrink-0">
                        <Lock className="h-3 w-3" />
                        <span>Locked</span>
                      </div>
                    ) : (
                      <Button
                        size="sm"
                        variant="default"
                        className="gap-1 text-xs shrink-0"
                        onClick={(e) => {
                          e.stopPropagation()
                          openLesson(lesson)
                        }}
                      >
                        Start Lesson
                        <ArrowRight className="h-3.5 w-3.5" />
                      </Button>
                    )}
                  </div>
                  <p className="text-sm text-muted-foreground mt-1">{lesson.description}</p>
                  {!isLocked && (
                    <p className="text-xs text-muted-foreground/80 mt-2">
                      {lesson.completed ? "Click to review lesson material →" : "Click to start this lesson →"}
                    </p>
                  )}
                </div>
              </CardContent>
            </Card>
          )
        })}
      </div>
    </div>
  )
}
