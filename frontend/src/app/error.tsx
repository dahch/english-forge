"use client"

import Link from "next/link"
import { useEffect } from "react"
import { AlertCircle, Home, RefreshCw } from "lucide-react"

import { Button } from "@/components/ui/button"

// Route-level error boundary: a render/loader exception used to leave a blank
// screen with no way forward. Keeps the user inside the app and offers retry.
export default function AppError({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  useEffect(() => {
    console.error(error)
  }, [error])

  return (
    <div className="flex min-h-[60vh] items-center justify-center p-6">
      <div className="w-full max-w-md space-y-4 rounded-xl border bg-card p-6 text-center shadow-sm">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-destructive/10">
          <AlertCircle className="h-6 w-6 text-destructive" />
        </div>
        <div className="space-y-1">
          <h1 className="text-lg font-semibold">Something went wrong</h1>
          <p className="text-sm text-muted-foreground">
            This screen failed to load. Your saved data is unaffected.
          </p>
          {error.digest && (
            <p className="text-xs text-muted-foreground">Error reference: {error.digest}</p>
          )}
        </div>
        <div className="flex flex-col gap-2 sm:flex-row sm:justify-center">
          <Button onClick={reset}>
            <RefreshCw className="mr-1 h-4 w-4" />
            Try again
          </Button>
          <Button variant="outline" asChild>
            <Link href="/conversation">
              <Home className="mr-1 h-4 w-4" />
              Back to conversation
            </Link>
          </Button>
        </div>
      </div>
    </div>
  )
}
