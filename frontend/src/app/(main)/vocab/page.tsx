"use client"

import { useState, useEffect } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { api } from "@/lib/api"
import type { VocabItem, QuizQuestion, QuizResult } from "@/lib/types"
import { BookOpen, Plus, Trash2, RotateCcw, CheckCircle2, XCircle } from "lucide-react"

export default function VocabPage() {
  const [vocab, setVocab] = useState<VocabItem[]>([])
  const [filter, setFilter] = useState("all")
  const [showAdd, setShowAdd] = useState(false)
  const [newWord, setNewWord] = useState("")
  const [newDef, setNewDef] = useState("")
  const [newExample, setNewExample] = useState("")
  const [quizQuestions, setQuizQuestions] = useState<QuizQuestion[]>([])
  const [quizAnswers, setQuizAnswers] = useState<Record<string, string>>({})
  const [quizResults, setQuizResults] = useState<Record<string, QuizResult>>({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")

  const loadVocab = () => {
    api.vocab.list(filter).then(setVocab).catch((err: unknown) => {
      setError(err instanceof Error ? err.message : "Failed to load vocabulary")
    })
  }

  useEffect(() => { loadVocab() }, [filter])

  const addVocab = async () => {
    if (!newWord.trim()) return
    setError("")
    try {
      await api.vocab.create({ word: newWord, definition: newDef, example: newExample })
      setNewWord("")
      setNewDef("")
      setNewExample("")
      setShowAdd(false)
      loadVocab()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to add word")
    }
  }

  const deleteVocab = async (id: string) => {
    setError("")
    try {
      await api.vocab.delete(id)
      loadVocab()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to delete word")
    }
  }

  const startQuiz = async () => {
    setLoading(true)
    setError("")
    try {
      const questions = await api.vocab.generateQuiz(5)
      setQuizQuestions(questions)
      setQuizAnswers({})
      setQuizResults({})
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to generate quiz")
    } finally {
      setLoading(false)
    }
  }

  const checkAnswer = async (q: QuizQuestion) => {
    const answer = quizAnswers[q.vocab_item_id] || ""
    if (!answer) return
    try {
      const result = await api.vocab.checkQuiz(q.vocab_item_id, answer)
      setQuizResults((prev) => ({ ...prev, [q.vocab_item_id]: result }))
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to check answer")
    }
  }

  return (
    <div className="max-w-4xl mx-auto p-6 space-y-6">
      {error && (
        <div className="p-3 rounded-md bg-destructive/10 text-destructive text-sm">{error}</div>
      )}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <BookOpen className="h-6 w-6 text-primary" />
          Vocabulary
        </h1>
        <div className="flex gap-2">
          <Button variant="outline" onClick={startQuiz} disabled={loading || vocab.length < 2}>
            Quiz Mode
          </Button>
          <Button onClick={() => setShowAdd(!showAdd)}>
            <Plus className="h-4 w-4 mr-1" />
            Add Word
          </Button>
        </div>
      </div>

      {showAdd && (
        <Card>
          <CardContent className="p-4 space-y-3">
            <Input placeholder="Word / expression" value={newWord} onChange={(e) => setNewWord(e.target.value)} />
            <Input placeholder="Definition" value={newDef} onChange={(e) => setNewDef(e.target.value)} />
            <Input placeholder="Example sentence" value={newExample} onChange={(e) => setNewExample(e.target.value)} />
            <Button onClick={addVocab} size="sm">Add</Button>
          </CardContent>
        </Card>
      )}

      {quizQuestions.length > 0 ? (
        <div className="space-y-4">
          <h2 className="text-xl font-semibold">Quiz</h2>
          {quizQuestions.map((q, i) => (
            <Card key={i}>
              <CardContent className="p-4 space-y-3">
                <p className="font-medium">{q.question}</p>
                {q.options.length > 0 ? (
                  <div className="grid grid-cols-2 gap-2">
                    {q.options.map((opt, j) => (
                      <Button
                        key={j}
                        variant={quizAnswers[q.vocab_item_id] === opt ? "default" : "outline"}
                        onClick={() => setQuizAnswers((prev) => ({ ...prev, [q.vocab_item_id]: opt }))}
                        className="justify-start"
                      >
                        {opt}
                      </Button>
                    ))}
                  </div>
                ) : (
                  <Input
                    placeholder="Your answer"
                    value={quizAnswers[q.vocab_item_id] || ""}
                    onChange={(e) => setQuizAnswers((prev) => ({ ...prev, [q.vocab_item_id]: e.target.value }))}
                  />
                )}
                {!quizResults[q.vocab_item_id] ? (
                  <Button size="sm" onClick={() => checkAnswer(q)} disabled={!quizAnswers[q.vocab_item_id]}>
                    Check
                  </Button>
                ) : (
                  <div className={`flex items-center gap-2 text-sm ${quizResults[q.vocab_item_id].correct ? "text-green-400" : "text-destructive"}`}>
                    {quizResults[q.vocab_item_id].correct ? <CheckCircle2 className="h-4 w-4" /> : <XCircle className="h-4 w-4" />}
                    {quizResults[q.vocab_item_id].explanation}
                  </div>
                )}
              </CardContent>
            </Card>
          ))}
          <Button variant="outline" onClick={() => setQuizQuestions([])}>Back to Vocab</Button>
        </div>
      ) : (
        <>
          <div className="flex gap-2">
            {["all", "due", "new"].map((f) => (
              <Button key={f} variant={filter === f ? "default" : "outline"} size="sm" onClick={() => setFilter(f)}>
                {f.charAt(0).toUpperCase() + f.slice(1)}
              </Button>
            ))}
          </div>

          {vocab.length === 0 ? (
            <div className="text-center py-12 text-muted-foreground">
              <BookOpen className="h-12 w-12 mx-auto mb-4 opacity-50" />
              <p>No vocabulary items yet. Start a conversation to learn new words!</p>
            </div>
          ) : (
            <div className="space-y-2">
              {vocab.map((item) => (
                <Card key={item.id}>
                  <CardContent className="p-4 flex items-start justify-between">
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <span className="font-semibold">{item.word}</span>
                        {item.ipa && <span className="text-xs text-muted-foreground">{item.ipa}</span>}
                        {item.next_review_at && (
                          <Badge variant="secondary" className="text-[10px]">
                            Review: {item.next_review_at}
                          </Badge>
                        )}
                      </div>
                      <p className="text-sm text-muted-foreground">{item.definition}</p>
                      {item.example && (
                        <p className="text-xs text-muted-foreground italic">&quot;{item.example}&quot;</p>
                      )}
                    </div>
                    <Button variant="ghost" size="icon" onClick={() => deleteVocab(item.id)}>
                      <Trash2 className="h-4 w-4 text-muted-foreground" />
                    </Button>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}
