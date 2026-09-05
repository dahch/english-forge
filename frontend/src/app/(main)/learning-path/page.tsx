"use client"

import { useState, useEffect } from "react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Progress } from "@/components/ui/progress"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import type { LearningPath, PathLesson } from "@/lib/types"
import { MapPin, CheckCircle2, Lock, Sparkles, AlertCircle, Loader2, Trophy } from "lucide-react"

const typeIcons: Record<string, string> = {
  vocabulary: "🔤",
  grammar: "📝",
  conversation: "💬",
  listening: "🎧",
  reading: "📖",
  writing: "✍️",
}

export default function LearningPathPage() {
  const [path, setPath] = useState<LearningPath | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")
  const [advancing, setAdvancing] = useState(false)
  const [completing, setCompleting] = useState<string | null>(null)

  useEffect(() => {
    loadPath()
  }, [])

  const loadPath = async () => {
    setLoading(true)
    try {
      const p = await api.learningPath.current()
      setPath(p)
    } catch (err: unknown) {
      if (err instanceof Error && err.message.includes("No active learning path")) {
        setPath(null)
      } else {
        setError(err instanceof Error ? err.message : "Failed to load learning path")
      }
    } finally {
      setLoading(false)
    }
  }

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
          <Button onClick={() => (window.location.href = "/assessment")}>
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

  const percent = Math.round(path.lessons_completed / path.lessons_required * 100)
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
            <Card key={lesson.id} className={lesson.completed ? "opacity-70" : isLocked ? "opacity-50" : ""}>
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
                        onClick={() => completeLesson(lesson)}
                        disabled={completing === lesson.id}
                      >
                        {completing === lesson.id ? <Loader2 className="h-4 w-4 animate-spin" /> : "Complete"}
                      </Button>
                    )}
                  </div>
                  <p className="text-sm text-muted-foreground mt-1">{lesson.description}</p>
                </div>
              </CardContent>
            </Card>
          )
        })}
      </div>
    </div>
  )
}
