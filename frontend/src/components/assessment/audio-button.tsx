"use client"

import { useEffect, useRef, useState } from "react"
import { Loader2, Pause, Volume2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { getToken } from "@/lib/api"

// Play button for assessment tutor audio. Fetches the message audio with the
// auth header (data URIs would bloat every messages poll), caches the blob
// per message, and toggles play/pause. Falls back to a disabled state when
// the backend can't synthesize (personal-api down).

export function AudioButton({
  assessmentId,
  messageId,
  disabled,
}: {
  assessmentId: string
  messageId: string
  disabled?: boolean
}) {
  const [state, setState] = useState<"idle" | "loading" | "playing">("idle")
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const blobRef = useRef<string | null>(null)

  // Release the object URL on unmount so long assessment sessions don't leak.
  useEffect(() => {
    return () => {
      audioRef.current?.pause()
      if (blobRef.current) URL.revokeObjectURL(blobRef.current)
    }
  }, [])

  async function toggle() {
    if (state === "loading") return

    if (state === "playing") {
      audioRef.current?.pause()
      setState("idle")
      return
    }

    try {
      setState("loading")
      if (!blobRef.current) {
        const res = await fetch(`/api/assessment/${assessmentId}/messages/${messageId}/audio`, {
          headers: { Authorization: `Bearer ${getToken()}` },
        })
        if (!res.ok) throw new Error(res.statusText)
        const blob = await res.blob()
        blobRef.current = URL.createObjectURL(blob)
      }
      const audio = new Audio(blobRef.current)
      audioRef.current = audio
      audio.onended = () => setState("idle")
      await audio.play()
      setState("playing")
    } catch {
      // Synthesis failed — surface a neutral idle state; the UI shows the
      // item text as fallback in that case.
      setState("idle")
      if (blobRef.current) {
        URL.revokeObjectURL(blobRef.current)
        blobRef.current = null
      }
    }
  }

  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={toggle}
      disabled={disabled || state === "loading"}
      className="h-7 px-2"
      aria-label={state === "playing" ? "Pause audio" : "Play audio"}
    >
      {state === "loading" ? (
        <Loader2 className="h-4 w-4 animate-spin" />
      ) : state === "playing" ? (
        <Pause className="h-4 w-4" />
      ) : (
        <Volume2 className="h-4 w-4" />
      )}
    </Button>
  )
}
