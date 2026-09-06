import type {
  Assessment,
  CEFRResult,
  ConversationTurn,
  DashboardStats,
  GeneratedLesson,
  LearningPath,
  Message,
  ProviderConfig,
  QuizQuestion,
  QuizResult,
  RecordingResult,
  Scenario,
  Session,
  SessionSummary,
  TutorProfile,
  User,
  VocabItem,
} from "./types"

// Same-origin by default: Next.js rewrites proxy /api/* to the backend
// service inside the docker network. Override only for special setups.
const API_BASE = process.env.NEXT_PUBLIC_API_URL || ""

export const SESSION_ID_KEY = "ef_active_session_id"

export function getActiveSessionId(): string | null {
  if (typeof window === "undefined") return null
  return localStorage.getItem(SESSION_ID_KEY)
}

export function setActiveSessionId(sessionId: string | null) {
  if (typeof window === "undefined") return
  if (sessionId) localStorage.setItem(SESSION_ID_KEY, sessionId)
  else localStorage.removeItem(SESSION_ID_KEY)
}

function getToken(): string | null {
  if (typeof window === "undefined") return null
  return localStorage.getItem("ef_token")
}

// Exported for components that need authenticated non-JSON fetches (e.g. the
// assessment audio player, which streams audio blobs).
export { getToken }

export function setToken(token: string) {
  localStorage.setItem("ef_token", token)
}

export function clearToken() {
  localStorage.removeItem("ef_token")
}

export function isAuthenticated(): boolean {
  return !!getToken()
}

let redirecting = false

// Typed error carrying the HTTP status, so callers can branch on status
// codes (e.g. 404 = "no active X") instead of matching error message text.
export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

function handleUnauthorized(): never {
  clearToken()
  setActiveSessionId(null)
  if (typeof window !== "undefined" && !redirecting) {
    redirecting = true
    window.location.href = "/login"
  }
  throw new Error("Unauthorized")
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken()
  // FormData bodies must not carry a JSON Content-Type — the browser sets
  // its own multipart boundary header.
  const isFormData = typeof FormData !== "undefined" && options.body instanceof FormData
  const headers: Record<string, string> = {
    ...(isFormData ? {} : { "Content-Type": "application/json" }),
    ...((options.headers as Record<string, string>) || {}),
  }
  if (token) {
    headers["Authorization"] = `Bearer ${token}`
  }

  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  })

  if (res.status === 401) {
    return handleUnauthorized()
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new ApiError(err.detail || "Request failed", res.status)
  }

  if (res.status === 204) return undefined as T
  return res.json()
}

export interface LessonExercise {
  question: string
  type: string
  explanation: string
  // multiple_choice only
  options?: string[]
}

export interface ExerciseCheckResult {
  correct: boolean
  correct_answer: string
  explanation: string
}

export interface LibraryLesson {
  id: string
  title: string
  topic: string
  level: string
  explanation: string
  examples: string[]
  // Present in the detail response only — the list returns exercise_count.
  exercises?: LessonExercise[]
  exercise_count: number
}

