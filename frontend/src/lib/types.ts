export interface User {
  id: string
  email: string
  display_name: string
  current_level: string
  assessment_completed: boolean
  created_at?: string
}

export interface TutorProfile {
  id: string
  user_id: string
  name: string
  age: number | null
  gender: string | null
  personality: string
  voice: string | null
}

export interface TokenResponse {
  access_token: string
  token_type: string
}

export interface Scenario {
  id: string
  name: string
  system_prompt: string
  cefr_level: string
  is_custom: boolean
}

export interface Session {
  id: string
  scenario_id: string | null
  started_at: string
  ended_at: string | null
  cefr_level: string
  provider_used: string | null
  scenario_name: string | null
  message_count: number
  initial_message?: Message
}

export interface Correction {
  id: string
  error_type: string
  original_fragment: string
  correction: string
  explanation: string
}

export interface Message {
  id: string
  session_id: string
  role: string
  text: string
  // Always null from the API; audio is fetched from the authenticated endpoint.
  audio_url: string | null
  has_audio?: boolean
  created_at: string
  corrections: Correction[]
}

export interface ConversationTurn {
  user_message: Message
  assistant_message: Message
  corrections: Correction[]
  new_vocab: { word: string; definition: string; example: string }[]
  audio_url: string | null
}

export interface SessionSummary {
  session_id: string
  duration_minutes: number
  total_messages: number
  corrections_by_type: Record<string, number>
  total_corrections: number
  new_words_learned: number
  corrections_list: Correction[]
  new_vocab_list: { word: string; definition: string; example: string }[]
}

export interface VocabItem {
  id: string
  word: string
  definition: string
  example: string
  ipa: string | null
  ease_factor: number
  interval_days: number
  next_review_at: string | null
  last_reviewed_at: string | null
  created_at: string
}

export interface GeneratedLesson {
  id: string
  title: string
  topic: string | null
  level: string | null
  explanation: string
  examples: string[]
  exercises: { question: string; type: string; options?: string[]; explanation?: string }[]
  based_on_errors: string | null
  completed: boolean
  completed_at: string | null
  created_at: string
  // "fallback" means the model failed/timed out and the deterministic template
  // was stored instead — the UI must say so, never pass it off as personalized.
  source?: "llm" | "fallback"
}

export interface LibraryLesson {
  id: string
  title: string
  topic: string
  level: string
  explanation: string
  examples: string[]
  exercises?: LessonExercise[]
  exercise_count: number
  completed?: boolean
  completed_at?: string | null
}

export interface LessonGenerateRequest {
  topic?: string
  level?: string
}

export interface ProviderConfig {
  id: string
  provider_name: string
  base_url: string
  model: string
  protocol: string
  is_active: boolean
  priority: number
  task_routing: string
  has_api_key: boolean
}

export interface ProviderTestResult {
  ok: boolean
  provider?: string | null
  model?: string | null
  latency_ms?: number | null
  reply?: string | null
  error?: string | null
}

export interface DashboardStats {
  total_sessions: number
  total_minutes: number
  total_words_learned: number
  total_reviews: number
  current_streak: number
  due_for_review: number
  current_level: string
  assessment_completed: boolean
  last_assessment: {
    estimated_level: string | null
    confidence: number
    completed_at: string
  } | null
  learning_path: {
    id: string
    current_level: string
    target_level: string
    lessons_completed: number
    lessons_required: number
    percent: number
  } | null
}

export interface CEFRResult {
  estimated_level: string
  confidence: number
  metrics: Record<string, number>
}

export interface QuizQuestion {
  vocab_item_id: string
  question_type: string
  question: string
  options: string[]
  correct_answer: string
}

export interface QuizResult {
  correct: boolean
  correct_answer: string
  explanation: string
}

