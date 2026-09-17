"use client"

import { useState, useEffect, useRef, useCallback, Suspense } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Progress } from "@/components/ui/progress"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import type { VocabItem, QuizQuestion, QuizResult } from "@/lib/types"
import {
  BookOpen,
  Plus,
  Trash2,
  CheckCircle2,
  XCircle,
  Volume2,
  Sparkles,
  Eye,
  Clock,
  X,
  Trophy,
  Loader2,
  RotateCw,
} from "lucide-react"

type ViewMode = "list" | "review" | "quiz"
type Accent = "en-US" | "en-GB"

interface ReviewStats {
  again: number
  hard: number
  good: number
  easy: number
  total: number
}

function VocabContent() {
  const router = useRouter()
  const searchParams = useSearchParams()

  // Vocab list state
  const [allVocab, setAllVocab] = useState<VocabItem[]>([])
  const [filter, setFilter] = useState<"all" | "due" | "new">("all")
  const [showAdd, setShowAdd] = useState(false)
  const [newWord, setNewWord] = useState("")
  const [newDef, setNewDef] = useState("")
  const [newExample, setNewExample] = useState("")
  const [newIpa, setNewIpa] = useState("")
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")

  // View mode
  const [viewMode, setViewMode] = useState<ViewMode>("list")

  // TTS Pronunciation state
  const [accent, setAccent] = useState<Accent>(() => {
    if (typeof window !== "undefined") {
      const savedAccent = localStorage.getItem("ef_vocab_accent") as Accent | null
      if (savedAccent === "en-US" || savedAccent === "en-GB") {
        return savedAccent
      }
    }
    return "en-US"
  })
  const [speakingWord, setSpeakingWord] = useState<string | null>(null)
  const utteranceRef = useRef<SpeechSynthesisUtterance | null>(null)

  // Web Audio Context reference (shared across ratings, lazy initialized)
  const audioCtxRef = useRef<AudioContext | null>(null)

  // Flashcard Review Mode state
  const [reviewCards, setReviewCards] = useState<VocabItem[]>([])
  const [currentCardIndex, setCurrentCardIndex] = useState(0)
  const [isAnswerRevealed, setIsAnswerRevealed] = useState(false)
  const [isReviewCompleted, setIsReviewCompleted] = useState(false)
  const [loadingReview, setLoadingReview] = useState(false)
  const [ratingSubmitting, setRatingSubmitting] = useState(false)
  const [reviewError, setReviewError] = useState<string | null>(null)
  const [pendingRatingQuality, setPendingRatingQuality] = useState<number | null>(null)
  const [reviewStats, setReviewStats] = useState<ReviewStats>({
    again: 0,
    hard: 0,
    good: 0,
    easy: 0,
    total: 0,
  })
  const [lastRated, setLastRated] = useState<{ quality: number; word: string } | null>(null)

  // Quiz state
  const [quizQuestions, setQuizQuestions] = useState<QuizQuestion[]>([])
  const [quizAnswers, setQuizAnswers] = useState<Record<string, string>>({})
  const [quizResults, setQuizResults] = useState<Record<string, QuizResult>>({})
  const [loadingQuiz, setLoadingQuiz] = useState(false)

  const handleAccentChange = (newAccent: Accent) => {
    setAccent(newAccent)
    if (typeof window !== "undefined") {
      localStorage.setItem("ef_vocab_accent", newAccent)
    }
  }

  // Load vocabulary list
  const loadVocab = useCallback(async () => {
    setError("")
    try {
      const items = await api.vocab.list("all")
      setAllVocab(items)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load vocabulary")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    Promise.resolve().then(() => {
      void loadVocab()
    })
  }, [loadVocab])

  // TTS Speech Synthesis helper
  const speakText = useCallback(
    (text: string, overrideAccent?: Accent) => {
      if (typeof window === "undefined" || !("speechSynthesis" in window)) {
        setError("Speech synthesis is not supported in this browser.")
        return
      }
      window.speechSynthesis.cancel()

      const chosenAccent = overrideAccent || accent
      const utterance = new SpeechSynthesisUtterance(text)
      utteranceRef.current = utterance
      utterance.lang = chosenAccent
      utterance.rate = 0.88

      const voices = window.speechSynthesis.getVoices()
      if (voices.length > 0) {
        if (chosenAccent === "en-GB") {
          const ukVoice = voices.find(
            (v) =>
              v.lang.toLowerCase().startsWith("en-gb") ||
              v.name.toLowerCase().includes("uk") ||
              v.name.toLowerCase().includes("british") ||
              v.name.toLowerCase().includes("george") ||
              v.name.toLowerCase().includes("daniel")
          )
          if (ukVoice) utterance.voice = ukVoice
        } else {
          const usVoice = voices.find(
            (v) =>
              v.lang.toLowerCase().startsWith("en-us") ||
              v.name.toLowerCase().includes("us") ||
              v.name.toLowerCase().includes("united states") ||
              v.name.toLowerCase().includes("samantha") ||
              v.name.toLowerCase().includes("alex")
          )
          if (usVoice) utterance.voice = usVoice
        }
      }

      setSpeakingWord(text)
      utterance.onend = () => {
        utteranceRef.current = null
        setSpeakingWord(null)
      }
      utterance.onerror = () => {
        utteranceRef.current = null
        setSpeakingWord(null)
      }

      window.speechSynthesis.speak(utterance)
    },
    [accent]
  )

  // Clean up ongoing TTS speech and AudioContext on unmount
  useEffect(() => {
    return () => {
      if (typeof window !== "undefined" && "speechSynthesis" in window) {
        window.speechSynthesis.cancel()
      }
      utteranceRef.current = null
      if (audioCtxRef.current && audioCtxRef.current.state !== "closed") {
        audioCtxRef.current.close().catch(() => {})
        audioCtxRef.current = null
      }
    }
  }, [])

  // Audio subtle feedback chime reusing shared AudioContext
  const playFeedbackChime = (quality: number) => {
    if (typeof window === "undefined") return
    try {
      const AudioContextClass =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
      if (!AudioContextClass) return

      if (!audioCtxRef.current || audioCtxRef.current.state === "closed") {
        audioCtxRef.current = new AudioContextClass()
      }
      const ctx = audioCtxRef.current
      if (ctx.state === "suspended") {
        ctx.resume().catch(() => {})
      }

      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.connect(gain)
      gain.connect(ctx.destination)

      const freq = quality === 0 ? 220 : quality === 3 ? 330 : quality === 4 ? 523.25 : 659.25
      osc.type = "sine"
      osc.frequency.setValueAtTime(freq, ctx.currentTime)
      gain.gain.setValueAtTime(0.08, ctx.currentTime)
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.15)
      osc.start()
      osc.stop(ctx.currentTime + 0.15)
    } catch {
      // Ignore audio context autoplay restriction
    }
  }

  // Calculate item counts for filters
  const todayDate = new Date()
  const todayStr = `${todayDate.getFullYear()}-${String(todayDate.getMonth() + 1).padStart(2, "0")}-${String(
    todayDate.getDate()
  ).padStart(2, "0")}`

  const isDueItem = (item: VocabItem) => !item.next_review_at || item.next_review_at <= todayStr
  const isNewItem = (item: VocabItem) => item.interval_days === 0

  const allCount = allVocab.length
  const dueCount = allVocab.filter(isDueItem).length
  const newCount = allVocab.filter(isNewItem).length

  // Filtered list
  const filteredVocab =
    filter === "due"
      ? allVocab.filter(isDueItem)
      : filter === "new"
      ? allVocab.filter(isNewItem)
      : allVocab

  // Start Flashcard Review Mode
  const startReview = useCallback(async () => {
    setLoadingReview(true)
    setError("")
    setReviewError(null)
    setPendingRatingQuality(null)
    try {
      const due = await api.vocab.due(50)
      setReviewCards(due)
      setCurrentCardIndex(0)
      setIsAnswerRevealed(false)
      setIsReviewCompleted(false)
      setReviewStats({ again: 0, hard: 0, good: 0, easy: 0, total: 0 })
      setLastRated(null)
      setViewMode("review")
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load due cards for review")
    } finally {
      setLoadingReview(false)
    }
  }, [])

  // Check URL query param `mode=review`
  useEffect(() => {
    if (searchParams.get("mode") === "review") {
      Promise.resolve().then(() => {
        void startReview()
      })
    }
  }, [searchParams, startReview])

  const exitReview = useCallback(() => {
    setViewMode("list")
    setIsAnswerRevealed(false)
    setIsReviewCompleted(false)
    setLastRated(null)
    setReviewError(null)
    setPendingRatingQuality(null)
    router.replace("/vocab")
    void loadVocab()
  }, [loadVocab, router])

  // Rate a card in Flashcard Review Mode
  const handleRateCard = useCallback(async (quality: number) => {
    const currentCard = reviewCards[currentCardIndex]
    if (!currentCard || ratingSubmitting) return

    setRatingSubmitting(true)
    setReviewError(null)
    setPendingRatingQuality(quality)

    try {
      await api.vocab.review(currentCard.id, quality)

      playFeedbackChime(quality)

      const key = quality === 0 ? "again" : quality === 3 ? "hard" : quality === 4 ? "good" : "easy"
      setReviewStats((prev) => ({
        ...prev,
        [key]: prev[key] + 1,
        total: prev.total + 1,
      }))

      setLastRated({ quality, word: currentCard.word })
      setTimeout(() => setLastRated(null), 1200)

      setPendingRatingQuality(null)

      if (currentCardIndex + 1 < reviewCards.length) {
        setCurrentCardIndex((idx) => idx + 1)
        setIsAnswerRevealed(false)
      } else {
        setIsReviewCompleted(true)
      }
    } catch (err: unknown) {
      console.error("Failed to submit review:", err)
      setReviewError(err instanceof Error ? err.message : "Failed to submit review. Please try again.")
    } finally {
      setRatingSubmitting(false)
    }
  }, [currentCardIndex, ratingSubmitting, reviewCards])

  // Keyboard shortcut listener for Flashcard Review Mode
  useEffect(() => {
    if (viewMode !== "review" || isReviewCompleted || reviewCards.length === 0) return

    const handleKeyDown = (e: KeyboardEvent) => {
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement ||
        (e.target as HTMLElement)?.isContentEditable
      ) {
        return
      }

      if (e.key === "Escape") {
        e.preventDefault()
        exitReview()
        return
      }

      const currentCard = reviewCards[currentCardIndex]
      if (!currentCard) return

      if (!isAnswerRevealed) {
        if (e.key === " " || e.key === "Enter") {
          e.preventDefault()
          setIsAnswerRevealed(true)
        } else if (e.key === "r" || e.key === "R" || e.key === "a" || e.key === "A") {
          e.preventDefault()
          speakText(currentCard.word)
        }
      } else {
        if (e.key === "1") {
          e.preventDefault()
          handleRateCard(0)
        } else if (e.key === "2") {
          e.preventDefault()
          handleRateCard(3)
        } else if (e.key === "3" || e.key === " " || e.key === "Enter") {
          e.preventDefault()
          handleRateCard(4)
        } else if (e.key === "4") {
          e.preventDefault()
          handleRateCard(5)
        } else if (e.key === "r" || e.key === "R" || e.key === "a" || e.key === "A") {
          e.preventDefault()
          speakText(currentCard.word)
        }
      }
    }

    window.addEventListener("keydown", handleKeyDown)
    return () => window.removeEventListener("keydown", handleKeyDown)
  }, [
    viewMode,
    isReviewCompleted,
    reviewCards,
    currentCardIndex,
    isAnswerRevealed,
    speakText,
    ratingSubmitting,
    exitReview,
    handleRateCard,
  ])

  // Add word action
  const addVocab = async () => {
    if (!newWord.trim()) return
    setError("")
    try {
      await api.vocab.create({
        word: newWord.trim(),
        definition: newDef.trim(),
        example: newExample.trim(),
        ipa: newIpa.trim() || null,
      })
      setNewWord("")
      setNewDef("")
      setNewExample("")
      setNewIpa("")
      setShowAdd(false)
      loadVocab()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to add word")
    }
  }

  // Delete word action
  const deleteVocab = async (id: string) => {
    setError("")
    try {
      await api.vocab.delete(id)
      loadVocab()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to delete word")
    }
  }

  // Quiz Mode actions
  const startQuiz = async () => {
    setLoadingQuiz(true)
    setError("")
    try {
      const questions = await api.vocab.generateQuiz(5)
      setQuizQuestions(questions)
      setQuizAnswers({})
      setQuizResults({})
      setViewMode("quiz")
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to generate quiz")
    } finally {
      setLoadingQuiz(false)
    }
  }

  const checkQuizAnswer = async (q: QuizQuestion) => {
    const answer = quizAnswers[q.vocab_item_id] || ""
    if (!answer) return
    try {
      const result = await api.vocab.checkQuiz(q.vocab_item_id, answer)
      setQuizResults((prev) => ({ ...prev, [q.vocab_item_id]: result }))
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to check answer")
    }
  }

  const exitQuiz = () => {
    setViewMode("list")
    setQuizQuestions([])
    setQuizAnswers({})
    setQuizResults({})
  }

  // ==========================================
  // VIEW: Flashcard Review Mode Completion
  // ==========================================
  if (viewMode === "review" && isReviewCompleted) {
    const retentionRate =
      reviewStats.total > 0
        ? Math.round(((reviewStats.good + reviewStats.easy) / reviewStats.total) * 100)
        : 100

    return (
      <div className="max-w-2xl mx-auto p-4 sm:p-6 space-y-6">
        <Card className="border-primary/40 bg-gradient-to-br from-primary/5 via-background to-secondary/30 text-center shadow-lg">
          <CardContent className="p-8 space-y-6">
            <div className="w-16 h-16 rounded-full bg-primary/10 text-primary mx-auto flex items-center justify-center shadow-inner">
              <Trophy className="h-8 w-8" />
            </div>

            <div className="space-y-2">
              <h2 className="text-2xl sm:text-3xl font-bold tracking-tight">Review Session Complete!</h2>
              <p className="text-muted-foreground text-sm max-w-md mx-auto">
                Congratulations! You reviewed <strong>{reviewStats.total}</strong> card
                {reviewStats.total === 1 ? "" : "s"} and reinforced your long-term memory.
              </p>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 max-w-lg mx-auto pt-2">
              <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-center">
                <p className="text-2xl font-extrabold text-red-500">{reviewStats.again}</p>
                <p className="text-xs text-muted-foreground font-medium mt-0.5">Again</p>
              </div>
              <div className="p-3 rounded-lg bg-amber-500/10 border border-amber-500/20 text-center">
                <p className="text-2xl font-extrabold text-amber-500">{reviewStats.hard}</p>
                <p className="text-xs text-muted-foreground font-medium mt-0.5">Hard</p>
              </div>
              <div className="p-3 rounded-lg bg-blue-500/10 border border-blue-500/20 text-center">
                <p className="text-2xl font-extrabold text-blue-500">{reviewStats.good}</p>
                <p className="text-xs text-muted-foreground font-medium mt-0.5">Good</p>
              </div>
              <div className="p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-center">
                <p className="text-2xl font-extrabold text-emerald-500">{reviewStats.easy}</p>
                <p className="text-xs text-muted-foreground font-medium mt-0.5">Easy</p>
              </div>
            </div>

            <div className="p-4 rounded-xl bg-background/80 border max-w-md mx-auto flex items-center justify-between shadow-xs">
              <span className="text-sm font-medium">Retention Score</span>
              <span className="text-xl font-extrabold text-primary">{retentionRate}%</span>
            </div>

            <div className="pt-2 flex flex-col sm:flex-row items-center justify-center gap-3">
              <Button size="lg" onClick={exitReview} className="w-full sm:w-auto font-semibold">
                Back to Vocabulary
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    )
  }

  // ==========================================
  // VIEW: Flashcard Review Mode Active Card
  // ==========================================
  if (viewMode === "review") {
    if (reviewCards.length === 0 && !loadingReview) {
      return (
        <div className="max-w-md mx-auto p-6 text-center space-y-4">
          <CheckCircle2 className="h-12 w-12 text-emerald-500 mx-auto" />
          <h2 className="text-xl font-bold">All caught up!</h2>
          <p className="text-sm text-muted-foreground">
            No cards are currently due for review. Great work staying ahead of your intervals!
          </p>
          <Button onClick={exitReview}>Back to Vocabulary</Button>
        </div>
      )
    }

    const currentCard = reviewCards[currentCardIndex]
    const reviewProgressPercent =
      reviewCards.length > 0 ? Math.round(((currentCardIndex + 1) / reviewCards.length) * 100) : 0

    return (
      <div className="max-w-2xl mx-auto p-4 sm:p-6 space-y-6">
        {/* Review Mode Header */}
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <Badge variant="outline" className="text-xs font-semibold gap-1.5 px-3 py-1">
              <Clock className="h-3.5 w-3.5 text-primary" />
              Card {currentCardIndex + 1} of {reviewCards.length}
            </Badge>
            <Badge variant="secondary" className="text-xs">
              {reviewCards.length - currentCardIndex} due
            </Badge>
          </div>

          <div className="flex items-center gap-2">
            {/* Accent switcher */}
            <div className="flex items-center rounded-lg border bg-muted/40 p-0.5 text-xs">
              <button
                type="button"
                className={`px-2 py-1 rounded font-medium transition-all ${
                  accent === "en-US"
                    ? "bg-background shadow-xs text-foreground font-semibold"
                    : "text-muted-foreground hover:text-foreground"
                }`}
                onClick={() => handleAccentChange("en-US")}
                title="American English accent"
                aria-pressed={accent === "en-US"}
              >
                🇺🇸 US
              </button>
              <button
                type="button"
                className={`px-2 py-1 rounded font-medium transition-all ${
                  accent === "en-GB"
                    ? "bg-background shadow-xs text-foreground font-semibold"
                    : "text-muted-foreground hover:text-foreground"
                }`}
                onClick={() => handleAccentChange("en-GB")}
                title="British English accent"
                aria-pressed={accent === "en-GB"}
              >
                🇬🇧 UK
              </button>
            </div>

            <Button
              variant="ghost"
              size="sm"
              onClick={exitReview}
              className="text-xs text-muted-foreground hover:text-foreground"
            >
              <X className="h-4 w-4 mr-1" />
              Exit Review
            </Button>
          </div>
        </div>

        {/* Progress Bar */}
        <Progress value={reviewProgressPercent} className="h-1.5" />

        {/* Review Submission Error with Retry */}
        {reviewError && (
          <div className="p-3 rounded-md bg-destructive/10 text-destructive text-sm flex items-center justify-between gap-3 animate-in fade-in-50 border border-destructive/20">
            <div className="flex items-center gap-2">
              <XCircle className="h-4 w-4 shrink-0" />
              <span>{reviewError}</span>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                className="h-7 px-3 text-xs border-destructive/40 hover:bg-destructive hover:text-destructive-foreground"
                onClick={() => {
                  if (pendingRatingQuality !== null) {
                    handleRateCard(pendingRatingQuality)
                  }
                }}
                disabled={ratingSubmitting}
              >
                {ratingSubmitting ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" />
                ) : (
                  <RotateCw className="h-3.5 w-3.5 mr-1" />
                )}
                Retry
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setReviewError(null)}
                className="h-7 px-2 text-xs"
              >
                Dismiss
              </Button>
            </div>
          </div>
        )}

        {/* Subtle rating indicator notification */}
        {lastRated && (
          <div className="flex items-center justify-center gap-1.5 text-xs font-semibold py-1 px-3 rounded-full mx-auto animate-in fade-in zoom-in-90 duration-150 bg-secondary border">
            <span>Rated &quot;{lastRated.word}&quot; as</span>
            <Badge
              variant="secondary"
              className={
                lastRated.quality === 0
                  ? "bg-red-500/20 text-red-500"
                  : lastRated.quality === 3
                  ? "bg-amber-500/20 text-amber-500"
                  : lastRated.quality === 4
                  ? "bg-blue-500/20 text-blue-500"
                  : "bg-emerald-500/20 text-emerald-500"
              }
            >
              {lastRated.quality === 0
                ? "Again"
                : lastRated.quality === 3
                ? "Hard"
                : lastRated.quality === 4
                ? "Good"
                : "Easy"}
            </Badge>
          </div>
        )}

        {/* Flashcard Body */}
        {currentCard && (
          <Card className="border-primary/30 shadow-md transition-all">
            <CardContent className="p-6 sm:p-8 space-y-6">
              {/* CARD FRONT */}
              <div className="text-center py-4 space-y-3">
                <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  Prompt
                </p>
                <div className="flex items-center justify-center gap-3">
                  <h2 className="text-3xl sm:text-5xl font-extrabold tracking-tight text-foreground">
                    {currentCard.word}
                  </h2>
                  <Button
                    variant="outline"
                    size="icon"
                    className={`h-11 w-11 rounded-full shadow-xs transition-all ${
                      speakingWord === currentCard.word
                        ? "border-primary text-primary bg-primary/10 ring-2 ring-primary/30 animate-pulse"
                        : "text-muted-foreground hover:text-primary hover:border-primary"
                    }`}
                    onClick={() => speakText(currentCard.word)}
                    title={`Listen pronunciation (${accent === "en-GB" ? "British UK" : "American US"})`}
                    aria-label={`Listen to pronunciation of ${currentCard.word} in ${accent === "en-GB" ? "British English" : "American English"}`}
                  >
                    <Volume2 className="h-5 w-5" />
                  </Button>
                </div>
                <div className="text-xs text-muted-foreground flex items-center justify-center gap-2">
                  <span>Pronunciation: {accent === "en-GB" ? "British (UK)" : "American (US)"}</span>
                  <span>•</span>
                  <span>
                    Press <kbd className="px-1.5 py-0.5 bg-muted rounded border text-[10px]">R</kbd> to listen
                  </span>
                </div>
              </div>

              {/* CARD FRONT ACTION: REVEAL */}
              {!isAnswerRevealed ? (
                <div className="pt-4 pb-2 flex flex-col items-center gap-3 border-t">
                  <Button
                    size="lg"
                    className="w-full max-w-sm py-6 text-base font-semibold shadow-md gap-2"
                    onClick={() => setIsAnswerRevealed(true)}
                  >
                    <Eye className="h-5 w-5" />
                    <span>Show Answer</span>
                    <kbd className="ml-2 bg-primary-foreground/20 text-primary-foreground px-2 py-0.5 rounded text-xs">
                      Space
                    </kbd>
                  </Button>
                  <p className="text-xs text-muted-foreground">
                    Recall definition &amp; usage before revealing
                  </p>
                </div>
              ) : (
                /* CARD BACK (REVEALED) */
                <div
                  className="border-t pt-6 space-y-5 animate-in fade-in-50 duration-200"
                  aria-live="polite"
                >
                  {currentCard.ipa && (
                    <div className="text-center">
                      <span className="text-sm font-mono text-muted-foreground bg-muted/60 px-3 py-1 rounded-md border">
                        /{currentCard.ipa}/
                      </span>
                    </div>
                  )}

                  <div className="text-center max-w-xl mx-auto space-y-1">
                    <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                      Definition
                    </p>
                    <p className="text-lg font-medium text-foreground leading-relaxed">
                      {currentCard.definition}
                    </p>
                  </div>

                  {currentCard.example && (
                    <div className="max-w-lg mx-auto p-4 rounded-xl bg-secondary/40 border border-secondary text-center">
                      <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-1">
                        Context Example
                      </p>
                      <p className="text-sm italic text-foreground/90">
                        &ldquo;{currentCard.example}&rdquo;
                      </p>
                    </div>
                  )}

                  {/* 4 Anki-Style Response Rating Buttons */}
                  <div className="space-y-3 pt-4">
                    <p className="text-center text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                      How well did you recall this?
                    </p>
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 max-w-2xl mx-auto">
                      {/* 1. Again (Red) */}
                      <Button
                        variant="outline"
                        className="flex-col h-auto py-3 px-2 border-red-500/30 text-red-600 dark:text-red-400 bg-red-500/5 hover:bg-red-500 hover:text-white transition-all shadow-xs"
                        onClick={() => handleRateCard(0)}
                        disabled={ratingSubmitting}
                      >
                        <span className="font-bold text-sm">Again</span>
                        <span className="text-[11px] opacity-80 mt-0.5">&lt; 1 day</span>
                        <kbd className="mt-1 text-[10px] px-1.5 py-0.5 rounded border border-current/20 font-mono">
                          1
                        </kbd>
                      </Button>

                      {/* 2. Hard (Orange) */}
                      <Button
                        variant="outline"
                        className="flex-col h-auto py-3 px-2 border-amber-500/30 text-amber-600 dark:text-amber-400 bg-amber-500/5 hover:bg-amber-500 hover:text-white transition-all shadow-xs"
                        onClick={() => handleRateCard(3)}
                        disabled={ratingSubmitting}
                      >
                        <span className="font-bold text-sm">Hard</span>
                        <span className="text-[11px] opacity-80 mt-0.5">Difficult</span>
                        <kbd className="mt-1 text-[10px] px-1.5 py-0.5 rounded border border-current/20 font-mono">
                          2
                        </kbd>
                      </Button>

                      {/* 3. Good (Blue/Default) */}
                      <Button
                        variant="outline"
                        className="flex-col h-auto py-3 px-2 border-blue-500/30 text-blue-600 dark:text-blue-400 bg-blue-500/5 hover:bg-blue-500 hover:text-white transition-all shadow-xs"
                        onClick={() => handleRateCard(4)}
                        disabled={ratingSubmitting}
                      >
                        <span className="font-bold text-sm">Good</span>
                        <span className="text-[11px] opacity-80 mt-0.5">Standard</span>
                        <kbd className="mt-1 text-[10px] px-1.5 py-0.5 rounded border border-current/20 font-mono">
                          3 / Space
                        </kbd>
                      </Button>

                      {/* 4. Easy (Green) */}
                      <Button
                        variant="outline"
                        className="flex-col h-auto py-3 px-2 border-emerald-500/30 text-emerald-600 dark:text-emerald-400 bg-emerald-500/5 hover:bg-emerald-500 hover:text-white transition-all shadow-xs"
                        onClick={() => handleRateCard(5)}
                        disabled={ratingSubmitting}
                      >
                        <span className="font-bold text-sm">Easy</span>
                        <span className="text-[11px] opacity-80 mt-0.5">Bonus</span>
                        <kbd className="mt-1 text-[10px] px-1.5 py-0.5 rounded border border-current/20 font-mono">
                          4
                        </kbd>
                      </Button>
                    </div>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        )}
      </div>
    )
  }

  // ==========================================
  // VIEW: Quiz Mode
  // ==========================================
  if (viewMode === "quiz") {
    return (
      <div className="max-w-4xl mx-auto p-4 sm:p-6 space-y-6">
        <div className="flex items-center justify-between">
          <h2 className="text-xl font-bold flex items-center gap-2">
            <BookOpen className="h-5 w-5 text-primary" />
            Vocabulary Quiz
          </h2>
          <Button variant="outline" size="sm" onClick={exitQuiz}>
            Back to Vocab
          </Button>
        </div>

        {quizQuestions.length > 0 ? (
          <div className="space-y-4">
            {quizQuestions.map((q, i) => (
              <Card key={i}>
                <CardContent className="p-4 space-y-3">
                  <p className="font-medium">{q.question}</p>
                  {q.options.length > 0 ? (
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                      {q.options.map((opt, j) => (
                        <Button
                          key={j}
                          variant={quizAnswers[q.vocab_item_id] === opt ? "default" : "outline"}
                          onClick={() => setQuizAnswers((prev) => ({ ...prev, [q.vocab_item_id]: opt }))}
                          className="justify-start text-left h-auto py-2.5 px-3"
                        >
                          {opt}
                        </Button>
                      ))}
                    </div>
                  ) : (
                    <Input
                      placeholder="Your answer"
                      value={quizAnswers[q.vocab_item_id] || ""}
                      onChange={(e) =>
                        setQuizAnswers((prev) => ({ ...prev, [q.vocab_item_id]: e.target.value }))
                      }
                      onKeyDown={(e) => e.key === "Enter" && checkQuizAnswer(q)}
                    />
                  )}
                  {!quizResults[q.vocab_item_id] ? (
                    <Button
                      size="sm"
                      onClick={() => checkQuizAnswer(q)}
                      disabled={!quizAnswers[q.vocab_item_id]}
                    >
                      Check
                    </Button>
                  ) : (
                    <div
                      className={`flex items-center gap-2 text-sm p-2 rounded-md ${
                        quizResults[q.vocab_item_id].correct
                          ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                          : "bg-destructive/10 text-destructive"
                      }`}
                    >
                      {quizResults[q.vocab_item_id].correct ? (
                        <CheckCircle2 className="h-4 w-4 shrink-0" />
                      ) : (
                        <XCircle className="h-4 w-4 shrink-0" />
                      )}
                      <span>{quizResults[q.vocab_item_id].explanation}</span>
                    </div>
                  )}
                </CardContent>
              </Card>
            ))}
            <div className="pt-2">
              <Button variant="outline" onClick={exitQuiz}>
                Back to Vocab List
              </Button>
            </div>
          </div>
        ) : (
          <div className="text-center py-12 text-muted-foreground">
            <p>No quiz questions available.</p>
            <Button variant="outline" className="mt-4" onClick={exitQuiz}>
              Back to Vocab
            </Button>
          </div>
        )}
      </div>
    )
  }

  // ==========================================
  // VIEW: Vocabulary List (Default)
  // ==========================================
  return (
    <div className="max-w-4xl mx-auto p-4 sm:p-6 space-y-6">
      {error && (
        <div className="p-3 rounded-md bg-destructive/10 text-destructive text-sm flex items-center justify-between">
          <span>{error}</span>
          <Button variant="ghost" size="sm" onClick={() => setError("")} className="h-6 px-2 text-xs">
            Dismiss
          </Button>
        </div>
      )}

      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <BookOpen className="h-6 w-6 text-primary" />
            Vocabulary
          </h1>
          <p className="text-xs text-muted-foreground mt-0.5">
            Spaced Repetition System (SRS) for permanent retention
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {/* Accent toggle (US / UK) */}
          <div className="flex items-center rounded-lg border bg-muted/40 p-0.5 text-xs">
            <button
              type="button"
              className={`px-2 py-1 rounded font-medium transition-all ${
                accent === "en-US"
                  ? "bg-background shadow-xs text-foreground font-semibold"
                  : "text-muted-foreground hover:text-foreground"
              }`}
              onClick={() => handleAccentChange("en-US")}
              title="American English pronunciation"
              aria-pressed={accent === "en-US"}
            >
              🇺🇸 US
            </button>
            <button
              type="button"
              className={`px-2 py-1 rounded font-medium transition-all ${
                accent === "en-GB"
                  ? "bg-background shadow-xs text-foreground font-semibold"
                  : "text-muted-foreground hover:text-foreground"
              }`}
              onClick={() => handleAccentChange("en-GB")}
              title="British English pronunciation"
              aria-pressed={accent === "en-GB"}
            >
              🇬🇧 UK
            </button>
          </div>

          {/* Prominent Review Due Cards CTA Button */}
          <Button
            variant="default"
            onClick={startReview}
            disabled={dueCount === 0 || loadingReview}
            className="gap-2 font-semibold shadow-sm"
          >
            {loadingReview ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Sparkles className="h-4 w-4 text-amber-300" />
            )}
            <span>Review Due Cards</span>
            <Badge variant="secondary" className="px-1.5 py-0 text-xs font-bold ml-1">
              {dueCount}
            </Badge>
          </Button>

          {/* Quiz Mode Button */}
          <Button
            variant="outline"
            onClick={startQuiz}
            disabled={loadingQuiz || allVocab.length < 2}
            className="gap-1.5"
          >
            {loadingQuiz && <Loader2 className="h-4 w-4 animate-spin" />}
            Quiz Mode
          </Button>

          {/* Add Word Button */}
          <Button variant="outline" onClick={() => setShowAdd(!showAdd)}>
            <Plus className="h-4 w-4 mr-1" />
            Add Word
          </Button>
        </div>
      </div>

      {/* Add Word Form */}
      {showAdd && (
        <Card className="border-primary/40 shadow-sm animate-in fade-in-50 duration-200">
          <CardContent className="p-4 space-y-3">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <Input
                placeholder="Word / expression *"
                value={newWord}
                onChange={(e) => setNewWord(e.target.value)}
              />
              <Input
                placeholder="IPA transcription (optional, e.g. /əˈkjumən/)"
                value={newIpa}
                onChange={(e) => setNewIpa(e.target.value)}
              />
            </div>
            <Input
              placeholder="Definition *"
              value={newDef}
              onChange={(e) => setNewDef(e.target.value)}
            />
            <Input
              placeholder="Example sentence"
              value={newExample}
              onChange={(e) => setNewExample(e.target.value)}
            />
            <div className="flex gap-2 justify-end pt-1">
              <Button variant="ghost" size="sm" onClick={() => setShowAdd(false)}>
                Cancel
              </Button>
              <Button onClick={addVocab} size="sm" disabled={!newWord.trim()}>
                Save Word
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Filter Tabs with Counts */}
      <div className="flex items-center justify-between gap-2 border-b pb-3">
        <div className="flex gap-2">
          <Button
            variant={filter === "all" ? "default" : "outline"}
            size="sm"
            onClick={() => setFilter("all")}
            className="text-xs"
          >
            All ({allCount})
          </Button>
          <Button
            variant={filter === "due" ? "default" : "outline"}
            size="sm"
            onClick={() => setFilter("due")}
            className={`text-xs ${filter !== "due" && dueCount > 0 ? "border-primary/50 text-primary" : ""}`}
          >
            Due ({dueCount})
          </Button>
          <Button
            variant={filter === "new" ? "default" : "outline"}
            size="sm"
            onClick={() => setFilter("new")}
            className="text-xs"
          >
            New ({newCount})
          </Button>
        </div>

        <Button
          variant="ghost"
          size="sm"
          onClick={loadVocab}
          disabled={loading}
          className="h-8 px-2 text-xs text-muted-foreground"
          title="Refresh vocabulary"
        >
          <RotateCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
        </Button>
      </div>

      {/* Vocabulary List Items */}
      {loading ? (
        <div className="space-y-2.5">
          {[...Array(4)].map((_, i) => (
            <Card key={i} className="shadow-xs">
              <CardContent className="p-4 flex items-start justify-between gap-3">
                <div className="space-y-2 flex-1">
                  <div className="flex items-center gap-2">
                    <Skeleton className="h-6 w-28" />
                    <Skeleton className="h-5 w-16" />
                    <Skeleton className="h-5 w-24" />
                  </div>
                  <Skeleton className="h-4 w-3/4" />
                  <Skeleton className="h-10 w-full rounded-lg" />
                </div>
                <Skeleton className="h-8 w-8 rounded-md" />
              </CardContent>
            </Card>
          ))}
        </div>
      ) : filteredVocab.length === 0 ? (
        <div className="text-center py-16 text-muted-foreground space-y-3">
          <BookOpen className="h-12 w-12 mx-auto opacity-40 text-muted-foreground" />
          <p className="text-base font-medium">
            {filter === "due"
              ? "No cards due for review right now! Check back later or review all words."
              : filter === "new"
              ? "No new vocabulary cards right now."
              : "No vocabulary items yet. Add words or start a conversation to learn new vocabulary!"}
          </p>
          {filter !== "all" && (
            <Button variant="outline" size="sm" onClick={() => setFilter("all")}>
              View All ({allCount})
            </Button>
          )}
        </div>
      ) : (
        <div className="space-y-2.5">
          {filteredVocab.map((item) => (
            <Card
              key={item.id}
              className="hover:border-primary/40 transition-colors shadow-xs"
            >
              <CardContent className="p-4 flex items-start justify-between gap-3">
                <div className="space-y-1.5 flex-1">
                  <div className="flex items-center flex-wrap gap-2">
                    <span className="font-bold text-base text-foreground">{item.word}</span>
                    <Button
                      size="icon"
                      variant="ghost"
                      className="h-7 w-7 text-muted-foreground hover:text-primary shrink-0"
                      onClick={() => speakText(item.word)}
                      title={`Listen to pronunciation (${accent === "en-GB" ? "UK" : "US"})`}
                      aria-label={`Listen to pronunciation of ${item.word}`}
                    >
                      <Volume2
                        className={`h-4 w-4 ${
                          speakingWord === item.word ? "text-primary animate-pulse" : ""
                        }`}
                      />
                    </Button>
                    {item.ipa && (
                      <Badge variant="outline" className="text-xs font-mono text-muted-foreground">
                        {item.ipa}
                      </Badge>
                    )}
                    {item.next_review_at ? (
                      <Badge
                        variant={isDueItem(item) ? "destructive" : "secondary"}
                        className="text-[10px]"
                      >
                        {isDueItem(item) ? "Due for review" : `Review: ${item.next_review_at}`}
                      </Badge>
                    ) : (
                      <Badge variant="destructive" className="text-[10px]">
                        Due for review
                      </Badge>
                    )}
                    <Badge variant="outline" className="text-[10px] text-muted-foreground">
                      Interval: {item.interval_days}d
                    </Badge>
                  </div>
                  <p className="text-sm text-foreground/90 leading-relaxed">{item.definition}</p>
                  {item.example && (
                    <p className="text-xs text-muted-foreground italic bg-secondary/40 p-2.5 rounded-lg border border-secondary/60">
                      &ldquo;{item.example}&rdquo;
                    </p>
                  )}
                </div>

                <Button
                  variant="ghost"
                  size="icon"
                  className="text-muted-foreground hover:text-destructive shrink-0"
                  onClick={() => deleteVocab(item.id)}
                  title="Delete word"
                  aria-label={`Delete ${item.word}`}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}

export default function VocabPage() {
  return (
    <Suspense
      fallback={
        <div className="max-w-4xl mx-auto p-8 text-center text-muted-foreground flex items-center justify-center gap-2">
          <Loader2 className="h-5 w-5 animate-spin text-primary" />
          <span>Loading vocabulary...</span>
        </div>
      }
    >
      <VocabContent />
    </Suspense>
  )
}