export const api = {
  auth: {
    register: (email: string, password: string, display_name: string) =>
      request<User>("/api/auth/register", {
        method: "POST",
        body: JSON.stringify({ email, password, display_name }),
      }),
    login: (email: string, password: string) =>
      request<{ access_token: string }>("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      }),
    me: () => request<User>("/api/auth/me"),
  },

  tutorProfile: {
    get: () => request<TutorProfile>("/api/tutor-profile"),
    update: (data: Partial<Omit<TutorProfile, "id" | "user_id">>) =>
      request<TutorProfile>("/api/tutor-profile", { method: "PATCH", body: JSON.stringify(data) }),
  },

  scenarios: {
    list: () => request<Scenario[]>("/api/scenarios"),
    create: (name: string, description: string, cefr_level: string) =>
      request<Scenario>("/api/scenarios", {
        method: "POST",
        body: JSON.stringify({ name, description, cefr_level }),
      }),
  },

  sessions: {
    create: (scenario_id: string | null, cefr_level: string) =>
      request<Session>("/api/sessions", {
        method: "POST",
        body: JSON.stringify({ scenario_id, cefr_level }),
      }),
    list: () => request<Session[]>("/api/sessions"),
    get: (id: string) => request<Session>(`/api/sessions/${id}`),
    end: (id: string) =>
      request<SessionSummary>(`/api/sessions/${id}/end`, { method: "PATCH" }),
    messages: (id: string) => request<Message[]>(`/api/sessions/${id}/messages`),
  },

  messages: {
    send: (sessionId: string, text: string) =>
      request<ConversationTurn>(`/api/sessions/${sessionId}/messages`, {
        method: "POST",
        body: JSON.stringify({ text }),
      }),
  },

  vocab: {
    list: (filter: string = "all") => request<VocabItem[]>(`/api/vocab?filter=${filter}`),
    create: (data: { word: string; definition: string; example: string }) =>
      request<VocabItem>("/api/vocab", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    update: (id: string, data: Partial<{ word: string; definition: string; example: string }>) =>
      request<VocabItem>(`/api/vocab/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
      }),
    delete: (id: string) => request<void>(`/api/vocab/${id}`, { method: "DELETE" }),
    review: (id: string, quality: number) =>
      request<VocabItem>(`/api/vocab/${id}/review`, {
        method: "POST",
        body: JSON.stringify({ quality }),
      }),
    due: (limit: number = 20) => request<VocabItem[]>(`/api/vocab/review/due?limit=${limit}`),
    generateQuiz: (count: number = 5) => request<QuizQuestion[]>(`/api/vocab/quiz/generate?count=${count}`),
    checkQuiz: (vocab_item_id: string, answer: string) =>
      request<QuizResult>("/api/vocab/quiz/check", {
        method: "POST",
        body: JSON.stringify({ vocab_item_id, answer }),
      }),
  },

  lessons: {
    library: () => request<LibraryLesson[]>("/api/lessons/library"),
    get: (id: string) => request<LibraryLesson>(`/api/lessons/library/${id}`),
    checkExercise: (lessonId: string, exerciseIndex: number, answer: string) =>
      request<ExerciseCheckResult>(`/api/lessons/library/${lessonId}/exercise`, {
        method: "POST",
        body: JSON.stringify({ exercise_index: exerciseIndex, answer }),
      }),
    generated: () => request<GeneratedLesson[]>("/api/lessons/generated"),
    checkGeneratedExercise: (lessonId: string, exerciseIndex: number, answer: string) =>
      request<ExerciseCheckResult>(`/api/lessons/generated/${lessonId}/exercise`, {
        method: "POST",
        body: JSON.stringify({ exercise_index: exerciseIndex, answer }),
      }),
    completeGenerated: (id: string, completed: boolean = true) =>
      request<{ id: string; completed: boolean }>(`/api/lessons/generated/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ completed }),
      }),
    generate: () => request<GeneratedLesson>("/api/lessons/generate", { method: "POST" }),
  },

  assessment: {
    current: () => request<Assessment>("/api/assessment/current"),
    start: () => request<Assessment>("/api/assessment/start", { method: "POST" }),
    send: (id: string, text: string, source: "text" | "voice" = "text", metrics?: Record<string, unknown> | null) =>
      request<Assessment>(`/api/assessment/${id}/message`, {
        method: "POST",
        body: JSON.stringify({ text, source, metrics: metrics ?? null }),
      }),
    // Upload a recording for STT (Moonshine/whisper) and, when the phase has
    // an expected text, pronunciation scoring.
    uploadRecording: (id: string, blob: Blob) => {
      const form = new FormData()
      form.append("file", blob, "answer.webm")
      return request<RecordingResult>(`/api/assessment/${id}/recordings`, {
        method: "POST",
        body: form,
      })
    },
    complete: (id: string) =>
      request<Assessment>(`/api/assessment/${id}/complete`, { method: "POST" }),
    reanalyze: (id: string) =>
      request<Assessment>(`/api/assessment/${id}/reanalyze`, { method: "POST" }),
  },

  learningPath: {
    current: () => request<LearningPath>("/api/learning-paths/current"),
    generate: (assessmentId?: string) =>
      request<LearningPath>("/api/learning-paths/generate", {
        method: "POST",
        body: JSON.stringify(assessmentId ? { assessment_id: assessmentId } : {}),
      }),
    completeLesson: (pathId: string, lessonId: string, completed: boolean = true) =>
      request<LearningPath>(`/api/learning-paths/${pathId}/lessons/${lessonId}/complete`, {
        method: "PATCH",
        body: JSON.stringify({ completed }),
      }),
    advance: (pathId: string) =>
      request<LearningPath>(`/api/learning-paths/${pathId}/advance`, { method: "POST" }),
  },

  dashboard: {
    stats: () => request<DashboardStats>("/api/dashboard/stats"),
    weekly: (weeks: number = 4) =>
      request<{ week: string; minutes: number; new_words: number; reviews: number; lessons: number }[]>(
        `/api/dashboard/weekly?weeks=${weeks}`
      ),
    cefr: () => request<CEFRResult>("/api/dashboard/cefr"),
  },

  settings: {
    getProviders: () => request<ProviderConfig[]>("/api/settings/providers"),
    createProvider: (data: {
      provider_name: string
      api_key: string
      base_url: string
      model: string
      protocol: string
      priority: number
      task_routing: string
    }) =>
      request<ProviderConfig>("/api/settings/providers", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    updateProvider: (id: string, data: Record<string, unknown>) =>
      request<ProviderConfig>(`/api/settings/providers/${id}`, {
        method: "PATCH",
        body: JSON.stringify(data),
      }),
    deleteProvider: (id: string) => request<void>(`/api/settings/providers/${id}`, { method: "DELETE" }),
    get: () => request<{ key: string; value: string }[]>("/api/settings"),
    update: (data: Record<string, unknown>) =>
      request<{ key: string; value: string }[]>("/api/settings", {
        method: "PATCH",
        body: JSON.stringify(data),
      }),
    clearData: () => request<{ message: string }>("/api/settings/clear-data", { method: "DELETE" }),
  },
}
