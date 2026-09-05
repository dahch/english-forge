"use client"

import { useState, useEffect } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { api } from "@/lib/api"
import type { DashboardStats, CEFRResult } from "@/lib/types"
import { BarChart3, Clock, BookOpen, Flame, RotateCcw, TrendingUp } from "lucide-react"
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts"

export default function DashboardPage() {
  const [stats, setStats] = useState<DashboardStats | null>(null)
  const [cefr, setCefr] = useState<CEFRResult | null>(null)
  const [weekly, setWeekly] = useState<{ week: string; minutes: number; new_words: number; reviews: number }[]>([])

  useEffect(() => {
    api.dashboard.stats().then(setStats).catch(console.error)
    api.dashboard.cefr().then(setCefr).catch(console.error)
    api.dashboard.weekly(8).then(setWeekly).catch(console.error)
  }, [])

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
      <h1 className="text-2xl font-bold flex items-center gap-2">
        <BarChart3 className="h-6 w-6 text-primary" />
        Dashboard
      </h1>

      {cefr && (
        <Card className="border-primary/30">
          <CardContent className="p-6 flex items-center justify-between">
            <div>
              <p className="text-sm text-muted-foreground">Estimated Level</p>
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
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
