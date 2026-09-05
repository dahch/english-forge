"use client"

import { useState, useEffect } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { api, type LibraryLesson, type GeneratedLesson } from "@/lib/api"
import { GraduationCap, CheckCircle2, XCircle, Sparkles, AlertCircle } from "lucide-react"

export default function LessonsPage() {
  const [lessons, setLessons] = useState<LibraryLesson[]>([])
  const [loadError, setLoadError] = useState("")
  const [activeLesson, setActiveLesson] = useState<LibraryLesson | null>(null)
  const [exerciseAnswers, setExerciseAnswers] = useState<Record<number, string>>({})
  const [exerciseResults, setExerciseResults] = useState<Record<number, { correct: boolean; correct_answer: string; explanation: string }>>({})
  const [checking, setChecking] = useState<Record<number, boolean>>({})
  const [generating, setGenerating] = useState(false)
  const [generatedLesson, setGeneratedLesson] = useState<GeneratedLesson | null>(null)

  useEffect(() => {
    api.lessons
      .library()
      .then(setLessons)
      .catch((err: unknown) => setLoadError(err instanceof Error ? err.message : "Failed to load lessons"))
  }, [])

  const checkExercise = async (index: number) => {
    if (!activeLesson) return
    const answer = exerciseAnswers[index] || ""
    if (!answer.trim()) return

    setChecking((prev) => ({ ...prev, [index]: true }))
    try {
      const result = await api.lessons.checkExercise(activeLesson.id, index, answer)
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
    try {
      const lesson = await api.lessons.generate()
      setGeneratedLesson(lesson)
    } catch (err: unknown) {
      setLoadError(err instanceof Error ? err.message : "Generation failed")
    } finally {
      setGenerating(false)
    }
  }

  if (activeLesson) {
    return (
      <div className="max-w-2xl mx-auto p-6 space-y-6">
        <Button variant="ghost" onClick={() => setActiveLesson(null)}>
          ← Back to Lessons
        </Button>
        <div>
          <div className="flex items-center gap-2 mb-2">
            <h1 className="text-2xl font-bold">{activeLesson.title}</h1>
            <Badge>{activeLesson.level}</Badge>
          </div>
          <p className="text-muted-foreground">{activeLesson.explanation}</p>
        </div>

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

        <div className="space-y-4">
          <h2 className="text-xl font-semibold">Exercises</h2>
          {activeLesson.exercise_count === 0 && (
            <p className="text-sm text-muted-foreground">This lesson has no exercises.</p>
          )}
          {Array.from({ length: activeLesson.exercise_count }).map((_, i) => {
            const result = exerciseResults[i]
            return (
              <Card key={i}>
                <CardContent className="p-4 space-y-3">
                  <p className="font-medium">Exercise {i + 1}</p>
                  {!result ? (
                    <div className="flex gap-2">
                      <Input
                        placeholder="Your answer"
                        value={exerciseAnswers[i] || ""}
                        onChange={(e) => setExerciseAnswers((prev) => ({ ...prev, [i]: e.target.value }))}
                        onKeyDown={(e) => e.key === "Enter" && checkExercise(i)}
                      />
                      <Button size="sm" onClick={() => checkExercise(i)} disabled={!exerciseAnswers[i] || checking[i]}>
                        {checking[i] ? "Checking..." : "Check"}
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
          })}
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
          {generating ? "Generating..." : "Generate Personalized Lesson"}
        </Button>
      </div>

      {loadError && (
        <div className="flex items-center gap-2 p-3 rounded-md bg-destructive/10 text-destructive text-sm">
          <AlertCircle className="h-4 w-4" />
          {loadError}
          <Button variant="ghost" size="sm" className="ml-auto" onClick={() => { setLoadError(""); api.lessons.library().then(setLessons).catch(() => setLoadError("Failed to load lessons")) }}>
            Retry
          </Button>
        </div>
      )}

      {generatedLesson && (
        <Card className="border-primary/50">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Sparkles className="h-4 w-4" />
              {generatedLesson.title || "Personalized Lesson"}
            </CardTitle>
            <CardDescription>Based on your most frequent errors</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-sm">{generatedLesson.explanation}</p>
            {generatedLesson.examples && (
              <ul className="space-y-1">
                {generatedLesson.examples.map((ex, i) => (
                  <li key={i} className="text-sm bg-secondary rounded p-2">{ex}</li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        {lessons.map((lesson) => (
          <Card key={lesson.id} className="cursor-pointer hover:border-primary/50 transition-colors" onClick={() => { setActiveLesson(lesson); setExerciseAnswers({}); setExerciseResults({}) }}>
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
    </div>
  )
}
