"use client"

import { useState, useEffect } from "react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Progress } from "@/components/ui/progress"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import type { DashboardStats, CEFRResult } from "@/lib/types"
import {
  BarChart3,
  Clock,
  BookOpen,
  Flame,
  RotateCcw,
  TrendingUp,
  Award,
  MapPin,
  Sparkles,
  AlertCircle,
} from "lucide-react"
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts"

export default function DashboardPage() {
  const [stats, setStats] = useState<DashboardStats | null>(null)
  const [cefr, setCefr] = useState<CEFRResult | null>(null)
  const [weekly, setWeekly] = useState<{ week: string; minutes: number; new_words: number; reviews: number; lessons: number }[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")

  useEffect(() => {
    async function load() {
      try {
        const [s, c, w] = await Promise.all([api.dashboard.stats(), api.dashboard.cefr(), api.dashboard.weekly(8)])
        setStats(s)
        setCefr(c)
        setWeekly(w)
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : "Failed to load dashboard")
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [])

  if (loading) {
    return (
      <div className="max-w-4xl mx-auto p-6 space-y-6">
        <Skeleton className="h-8 w-40" />
        <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
          {Array.from({ length: 6 }).map((_, i) => (
            <Card key={i}>
              <CardContent className="p-4">
                <Skeleton className="h-16 w-full" />
              </CardContent>
            </Card>
          ))}
        </div>
        <Card>
          <CardContent className="p-4 h-64">
            <Skeleton className="h-full w-full" />
          </CardContent>
        </Card>
      </div>
    )
  }

  const statCards = stats
    ? [
        { label: "Total Sessions", value: stats.total_sessions, icon: BarChart3 },
        { label: "Minutes Spoken", value: stats.total_minutes, icon: Clock },
        { label: "Words Learned", value: stats.total_words_learned, icon: BookOpen },
        { label: "Current Streak", value: `${stats.current_streak} days`, icon: Flame },
        { label: "Reviews Done", value: stats.total_reviews, icon: RotateCcw },
        { label: "Due for Review", value: stats.due_for_review, icon: TrendingUp },
      ]
    : []

  return (
    <div className="max-w-4xl mx-auto p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <BarChart3 className="h-6 w-6 text-primary" />
          Dashboard
        </h1>
        {!stats?.assessment_completed && (
          <Button onClick={() => (window.location.href = "/assessment")}>
            <Sparkles className="h-4 w-4 mr-1" />
            Take Assessment
          </Button>
        )}
      </div>

      {error && (
        <div className="flex items-center gap-2 p-3 rounded-md bg-destructive/10 text-destructive text-sm">
          <AlertCircle className="h-4 w-4" />
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Card className="border-primary/30">
          <CardContent className="p-6 flex items-center justify-between">
            <div>
              <p className="text-sm text-muted-foreground">Current Level</p>
              <div className="flex items-center gap-3 mt-1">
                <span className="text-4xl font-bold">{stats?.current_level || "A1"}</span>
                <Badge variant="secondary">{stats?.assessment_completed ? "Assessed" : "Estimated"}</Badge>
              </div>
            </div>
            <Award className="h-10 w-10 text-primary/50" />
          </CardContent>
        </Card>

        {stats?.learning_path ? (
          <Card className="border-primary/30">
            <CardContent className="p-6 space-y-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <MapPin className="h-5 w-5 text-primary" />
                  <p className="font-medium">Learning Path</p>
                </div>
                <Badge variant="secondary">
                  {stats.learning_path.current_level} → {stats.learning_path.target_level}
                </Badge>
              </div>
              <Progress value={stats.learning_path.percent} />
              <p className="text-xs text-muted-foreground">
                {stats.learning_path.lessons_completed} of {stats.learning_path.lessons_required} lessons completed ({stats.learning_path.percent}%)
              </p>
            </CardContent>
          </Card>
        ) : (
          <Card className="border-dashed">
            <CardContent className="p-6 space-y-3">
              <p className="font-medium">No learning path yet</p>
              <p className="text-sm text-muted-foreground">
                Complete the assessment to generate a personalized path from your current level to the next.
              </p>
              <Button size="sm" onClick={() => (window.location.href = "/assessment")}>
                <Sparkles className="h-4 w-4 mr-1" />
                Start Assessment
              </Button>
            </CardContent>
          </Card>
        )}
      </div>

      {cefr && (
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Estimated CEFR Level (from conversations)</CardTitle>
          </CardHeader>
          <CardContent className="p-6 flex items-center justify-between">
            <div>
              <div className="flex items-center gap-3 mt-1">
                <span className="text-4xl font-bold">{cefr.estimated_level}</span>
                <Badge variant="secondary">{Math.round(cefr.confidence * 100)}% confidence</Badge>
              </div>
            </div>
            <div className="text-right text-xs text-muted-foreground space-y-1">
              {cefr.metrics && Object.entries(cefr.metrics).map(([key, val]) => (
                <div key={key}>{key.replace(/_/g, " ")}: {typeof val === "number" ? val.toFixed(1) : val}</div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
        {statCards.map((card) => (
          <Card key={card.label}>
            <CardContent className="p-4 flex items-center gap-3">
              <div className="p-2 rounded-lg bg-primary/10">
                <card.icon className="h-5 w-5 text-primary" />
              </div>
              <div>
                <p className="text-2xl font-bold">{card.value}</p>
                <p className="text-xs text-muted-foreground">{card.label}</p>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      {weekly.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Weekly Activity</CardTitle>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={250}>
              <BarChart data={weekly}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                <XAxis dataKey="week" tick={{ fontSize: 11 }} stroke="var(--muted-foreground)" />
                <YAxis tick={{ fontSize: 11 }} stroke="var(--muted-foreground)" />
                <Tooltip
                  contentStyle={{ backgroundColor: "var(--popover)", border: "1px solid var(--border)", borderRadius: "8px", color: "var(--popover-foreground)" }}
                  labelStyle={{ color: "var(--popover-foreground)" }}
                />
                <Bar dataKey="minutes" fill="var(--primary)" radius={[4, 4, 0, 0]} name="Minutes" />
                <Bar dataKey="reviews" fill="var(--muted-foreground)" radius={[4, 4, 0, 0]} name="Reviews" />
                <Bar dataKey="lessons" fill="var(--secondary-foreground)" radius={[4, 4, 0, 0]} name="Lessons" />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
