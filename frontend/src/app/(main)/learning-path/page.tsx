"use client"

import { useState, useEffect } from "react"
import { useRouter } from "next/navigation"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Progress } from "@/components/ui/progress"
import { Skeleton } from "@/components/ui/skeleton"
import { api, ApiError } from "@/lib/api"
import type { LearningPath, PathLesson, PathLessonDetail } from "@/lib/types"
import { MapPin, CheckCircle2, Lock, Sparkles, AlertCircle, Loader2, Trophy, XCircle } from "lucide-react"

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
  const [path, setPath] = useState<LearningPath | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")
  const [advancing, setAdvancing] = useState(false)
  const [completing, setCompleting] = useState<string | null>(null)
  // Lesson detail view: opened by clicking a card. The stub (a PathLesson,
  // structurally identical) renders immediately; the detail request fills in
  // the generated content (explanation/examples/exercises).
  const [activeLesson, setActiveLesson] = useState<PathLessonDetail | null>(null)
  const [lessonLoading, setLessonLoading] = useState(false)
  const [exerciseAnswers, setExerciseAnswers] = useState<Record<number, string>>({})
  const [exerciseResults, setExerciseResults] = useState<Record<number, { correct: boolean; correct_answer: string; explanation: string }>>({})
  const [checking, setChecking] = useState<Record<number, boolean>>({})

  const generatePath = async () => {
    setLoading(true)
    setError("")
    try {
      const p = await api.learningPath.generate()
      setPath(p)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to generate path")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    // All setState calls happen after await — no synchronous cascading
    // renders (loading already starts as true for the initial fetch).
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
    return () => { cancelled = true }
  }, [])

  const completeLesson = async (lesson: PathLesson) => {
    if (!path || lesson.completed) return
    setCompleting(lesson.id)
    try {
      const p = await api.learningPath.completeLesson(path.id, lesson.id)
      setPath(p)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to complete lesson")
    } finally {
      setCompleting(null)
    }
  }

  // Open the lesson detail: renders the card immediately, then loads (or
  // lazily generates) the interactive content.
  const openLesson = async (lesson: PathLesson) => {
    if (!path) return
    setError("")
    setExerciseAnswers({})
    setExerciseResults({})
    setActiveLesson(lesson)
    setLessonLoading(true)
    try {
      const detail = await api.learningPath.lessonDetail(path.id, lesson.id)
      setActiveLesson(detail)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load lesson")
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
    try {
      const result = await api.learningPath.checkLessonExercise(path.id, activeLesson.id, index, answer)
      setExerciseResults((prev) => ({ ...prev, [index]: result }))
    } catch (err: unknown) {
      setExerciseResults((prev) => ({
        ...prev,
        [index]: { correct: false, correct_answer: "", explanation: err instanceof Error ? err.message : "Check failed" },
      }))
    } finally {
      setChecking((prev) => ({ ...prev, [index]: false }))
    }
  }

  // Complete from the detail view — same endpoint as the card button; the
  // full path response refreshes counters/progress and closes the view.
  const completeFromDetail = async () => {
    if (!path || !activeLesson || activeLesson.completed) return
    setCompleting(activeLesson.id)
    try {
      const p = await api.learningPath.completeLesson(path.id, activeLesson.id)
      setPath(p)
      setActiveLesson(null)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to complete lesson")
    } finally {
      setCompleting(null)
    }
  }

  const advance = async () => {
    if (!path) return
    setAdvancing(true)
    try {
      const p = await api.learningPath.advance(path.id)
      setPath(p)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to advance")
    } finally {
      setAdvancing(false)
    }
  }

  if (loading) {
    return (
      <div className="max-w-3xl mx-auto p-6 space-y-6">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-4 w-3/4" />
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
          <Button onClick={() => router.push("/assessment")}>
            Go to Assessment
          </Button>
          <Button variant="outline" onClick={generatePath}>
            <Sparkles className="h-4 w-4 mr-1" />
            Generate Path from Current Level
          </Button>
        </div>
      </div>
    )
  }

  // Lesson detail view — content is generated lazily on first open.
  if (path && activeLesson) {
    const content = activeLesson.content
    const exercises = content?.exercises ?? []
    const examples = content?.examples ?? []
    const allAnswered = exercises.length > 0 && exercises.every((_, i) => exerciseResults[i])

    return (
      <div className="max-w-2xl mx-auto p-4 sm:p-6 space-y-6">
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <Button variant="ghost" onClick={() => setActiveLesson(null)}>
            ← Back to Path
          </Button>
          {activeLesson.completed ? (
            <CheckCircle2 className="h-5 w-5 text-green-400" />
          ) : (
            <Button
              size="sm"
              onClick={completeFromDetail}
              disabled={completing === activeLesson.id || (exercises.length > 0 && !allAnswered)}
              title={
                exercises.length > 0 && !allAnswered
                  ? "Finish the exercises to mark the lesson complete"
                  : undefined
              }
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
            <Button variant="ghost" size="sm" onClick={() => setError("")}>Dismiss</Button>
          </div>
        )}

        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-2xl">{typeIcons[activeLesson.lesson_type] || "📚"}</span>
            <h1 className="text-2xl font-bold">{activeLesson.topic}</h1>
            <Badge variant="secondary">{activeLesson.lesson_type}</Badge>
          </div>
          <p className="text-sm text-muted-foreground mt-1">{activeLesson.description}</p>
        </div>

        {lessonLoading || !content ? (
          <div className="space-y-4">
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-40 w-full" />
          </div>
        ) : (
          <>
            {content.explanation && (
              <Card>
                <CardHeader>
                  <CardTitle className="text-lg">Explanation</CardTitle>
                  <CardDescription>Targeted at your level: {path.current_level} → {path.target_level}</CardDescription>
                </CardHeader>
                <CardContent>
                  <p className="text-sm">{content.explanation}</p>
                </CardContent>
              </Card>
            )}

            {examples.length > 0 && (
              <Card>
                <CardHeader>
                  <CardTitle className="text-lg">Examples</CardTitle>
                </CardHeader>
                <CardContent>
                  <ul className="space-y-2">
                    {examples.map((ex, i) => (
                      <li key={i} className="text-sm bg-secondary rounded-lg p-3">{ex}</li>
                    ))}
                  </ul>
                </CardContent>
              </Card>
            )}

            <div className="space-y-4">
              <h2 className="text-xl font-semibold">Exercises</h2>
              {exercises.length === 0 ? (
                <p className="text-sm text-muted-foreground">This lesson has no exercises.</p>
              ) : (
                exercises.map((ex, i) => {
                  const result = exerciseResults[i]
                  const options = Array.isArray(ex.options) ? ex.options : undefined
                  const isMultipleChoice = ex.type === "multiple_choice" && !!options?.length
                  return (
                    <Card key={i}>
                      <CardContent className="p-4 space-y-3">
                        <p className="font-medium">{ex.question}</p>
                        {!result ? (
                          isMultipleChoice ? (
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
                                placeholder="Your answer"
                                value={exerciseAnswers[i] || ""}
                                onChange={(e) => setExerciseAnswers((prev) => ({ ...prev, [i]: e.target.value }))}
                                onKeyDown={(e) => e.key === "Enter" && checkExercise(i)}
                              />
                              <Button size="sm" onClick={() => checkExercise(i)} disabled={!exerciseAnswers[i] || checking[i]}>
                                {checking[i] ? <Loader2 className="h-4 w-4 animate-spin" /> : "Check"}
                              </Button>
                            </div>
                          )
                        ) : (
                          <div className={`flex items-start gap-2 text-sm ${result.correct ? "text-green-400" : "text-destructive"}`}>
                            {result.correct ? <CheckCircle2 className="h-4 w-4 mt-0.5" /> : <XCircle className="h-4 w-4 mt-0.5" />}
                            <div>
                              <p>{result.explanation}</p>
                              {!result.correct && result.correct_answer && (
                                <p className="text-muted-foreground">Correct answer: <strong>{result.correct_answer}</strong></p>
                              )}
                            </div>
                          </div>
                        )}
                      </CardContent>
                    </Card>
                  )
                })
              )}
              {exercises.length > 0 && !allAnswered && (
                <p className="text-xs text-muted-foreground">Finish the exercises to mark the lesson complete — that counts toward your progress to the next level.</p>
              )}
            </div>
          </>
        )}
      </div>
    )
  }

  // Clamp — paths created before the lesson-cap fix could exceed 100%.
  const percent = Math.min(100, Math.round(path.lessons_completed / path.lessons_required * 100))
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
            From <strong>{path.current_level}</strong> to <strong>{path.target_level}</strong> · {path.lessons_completed} of {path.lessons_required} lessons completed
          </p>
        </div>
        {canAdvance && (
          <Button onClick={advance} disabled={advancing}>
            {advancing ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Trophy className="h-4 w-4 mr-1" />}
            Advance to {path.target_level}
          </Button>
        )}
      </div>

      <Progress value={percent} />

      {error && (
        <div className="flex items-center gap-2 p-3 rounded-md bg-destructive/10 text-destructive text-sm">
          <AlertCircle className="h-4 w-4" />
          {error}
          <Button variant="ghost" size="sm" className="ml-auto" onClick={() => setError("")}>Dismiss</Button>
        </div>
      )}

      <div className="space-y-3">
        {path.path_lessons.map((lesson, idx) => {
          const isLocked = !lesson.completed && idx > 0 && !path.path_lessons[idx - 1]?.completed
          return (
            <Card
              key={lesson.id}
              className={`hover:border-primary/50 transition-colors ${lesson.completed ? "opacity-70" : isLocked ? "opacity-50" : "cursor-pointer"}`}
              onClick={() => {
                if (!isLocked) openLesson(lesson)
              }}
            >
              <CardContent className="p-4 flex items-start gap-4">
                <div className="text-2xl">{typeIcons[lesson.lesson_type] || "📚"}</div>
                <div className="flex-1">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="font-semibold">{lesson.topic}</span>
                      <Badge variant="secondary" className="text-[10px]">{lesson.lesson_type}</Badge>
                    </div>
                    {lesson.completed ? (
                      <CheckCircle2 className="h-5 w-5 text-green-400" />
                    ) : isLocked ? (
                      <Lock className="h-5 w-5 text-muted-foreground" />
                    ) : (
                      <Button
                        size="sm"
                        onClick={(e) => {
                          // Fallback shortcut — the full lesson experience
                          // opens by clicking the card.
                          e.stopPropagation()
                          completeLesson(lesson)
                        }}
                        disabled={completing === lesson.id}
                      >
                        {completing === lesson.id ? <Loader2 className="h-4 w-4 animate-spin" /> : "Complete"}
                      </Button>
                    )}
                  </div>
                  <p className="text-sm text-muted-foreground mt-1">{lesson.description}</p>
                  {!isLocked && !lesson.completed && (
                    <p className="text-xs text-muted-foreground mt-2">Click the card to open the lesson →</p>
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