// Assessment chat messages. `kind` drives the section UI: "chat" (tutor
// conversation), "mic_check" (read-aloud calibration), "listening" (audio-only
// item — text hidden until answered), "speaking" (read-aloud item). Tutor
// audio plays via a fetch-with-auth endpoint (see AudioButton); the raw data
// URI is never serialized. metrics holds per-answer evidence (listening grade
// or pronunciation WER/PER/fluency composite, computed server-side).
export interface AssessmentMessage {
  id: string
  role: string
  text: string
  kind: string
  metrics: Record<string, unknown> | null
  created_at: string
}

// Dimension scores (0-100) computed deterministically at analysis time.
// Missing keys mean the dimension was not assessed.
export type DimensionScores = {
  grammar?: number
  vocabulary?: number
  fluency?: number
  listening?: number
  pronunciation?: number
}

export interface Assessment {
  id: string
  started_at: string
  completed_at: string | null
  estimated_level: string | null
  confidence: number | null
  strengths: string[] | null
  weaknesses: string[] | null
  recommendations: string[] | null
  summary: string | null
  phase: string | null
  dimension_scores: DimensionScores | null
  // Difficulty-weighted CEFR band per dimension (final level = median).
  dimension_bands?: Record<string, string | null> | null
  messages: AssessmentMessage[]
  is_complete?: boolean
  // Server-computed progress for the section stepper ("item X of Y").
  section_step?: number
  section_total?: number
  questions_asked?: number
}

// Result of POST /assessment/{id}/recordings — STT transcript with Moonshine
// word timestamps (when personal-api exposes them) and optional pronunciation
// metrics computed against the item's expected text.
export interface RecordingResult {
  transcript: string
  words: { word: string; start: number; end: number }[]
  metrics: Record<string, unknown> | null
}

export interface LearningPath {
  id: string
  current_level: string
  target_level: string
  lessons_required: number
  lessons_completed: number
  created_at: string
  completed_at: string | null
  is_active: boolean
  // Transient provenance: "curriculum" = deterministic seed because the model
  // failed/timed out; "unknown" for paths read back from the DB.
  generation_source?: "llm" | "curriculum" | "unknown"
  path_lessons: PathLesson[]
}

export interface PathLesson {
  id: string
  path_id: string
  lesson_type: string
  topic: string
  description: string
  // Backend JSON-parses the stored content column, so it arrives as an object.
  content: PathLessonContent | null
  order: number
  completed: boolean
  completed_at: string | null
}

export interface LessonExercise {
  question: string
  type: string
  options?: string[]
  explanation?: string
}

export interface ExerciseCheckResult {
  correct: boolean
  correct_answer: string
  explanation: string
}

export interface VocabItemPayload {
  word: string
  definition: string
  ipa?: string | null
  example: string
}

// Interactive content of a path lesson. Metadata (focus/lesson_type) is stored
// at path-generation time; multi-type content is generated lazily on first open
// (POST .../generate, idempotent). Answers never reach the client — grading
// happens server-side.
export interface PathLessonContent {
  focus?: string
  lesson_type?: string
  title?: string
  explanation?: string
  examples?: string[]
  formula?: string
  // Conversation
  scenario_context?: string
  goal?: string
  tutor_role?: string
  target_expressions?: string[]
  // Vocabulary
  items?: VocabItemPayload[]
  vocab_items?: VocabItemPayload[]
  // Listening & Reading / Writing
  passage?: string
  transcript?: string
  audio_url?: string
  writing_prompt?: string
  // Interactive Exercises
  exercises?: LessonExercise[]
}

// Shape of POST /learning-paths/{path_id}/lessons/{lesson_id}/generate.
// Structurally identical to PathLesson — a PathLesson can be used as the
// loading stub while the detail request is in flight.
export type PathLessonDetail = PathLesson

export type CEFRLevel = "A1" | "A2" | "B1" | "B2" | "C1" | "C2"

export const CEFR_LEVELS: CEFRLevel[] = ["A1", "A2", "B1", "B2", "C1", "C2"]
