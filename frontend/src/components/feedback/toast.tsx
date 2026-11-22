"use client"

// Minimal toast system: no dependency, accessible (role=status / alert),
// auto-dismiss for success/info and sticky for errors until dismissed.
// Rendered once from the root layout so every route (including auth) can
// report failures without inventing its own inline banner.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react"
import { AlertCircle, CheckCircle2, Info, X } from "lucide-react"

import { cn } from "@/lib/utils"

export type ToastVariant = "success" | "error" | "info"

export interface ToastOptions {
  title: string
  description?: string
  variant?: ToastVariant
  /** 0 = sticky (used for errors). */
  durationMs?: number
}

interface ToastItem extends ToastOptions {
  id: number
  variant: ToastVariant
}

interface ToastContextValue {
  toast: (options: ToastOptions) => void
  dismiss: (id: number) => void
}

const ToastContext = createContext<ToastContextValue | null>(null)

const DEFAULT_DURATION_MS = 6000

const VARIANT_STYLES: Record<ToastVariant, string> = {
  success: "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  error: "border-destructive/40 bg-destructive/10 text-destructive",
  info: "border-primary/30 bg-secondary text-foreground",
}

function VariantIcon({ variant }: { variant: ToastVariant }) {
  if (variant === "success") return <CheckCircle2 className="h-4 w-4 shrink-0" />
  if (variant === "error") return <AlertCircle className="h-4 w-4 shrink-0" />
  return <Info className="h-4 w-4 shrink-0" />
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])

  const dismiss = useCallback((id: number) => {
    setItems((current) => current.filter((item) => item.id !== id))
  }, [])

  const toast = useCallback(
    ({ title, description, variant = "info", durationMs = DEFAULT_DURATION_MS }: ToastOptions) => {
      const id = Date.now() + Math.random()
      setItems((current) => [...current.slice(-4), { id, title, description, variant, durationMs }])
    },
    [],
  )

  const value = useMemo(() => ({ toast, dismiss }), [toast, dismiss])

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        role="region"
        aria-label="Notifications"
        className="pointer-events-none fixed inset-x-0 bottom-20 z-[100] flex flex-col items-center gap-2 px-4 md:bottom-6 md:inset-x-auto md:right-6 md:items-end"
      >
        {items.map((item) => (
          <ToastCard key={item.id} item={item} onDismiss={dismiss} />
        ))}
      </div>
    </ToastContext.Provider>
  )
}

function ToastCard({
  item,
  onDismiss,
}: {
  item: ToastItem
  onDismiss: (id: number) => void
}) {
  useEffect(() => {
    if (!item.durationMs) return
    const timer = setTimeout(() => onDismiss(item.id), item.durationMs)
    return () => clearTimeout(timer)
  }, [item.durationMs, item.id, onDismiss])

  const sticky = item.variant === "error" || item.durationMs === 0

  return (
    <div
      role={item.variant === "error" ? "alert" : "status"}
      aria-live={item.variant === "error" ? "assertive" : "polite"}
      className={cn(
        "pointer-events-auto flex w-full max-w-sm items-start gap-2 rounded-lg border p-3 text-sm shadow-lg backdrop-blur",
        VARIANT_STYLES[item.variant],
        sticky && "items-start",
      )}
    >
      <VariantIcon variant={item.variant} />
      <div className="min-w-0 flex-1">
        <p className="font-medium">{item.title}</p>
        {item.description && (
          <p className="mt-0.5 text-xs opacity-90 break-words">{item.description}</p>
        )}
      </div>
      <button
        type="button"
        onClick={() => onDismiss(item.id)}
        aria-label="Dismiss notification"
        className="rounded p-0.5 opacity-70 transition-opacity hover:opacity-100 focus:outline-none focus:ring-2 focus:ring-ring"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  )
}

export function useToast(): ToastContextValue {
  const context = useContext(ToastContext)
  if (!context) {
    throw new Error("useToast must be used inside <ToastProvider>")
  }
  return context
}
