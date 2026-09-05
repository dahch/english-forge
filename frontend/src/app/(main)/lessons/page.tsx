"use client"

import { useState, useEffect } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Skeleton } from "@/components/ui/skeleton"
import { api, type LibraryLesson } from "@/lib/api"
import type { GeneratedLesson } from "@/lib/types"
import { GraduationCap, CheckCircle2, XCircle, Sparkles, AlertCircle, Loader2, BookOpen, Check } from "lucide-react"

export default function LessonsPage() {
  const [libraryLessons, setLibraryLessons] = useState<LibraryLesson[]>([])
  const [generatedLessons, setGeneratedLessons] = useState<GeneratedLesson[]>([])
  const [loadError, setLoadError] = useState("")
  const [activeLesson, setActiveLesson] = useState<LibraryLesson | GeneratedLesson | null>(null)
  const [exerciseAnswers, setExerciseAnswers] = useState<Record<number, string>>({})
  const [exerciseResults, setExerciseResults] = useState<Record<number, { correct: boolean; correct_answer: string; explanation: string }>>({})
  const [checking, setChecking] = useState<Record<number, boolean>>({})
  const [generating, setGenerating] = useState(false)
  const [completing, setCompleting] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    loadAll()
  }, [])

  const loadAll = async () => {
    setLoading(true)
    try {
      const [lib, gen] = await Promise.all([api.lessons.library(), api.lessons.generated()])
      setLibraryLessons(lib)
      setGeneratedLessons(gen)
    } catch (err: unknown) {
      setLoadError(err instanceof Error ? err.message : "Failed to load lessons")
    } finally {
      setLoading(false)
    }
  }

  const checkExercise = async (index: number) => {
    if (!activeLesson) return
    const answer = exerciseAnswers[index] || ""
    if (!answer.trim()) return

    setChecking((prev) => ({ ...prev, [index]: true }))
    try {
      const result = isGeneratedLesson(activeLesson)
        ? await api.lessons.checkGeneratedExercise(activeLesson.id, index, answer)
        : await api.lessons.checkExercise(activeLesson.id, index, answer)
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

  const generateLesson = async () => {
    setGenerating(true)
    setLoadError("")
    try {
      const lesson = await api.lessons.generate()
      setGeneratedLessons((prev) => [lesson, ...prev])
      setActiveLesson(lesson)
      setExerciseAnswers({})
      setExerciseResults({})
    } catch (err: unknown) {
      setLoadError(err instanceof Error ? err.message : "Generation failed")
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
    } catch (err: unknown) {
      setLoadError(err instanceof Error ? err.message : "Failed to mark complete")
    } finally {
      setCompleting(null)
    }
  }

  const isGeneratedLesson = (lesson: LibraryLesson | GeneratedLesson): lesson is GeneratedLesson =>
    "completed" in lesson

  // Library list items lack exercise data — fetch the full detail on open.
  const openLibraryLesson = async (lesson: LibraryLesson) => {
    setActiveLesson(lesson)
    setExerciseAnswers({})
    setExerciseResults({})
    try {
      const detail = await api.lessons.get(lesson.id)
      setActiveLesson((prev) => (prev && prev.id === lesson.id ? detail : prev))
    } catch (err: unknown) {
      setLoadError(err instanceof Error ? err.message : "Failed to load lesson")
      setActiveLesson(null)
    }
  }

  if (activeLesson) {
    return (
      <div className="max-w-2xl mx-auto p-6 space-y-6">
        <div className="flex items-center justify-between">
          <Button variant="ghost" onClick={() => setActiveLesson(null)}>
            ← Back to Lessons
          </Button>
          {isGeneratedLesson(activeLesson) && !activeLesson.completed && (
            <Button size="sm" onClick={() => completeGeneratedLesson(activeLesson)} disabled={completing === activeLesson.id}>
              {completing === activeLesson.id ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Check className="h-4 w-4 mr-1" />}
              Mark Complete
            </Button>
          )}
        </div>
        <div>
          <div className="flex items-center gap-2 mb-2">
            <h1 className="text-2xl font-bold">{activeLesson.title}</h1>
            {"level" in activeLesson && activeLesson.level && <Badge>{activeLesson.level}</Badge>}
          </div>
          <p className="text-muted-foreground">{activeLesson.explanation}</p>
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
          {activeLesson.exercises ? (
            activeLesson.exercises.map((ex, i) => {
              const result = exerciseResults[i]
              return (
                <Card key={i}>
                  <CardContent className="p-4 space-y-3">
                    <p className="font-medium">{ex.question}</p>
                    {!result ? (
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
      <div className="max-w-4xl mx-auto p-6 space-y-6">
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
    <div className="max-w-4xl mx-auto p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <GraduationCap className="h-6 w-6 text-primary" />
          Lessons
        </h1>
        <Button onClick={generateLesson} disabled={generating}>
          <Sparkles className="h-4 w-4 mr-1" />
          {generating ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : null}
          {generating ? "Generating..." : "Generate Personalized Lesson"}
        </Button>
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

      <Tabs defaultValue="library">
        <TabsList>
          <TabsTrigger value="library" className="gap-1"><BookOpen className="h-4 w-4" /> Library</TabsTrigger>
          <TabsTrigger value="generated" className="gap-1"><Sparkles className="h-4 w-4" /> Generated ({generatedLessons.length})</TabsTrigger>
        </TabsList>

        <TabsContent value="library" className="mt-4">
          <div className="grid gap-4 md:grid-cols-2">
            {libraryLessons.map((lesson) => (
              <Card key={lesson.id} className="cursor-pointer hover:border-primary/50 transition-colors" onClick={() => openLibraryLesson(lesson)}>
                <CardHeader>
                  <div className="flex items-center justify-between">
                    <CardTitle className="text-lg">{lesson.title}</CardTitle>
                    <Badge variant="secondary">{lesson.level}</Badge>
                  </div>
                  <CardDescription>{lesson.topic}</CardDescription>
                </CardHeader>
                <CardContent>
                  <p className="text-sm text-muted-foreground line-clamp-2">{lesson.explanation}</p>
                  <p className="text-xs text-muted-foreground mt-2">{lesson.exercise_count} exercises</p>
                </CardContent>
              </Card>
            ))}
          </div>
        </TabsContent>

        <TabsContent value="generated" className="mt-4">
          {generatedLessons.length === 0 ? (
            <div className="text-center py-12 text-muted-foreground">
              <Sparkles className="h-12 w-12 mx-auto mb-4 opacity-50" />
              <p>No personalized lessons yet. Generate one based on your mistakes!</p>
            </div>
          ) : (
            <div className="grid gap-4 md:grid-cols-2">
              {generatedLessons.map((lesson) => (
                <Card key={lesson.id} className={`cursor-pointer hover:border-primary/50 transition-colors ${lesson.completed ? "opacity-70" : ""}`} onClick={() => { setActiveLesson(lesson); setExerciseAnswers({}); setExerciseResults({}) }}>
                  <CardHeader>
                    <div className="flex items-center justify-between">
                      <CardTitle className="text-lg flex items-center gap-2">
                        {lesson.title}
                        {lesson.completed && <Check className="h-4 w-4 text-green-400" />}
                      </CardTitle>
                      {lesson.level && <Badge variant="secondary">{lesson.level}</Badge>}
                    </div>
                    <CardDescription>{lesson.based_on_errors || "Personalized"}</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <p className="text-sm text-muted-foreground line-clamp-2">{lesson.explanation}</p>
                    <p className="text-xs text-muted-foreground mt-2">{lesson.exercises?.length || 0} exercises</p>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </TabsContent>
      </Tabs>
    </div>
  )
}
