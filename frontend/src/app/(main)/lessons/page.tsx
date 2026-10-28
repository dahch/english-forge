"use client"

import { useEffect, useRef, useState } from "react"
import Link from "next/link"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Skeleton } from "@/components/ui/skeleton"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Label } from "@/components/ui/label"
import { api, type LibraryLesson } from "@/lib/api"
import { useToast } from "@/components/feedback/toast"
import { formatElapsed, useElapsed } from "@/lib/use-elapsed"
import type { GeneratedLesson, LearningPath } from "@/lib/types"
import {
  GraduationCap,
  CheckCircle2,
  XCircle,
  Sparkles,
  AlertCircle,
  Loader2,
  BookOpen,
  Check,
  Route,
  Lock,
  ExternalLink,
  ChevronRight,
  Plus,
  RefreshCw,
  X,
} from "lucide-react"

const CEFR_LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"] as const

const TOPIC_SUGGESTIONS = [
  "Job Interview Phrasal Verbs",
  "Small Talk & Icebreakers",
  "Past Continuous vs Past Simple",
  "Conditionals (If & Would)",
  "Business Email Writing",
  "Ordering Food & Restaurants",
]

export default function LessonsPage() {
  const { toast } = useToast()
  const [libraryLessons, setLibraryLessons] = useState<LibraryLesson[]>([])
  const [generatedLessons, setGeneratedLessons] = useState<GeneratedLesson[]>([])
  const [learningPath, setLearningPath] = useState<LearningPath | null>(null)
  const [userLevel, setUserLevel] = useState<string>("B1")
  const [showGenerateModal, setShowGenerateModal] = useState(false)
  const [customTopic, setCustomTopic] = useState("")
  const [customLevel, setCustomLevel] = useState("B1")
  const [loadError, setLoadError] = useState("")
  const [activeLesson, setActiveLesson] = useState<LibraryLesson | GeneratedLesson | null>(null)
  const [exerciseAnswers, setExerciseAnswers] = useState<Record<number, string>>({})
  const [exerciseResults, setExerciseResults] = useState<Record<number, { correct: boolean; correct_answer: string; explanation: string }>>({})
  const [exerciseErrors, setExerciseErrors] = useState<Record<number, string>>({})
  const [checking, setChecking] = useState<Record<number, boolean>>({})
  const [generating, setGenerating] = useState(false)
  const [completing, setCompleting] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [generatedHasMore, setGeneratedHasMore] = useState(false)
  const [loadingMoreGenerated, setLoadingMoreGenerated] = useState(false)
  const generatedOffsetRef = useRef(0)
  const generatingSeconds = useElapsed(generating)
  const GENERATED_PAGE_SIZE = 30

  useEffect(() => {
    loadAll()
  }, [])

  // Close modal on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && showGenerateModal && !generating) {
        setShowGenerateModal(false)
      }
    }
    window.addEventListener("keydown", handleKeyDown)
    return () => window.removeEventListener("keydown", handleKeyDown)
  }, [showGenerateModal, generating])

  const loadAll = async () => {
    setLoading(true)
    try {
      const [libRes, genRes, pathRes, meRes] = await Promise.allSettled([
        api.lessons.library(),
        api.lessons.generated(GENERATED_PAGE_SIZE, 0),
        api.learningPath.current(),
        api.auth.me(),
      ])

      if (libRes.status === "fulfilled") setLibraryLessons(libRes.value)
      if (genRes.status === "fulfilled") {
        setGeneratedLessons(genRes.value)
        generatedOffsetRef.current = genRes.value.length
        setGeneratedHasMore(genRes.value.length === GENERATED_PAGE_SIZE)
      }
      if (pathRes.status === "fulfilled") setLearningPath(pathRes.value)
      if (meRes.status === "fulfilled") {
        const lvl = meRes.value.current_level || "B1"
        setUserLevel(lvl)
        setCustomLevel(lvl)
      }
    } catch (err: unknown) {
      setLoadError(err instanceof Error ? err.message : "Failed to load lessons")
    } finally {
      setLoading(false)
    }
  }

  const loadMoreGenerated = async () => {
    setLoadingMoreGenerated(true)
    try {
      const page = await api.lessons.generated(
        GENERATED_PAGE_SIZE,
        generatedOffsetRef.current,
      )
      setGeneratedLessons((prev) => {
        const seen = new Set(prev.map((lesson) => lesson.id))
        return [...prev, ...page.filter((lesson) => !seen.has(lesson.id))]
      })
      generatedOffsetRef.current += page.length
      setGeneratedHasMore(page.length === GENERATED_PAGE_SIZE)
    } catch (err: unknown) {
      toast({
        title: "Could not load more lessons",
        description: err instanceof Error ? err.message : "Try again.",
        variant: "error",
      })
    } finally {
      setLoadingMoreGenerated(false)
    }
  }

  const checkExercise = async (index: number, answerOverride?: string) => {
    if (!activeLesson) return
    const answer = answerOverride ?? (exerciseAnswers[index] || "")
    if (!answer.trim()) return

    setChecking((prev) => ({ ...prev, [index]: true }))
    setExerciseErrors((prev) => ({ ...prev, [index]: "" }))
    try {
      const result = isGeneratedLesson(activeLesson)
        ? await api.lessons.checkGeneratedExercise(activeLesson.id, index, answer)
        : await api.lessons.checkExercise(activeLesson.id, index, answer)
      setExerciseResults((prev) => ({ ...prev, [index]: result }))
    } catch (err: unknown) {
      setExerciseErrors((prev) => ({
        ...prev,
        [index]: err instanceof Error ? err.message : "Check failed. Please try again.",
      }))
    } finally {
      setChecking((prev) => ({ ...prev, [index]: false }))
    }
  }

  const generateLessonOnDemand = async (topicOverride?: string, levelOverride?: string) => {
    setGenerating(true)
    setLoadError("")
    try {
      const topic = topicOverride !== undefined ? topicOverride : customTopic
      const level = levelOverride || customLevel || userLevel || "B1"
      const lesson = await api.lessons.generate({
        topic: topic.trim() || undefined,
        level,
      })
      setGeneratedLessons((prev) => [lesson, ...prev.filter((item) => item.id !== lesson.id)])
      // The new row shifts server-side pagination by one.
      generatedOffsetRef.current += 1
      setActiveLesson(lesson)
      setExerciseAnswers({})
      setExerciseResults({})
      setExerciseErrors({})
      setShowGenerateModal(false)
      setCustomTopic("")
      if (lesson.source === "fallback") {
        toast({
          title: "Base lesson generated",
          description:
            "The model didn't answer in time, so a default lesson for this level was saved. Try Regenerate for a personalized one.",
          variant: "info",
        })
      } else {
        toast({ title: "Lesson ready", description: lesson.title, variant: "success" })
      }
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Lesson generation failed"
      setLoadError(message)
      toast({ title: "Could not generate the lesson", description: message, variant: "error" })
    } finally {
      setGenerating(false)
    }
  }

  const completeGeneratedLesson = async (lesson: GeneratedLesson) => {
    if (lesson.completed) return
    setCompleting(lesson.id)
    try {
      await api.lessons.completeGenerated(lesson.id, true)
      setGeneratedLessons((prev) =>
        prev.map((l) => (l.id === lesson.id ? { ...l, completed: true, completed_at: new Date().toISOString() } : l))
      )
      setActiveLesson((prev) => (prev && prev.id === lesson.id ? { ...prev, completed: true } : prev))
      toast({ title: "Lesson completed", variant: "success" })
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to mark complete"
      setLoadError(message)
      toast({ title: "Could not mark the lesson complete", description: message, variant: "error" })
    } finally {
      setCompleting(null)
    }
  }

  const toggleLibraryLessonComplete = async (lesson: LibraryLesson) => {
    const nextStatus = !lesson.completed
    setCompleting(lesson.id)
    try {
      await api.lessons.completeLibrary(lesson.id, nextStatus)
      setLibraryLessons((prev) =>
        prev.map((l) => (l.id === lesson.id ? { ...l, completed: nextStatus } : l))
      )
      setActiveLesson((prev) => (prev && prev.id === lesson.id ? { ...prev, completed: nextStatus } : prev))
    } catch (err: unknown) {
      setLoadError(err instanceof Error ? err.message : "Failed to update lesson completion")
    } finally {
      setCompleting(null)
    }
  }

  const isGeneratedLesson = (lesson: LibraryLesson | GeneratedLesson): lesson is GeneratedLesson =>
    "completed" in lesson && "based_on_errors" in lesson

  const openLibraryLesson = async (lesson: LibraryLesson) => {
    setActiveLesson(lesson)
    setExerciseAnswers({})
    setExerciseResults({})
    setExerciseErrors({})
    try {
      const detail = await api.lessons.get(lesson.id)
      setActiveLesson((prev) => (prev && prev.id === lesson.id ? { ...detail, completed: lesson.completed } : prev))
    } catch (err: unknown) {
      setLoadError(err instanceof Error ? err.message : "Failed to load lesson detail")
      setActiveLesson(null)
    }
  }

  if (activeLesson) {
    const isGen = isGeneratedLesson(activeLesson)
    return (
      <div className="max-w-2xl mx-auto p-4 sm:p-6 space-y-6">
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <Button variant="ghost" onClick={() => setActiveLesson(null)}>
            ← Back to Lessons
          </Button>

          {isGen ? (
            <div className="flex items-center gap-2">
              {activeLesson.source === "fallback" && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() =>
                    generateLessonOnDemand(
                      activeLesson.topic ?? undefined,
                      activeLesson.level ?? undefined,
                    )
                  }
                  disabled={generating}
                >
                  {generating ? (
                    <Loader2 className="h-4 w-4 animate-spin mr-1" />
                  ) : (
                    <RefreshCw className="h-4 w-4 mr-1" />
                  )}
                  {generating ? `Regenerating… ${formatElapsed(generatingSeconds)}` : "Regenerate"}
                </Button>
              )}
              <Button
                size="sm"
                variant={activeLesson.completed ? "outline" : "default"}
                onClick={() => completeGeneratedLesson(activeLesson)}
                disabled={completing === activeLesson.id || activeLesson.completed}
              >
                {completing === activeLesson.id ? (
                  <Loader2 className="h-4 w-4 animate-spin mr-1" />
                ) : (
                  <Check className={`h-4 w-4 mr-1 ${activeLesson.completed ? "text-emerald-600 dark:text-emerald-400" : ""}`} />
                )}
                {activeLesson.completed ? "Completed" : "Mark Complete"}
              </Button>
            </div>
          ) : (
            <Button
              size="sm"
              variant={activeLesson.completed ? "outline" : "default"}
              onClick={() => toggleLibraryLessonComplete(activeLesson as LibraryLesson)}
              disabled={completing === activeLesson.id}
            >
              {completing === activeLesson.id ? (
                <Loader2 className="h-4 w-4 animate-spin mr-1" />
              ) : (
                <Check className={`h-4 w-4 mr-1 ${activeLesson.completed ? "text-emerald-600 dark:text-emerald-400" : ""}`} />
              )}
              {activeLesson.completed ? "Completed (Click to unmark)" : "Mark Complete"}
            </Button>
          )}
        </div>

        <div>
          <div className="flex items-center gap-2 mb-2">
            <h1 className="text-2xl font-bold">{activeLesson.title}</h1>
            {"level" in activeLesson && activeLesson.level && <Badge>{activeLesson.level}</Badge>}
            {isGen && activeLesson.source === "fallback" && (
              <Badge
                variant="outline"
                className="gap-1 border-amber-500/40 text-amber-600 dark:text-amber-400"
              >
                <AlertCircle className="h-3 w-3" />
                Base lesson
              </Badge>
            )}
            {activeLesson.completed && (
              <Badge variant="outline" className="text-emerald-600 dark:text-emerald-400 border-emerald-500/30 gap-1">
                <Check className="h-3 w-3" /> Done
              </Badge>
            )}
          </div>
          <p className="text-muted-foreground">{activeLesson.explanation}</p>
          {isGen && activeLesson.source === "fallback" && (
            <p className="mt-1 text-xs text-amber-600 dark:text-amber-400">
              The model didn&apos;t answer, so this is the default lesson for this
              level — not personalized. Use Regenerate to try again.
            </p>
          )}
        </div>

        {activeLesson.examples && activeLesson.examples.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle className="text-lg">Examples</CardTitle>
            </CardHeader>
            <CardContent>
              <ul className="space-y-2">
                {activeLesson.examples.map((ex, i) => (
                  <li key={i} className="text-sm bg-secondary rounded-lg p-3">{ex}</li>
                ))}
              </ul>
            </CardContent>
          </Card>
        )}

        <div className="space-y-4">
          <h2 className="text-xl font-semibold">Exercises</h2>
          {activeLesson.exercises && activeLesson.exercises.length > 0 ? (
            activeLesson.exercises.map((ex, i) => {
              const result = exerciseResults[i]
              const options = "options" in ex && Array.isArray(ex.options) ? ex.options : undefined
              const isMultipleChoice = ex.type === "multiple_choice" && !!options?.length
              return (
                <Card key={i}>
                  <CardContent className="p-4 space-y-3">
                    <p className="font-medium">{ex.question}</p>
                    {!result ? (
                      <div className="space-y-2">
                        {isMultipleChoice ? (
                          <div className="flex flex-wrap gap-2" role="group" aria-label={`Options for exercise ${i + 1}`}>
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
                              placeholder="Your answer"
                              aria-label={`Your answer for exercise ${i + 1}: ${ex.question}`}
                              value={exerciseAnswers[i] || ""}
                              onChange={(e) => setExerciseAnswers((prev) => ({ ...prev, [i]: e.target.value }))}
                              onKeyDown={(e) => e.key === "Enter" && checkExercise(i)}
                            />
                            <Button size="sm" onClick={() => checkExercise(i)} disabled={!exerciseAnswers[i] || checking[i]}>
                              {checking[i] ? <Loader2 className="h-4 w-4 animate-spin" /> : "Check"}
                            </Button>
                          </div>
                        )}
                        {exerciseErrors[i] && (
                          <p role="alert" className="text-xs text-destructive flex items-center gap-1 mt-1">
                            <AlertCircle className="h-3.5 w-3.5 shrink-0" />
                            <span>{exerciseErrors[i]}</span>
                          </p>
                        )}
                      </div>
                    ) : (
                      <div
                        role="status"
                        aria-live="polite"
                        className={`flex items-start gap-2 text-sm ${result.correct ? "text-emerald-600 dark:text-emerald-400 font-medium" : "text-destructive"}`}
                      >
                        {result.correct ? <CheckCircle2 className="h-4 w-4 mt-0.5 shrink-0" /> : <XCircle className="h-4 w-4 mt-0.5 shrink-0" />}
                        <div>
                          <p>{result.explanation}</p>
                          {!result.correct && result.correct_answer && (
                            <p className="text-muted-foreground font-normal mt-1">Correct answer: <strong>{result.correct_answer}</strong></p>
                          )}
                        </div>
                      </div>
                    )}
                  </CardContent>
                </Card>
              )
            })
          ) : isGeneratedLesson(activeLesson) ? (
            <p className="text-sm text-muted-foreground">This lesson has no exercises.</p>
          ) : (
            <Skeleton className="h-10 w-full" />
          )}
        </div>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="max-w-4xl mx-auto p-4 sm:p-6 space-y-6">
        <div className="flex items-center justify-between">
          <Skeleton className="h-8 w-40" />
          <Skeleton className="h-10 w-48" />
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Card key={i}>
              <CardContent className="p-4 space-y-3">
                <Skeleton className="h-5 w-3/4" />
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-4 w-1/2" />
              </CardContent>
            </Card>
          ))}
        </div>
      </div>
    )
  }

  return (
    <div className="max-w-4xl mx-auto p-4 sm:p-6 space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <GraduationCap className="h-6 w-6 text-primary" />
          Lessons Hub
        </h1>
        <div className="flex items-center gap-2">
          <Button onClick={() => setShowGenerateModal(true)} className="gap-1 shadow-sm">
            <Sparkles className="h-4 w-4" />
            Generate Lesson
          </Button>
        </div>
      </div>

      {loadError && (
        <div className="flex items-center gap-2 p-3 rounded-md bg-destructive/10 text-destructive text-sm">
          <AlertCircle className="h-4 w-4" />
          {loadError}
          <Button variant="ghost" size="sm" className="ml-auto" onClick={() => { setLoadError(""); loadAll() }}>
            Retry
          </Button>
        </div>
      )}

      {/* Generate Custom Lesson Dialog */}
      {showGenerateModal && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="generate-lesson-modal-title"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 animate-in fade-in duration-150"
          onClick={(e) => {
            if (e.target === e.currentTarget && !generating) {
              setShowGenerateModal(false)
            }
          }}
        >
          <Card className="w-full max-w-lg shadow-2xl border-primary/20" onClick={(e) => e.stopPropagation()}>
            <CardHeader className="flex flex-row items-start justify-between pb-4">
              <div>
                <CardTitle id="generate-lesson-modal-title" className="text-xl flex items-center gap-2">
                  <Sparkles className="h-5 w-5 text-primary" />
                  Generate Lesson on Any Topic
                </CardTitle>
                <CardDescription>
                  Choose a topic and CEFR level to generate an interactive AI lesson with explanations and exercises.
                </CardDescription>
              </div>
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8 text-muted-foreground hover:text-foreground"
                aria-label="Close dialog"
                onClick={() => !generating && setShowGenerateModal(false)}
                disabled={generating}
              >
                <X className="h-4 w-4" />
              </Button>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="custom-topic-input">Topic or Language Point</Label>
                <Input
                  id="custom-topic-input"
                  autoFocus
                  placeholder="e.g., Job Interview Phrasal Verbs, Past Continuous vs Simple..."
                  value={customTopic}
                  onChange={(e) => setCustomTopic(e.target.value)}
                  disabled={generating}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !generating) {
                      e.preventDefault()
                      generateLessonOnDemand()
                    }
                  }}
                />
              </div>

              <div className="space-y-2">
                <Label className="text-xs text-muted-foreground">Popular Topic Suggestions:</Label>
                <div className="flex flex-wrap gap-1.5">
                  {TOPIC_SUGGESTIONS.map((s) => (
                    <button
                      key={s}
                      type="button"
                      onClick={() => setCustomTopic(s)}
                      disabled={generating}
                      className="text-xs px-2.5 py-1 rounded-full bg-secondary hover:bg-primary/20 text-muted-foreground hover:text-foreground transition-colors"
                    >
                      + {s}
                    </button>
                  ))}
                </div>
              </div>

              <div className="space-y-2">
                <Label htmlFor="custom-level-select">CEFR Target Level</Label>
                <Select value={customLevel} onValueChange={setCustomLevel} disabled={generating}>
                  <SelectTrigger id="custom-level-select">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {CEFR_LEVELS.map((lvl) => (
                      <SelectItem key={lvl} value={lvl}>
                        {lvl} {lvl === userLevel ? "(Your Current Level)" : ""}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="flex justify-end gap-2 pt-2">
                <Button
                  variant="outline"
                  onClick={() => setShowGenerateModal(false)}
                  disabled={generating}
                >
                  Cancel
                </Button>
                <Button
                  onClick={() => generateLessonOnDemand()}
                  disabled={generating}
                  className="gap-2"
                >
                  {generating ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
                  {generating
                    ? `Crafting lesson… ${formatElapsed(generatingSeconds)}`
                    : "Generate Lesson"}
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      <Tabs defaultValue="library">
        <TabsList className="grid grid-cols-3 w-full max-w-md">
          <TabsTrigger value="library" className="gap-1.5">
            <BookOpen className="h-4 w-4" /> Library
          </TabsTrigger>
          <TabsTrigger value="generated" className="gap-1.5">
            <Sparkles className="h-4 w-4" /> Generated ({generatedLessons.length})
          </TabsTrigger>
          <TabsTrigger value="path" className="gap-1.5">
            <Route className="h-4 w-4" /> Learning Path
          </TabsTrigger>
        </TabsList>

        {/* Library Lessons Tab */}
        <TabsContent value="library" className="mt-4">
          <div className="grid gap-4 md:grid-cols-2">
            {libraryLessons.map((lesson) => (
              <Card
                key={lesson.id}
                role="button"
                tabIndex={0}
                aria-label={`Lesson: ${lesson.title}, Level ${lesson.level}, ${lesson.completed ? "Completed" : "Not completed"}`}
                className="cursor-pointer hover:border-primary/50 focus-visible:ring-2 focus-visible:ring-primary focus-visible:outline-none transition-colors relative"
                onClick={() => openLibraryLesson(lesson)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault()
                    openLibraryLesson(lesson)
                  }
                }}
              >
                <CardHeader>
                  <div className="flex items-center justify-between gap-2">
                    <CardTitle className="text-lg flex items-center gap-2">
                      {lesson.title}
                      {lesson.completed && (
                        <CheckCircle2 className="h-4 w-4 text-emerald-600 dark:text-emerald-400 shrink-0" />
                      )}
                    </CardTitle>
                    <Badge variant="secondary">{lesson.level}</Badge>
                  </div>
                  <CardDescription>{lesson.topic}</CardDescription>
                </CardHeader>
                <CardContent>
                  <p className="text-sm text-muted-foreground line-clamp-2">{lesson.explanation}</p>
                  <div className="flex items-center justify-between mt-3 text-xs text-muted-foreground">
                    <span>{lesson.exercise_count} exercises</span>
                    {lesson.completed ? (
                      <Badge variant="outline" className="text-emerald-600 dark:text-emerald-400 border-emerald-500/30 gap-1 text-[11px]">
                        <Check className="h-3 w-3" /> Completed
                      </Badge>
                    ) : (
                      <span className="text-primary/80 font-medium">Practice Now →</span>
                    )}
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        </TabsContent>

        {/* AI Generated Lessons Tab */}
        <TabsContent value="generated" className="mt-4">
          {generatedLessons.length === 0 ? (
            <Card className="text-center py-12">
              <CardContent className="space-y-4">
                <Sparkles className="h-12 w-12 mx-auto text-primary opacity-60" />
                <div>
                  <h3 className="text-lg font-semibold">No Generated Lessons Yet</h3>
                  <p className="text-sm text-muted-foreground mt-1 max-w-md mx-auto">
                    Generate an interactive lesson on any topic you want to master, or let EnglishForge build one based on your speaking mistakes.
                  </p>
                </div>
                <Button onClick={() => setShowGenerateModal(true)} className="gap-2">
                  <Plus className="h-4 w-4" />
                  Generate First Lesson
                </Button>
              </CardContent>
            </Card>
          ) : (
            <div className="space-y-4">
              <div className="grid gap-4 md:grid-cols-2">
              {generatedLessons.map((lesson) => (
                <Card
                  key={lesson.id}
                  role="button"
                  tabIndex={0}
                  aria-label={`Generated Lesson: ${lesson.title}, Level ${lesson.level || "General"}, ${lesson.completed ? "Completed" : "Not completed"}`}
                  className={`cursor-pointer hover:border-primary/50 focus-visible:ring-2 focus-visible:ring-primary focus-visible:outline-none transition-colors ${lesson.completed ? "opacity-80" : ""}`}
                  onClick={() => {
                    setActiveLesson(lesson)
                    setExerciseAnswers({})
                    setExerciseResults({})
                    setExerciseErrors({})
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault()
                      setActiveLesson(lesson)
                      setExerciseAnswers({})
                      setExerciseResults({})
                      setExerciseErrors({})
                    }
                  }}
                >
                  <CardHeader>
                    <div className="flex items-center justify-between gap-2">
                      <CardTitle className="text-lg flex items-center gap-2">
                        {lesson.title}
                        {lesson.completed && <Check className="h-4 w-4 text-emerald-600 dark:text-emerald-400 shrink-0" />}
                      </CardTitle>
                      {lesson.level && <Badge variant="secondary">{lesson.level}</Badge>}
                    </div>
                    <CardDescription>{lesson.based_on_errors || "Personalized Topic"}</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <p className="text-sm text-muted-foreground line-clamp-2">{lesson.explanation}</p>
                    <div className="flex items-center justify-between mt-3 text-xs text-muted-foreground">
                      <span>{lesson.exercises?.length || 0} exercises</span>
                      {lesson.completed ? (
                        <Badge variant="outline" className="text-emerald-600 dark:text-emerald-400 border-emerald-500/30 gap-1 text-[11px]">
                          <Check className="h-3 w-3" /> Completed
                        </Badge>
                      ) : (
                        <span className="text-primary/80 font-medium">Open Lesson →</span>
                      )}
                    </div>
                  </CardContent>
                </Card>
              ))}
              </div>

              {generatedHasMore && (
                <div className="text-center">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => void loadMoreGenerated()}
                    disabled={loadingMoreGenerated}
                  >
                    {loadingMoreGenerated && (
                      <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                    )}
                    {loadingMoreGenerated ? "Loading…" : "Load more lessons"}
                  </Button>
                </div>
              )}
            </div>
          )}
        </TabsContent>

        {/* Learning Path Curriculum Tab */}
        <TabsContent value="path" className="mt-4">
          {learningPath ? (
            <Card>
              <CardHeader>
                <div className="flex items-center justify-between flex-wrap gap-2">
                  <div>
                    <CardTitle className="text-xl flex items-center gap-2">
                      <Route className="h-5 w-5 text-primary" />
                      Active Curriculum: {learningPath.current_level} → {learningPath.target_level}
                    </CardTitle>
                    <CardDescription className="mt-1">
                      {learningPath.lessons_completed} of {learningPath.lessons_required} lessons completed
                    </CardDescription>
                  </div>
                  <Link href="/learning-path">
                    <Button variant="outline" size="sm" className="gap-1.5">
                      Open Full Learning Path
                      <ExternalLink className="h-3.5 w-3.5" />
                    </Button>
                  </Link>
                </div>
                {/* Visual progress bar */}
                <div
                  role="progressbar"
                  aria-valuenow={Math.round((learningPath.lessons_completed / (learningPath.lessons_required || 1)) * 100)}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-label={`Learning path progress: ${learningPath.lessons_completed} of ${learningPath.lessons_required} lessons completed`}
                  className="w-full bg-secondary rounded-full h-2 mt-4 overflow-hidden"
                >
                  <div
                    className="bg-primary h-full transition-all duration-300 rounded-full"
                    style={{
                      width: `${Math.round((learningPath.lessons_completed / (learningPath.lessons_required || 1)) * 100)}%`,
                    }}
                  />
                </div>
              </CardHeader>
              <CardContent className="space-y-3 pt-2">
                {learningPath.path_lessons && learningPath.path_lessons.length > 0 ? (
                  (() => {
                    const firstIncompleteIdx = learningPath.path_lessons.findIndex((l) => !l.completed)
                    return learningPath.path_lessons.map((pl, idx) => {
                      const isCompleted = pl.completed
                      const isCurrent = !isCompleted && (idx === firstIncompleteIdx || firstIncompleteIdx === -1)
                      const isLocked = !isCompleted && !isCurrent

                      return (
                        <div
                          key={pl.id}
                          className={`flex items-center justify-between p-3 rounded-lg border transition-colors ${
                            isCompleted
                              ? "bg-secondary/40 border-border/50"
                              : isCurrent
                              ? "bg-primary/5 border-primary/40 shadow-sm"
                              : "bg-background/50 border-border/30 opacity-60"
                          }`}
                        >
                          <div className="flex items-center gap-3">
                            <div
                              className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-semibold ${
                                isCompleted
                                  ? "bg-emerald-500/20 text-emerald-600 dark:text-emerald-400"
                                  : isCurrent
                                  ? "bg-primary text-primary-foreground"
                                  : "bg-muted text-muted-foreground"
                              }`}
                            >
                              {isCompleted ? (
                                <Check className="h-3.5 w-3.5" />
                              ) : isLocked ? (
                                <Lock className="h-3 w-3" />
                              ) : (
                                pl.order
                              )}
                            </div>
                            <div>
                              <p className="text-sm font-medium">{pl.topic}</p>
                              <div className="flex items-center gap-2 mt-0.5">
                                <Badge variant="outline" className="text-[10px] px-1.5 py-0 capitalize">
                                  {pl.lesson_type}
                                </Badge>
                                <span className="text-xs text-muted-foreground line-clamp-1">{pl.description}</span>
                              </div>
                            </div>
                          </div>

                          <div className="flex items-center gap-2">
                            {isCompleted ? (
                              <Badge variant="secondary" className="text-xs">Done</Badge>
                            ) : isCurrent ? (
                              <Link href="/learning-path">
                                <Button size="sm" className="h-8 text-xs gap-1">
                                  Resume <ChevronRight className="h-3 w-3" />
                                </Button>
                              </Link>
                            ) : (
                              <Badge variant="outline" className="text-xs text-muted-foreground">Locked</Badge>
                            )}
                          </div>
                        </div>
                      )
                    })
                  })()
                ) : (
                  <p className="text-sm text-muted-foreground text-center py-4">No curriculum lessons loaded.</p>
                )}
              </CardContent>
            </Card>
          ) : (
            <Card className="text-center py-12">
              <CardContent className="space-y-4">
                <Route className="h-12 w-12 mx-auto text-primary opacity-60" />
                <div>
                  <h3 className="text-lg font-semibold">No Active Learning Path</h3>
                  <p className="text-sm text-muted-foreground mt-1 max-w-md mx-auto">
                    Take the diagnostic assessment or generate a tailored CEFR path to unlock a sequential curriculum.
                  </p>
                </div>
                <div className="flex justify-center gap-3">
                  <Link href="/assessment">
                    <Button variant="outline">Take Assessment</Button>
                  </Link>
                  <Link href="/learning-path">
                    <Button>Go to Learning Path</Button>
                  </Link>
                </div>
              </CardContent>
            </Card>
          )}
        </TabsContent>
      </Tabs>
    </div>
  )
}
