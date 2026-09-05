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
  audio_url: string | null
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
    estimated_level: string
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
  messages: Message[]
  is_complete?: boolean
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
  path_lessons: PathLesson[]
}

export interface PathLesson {
  id: string
  path_id: string
  lesson_type: string
  topic: string
  description: string
  content: string | null
  order: number
  completed: boolean
  completed_at: string | null
}

export type CEFRLevel = "A1" | "A2" | "B1" | "B2" | "C1" | "C2"

export const CEFR_LEVELS: CEFRLevel[] = ["A1", "A2", "B1", "B2", "C1", "C2"]
