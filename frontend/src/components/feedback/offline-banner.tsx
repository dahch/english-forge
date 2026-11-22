"use client"

import { useEffect, useState } from "react"
import { WifiOff } from "lucide-react"

// Persistent, low-noise connection state. Without it, every failed request
// surfaces as an unrelated inline error ("Failed to load") and users retry
// blindly while the network is down.
export function OfflineBanner() {
  const [offline, setOffline] = useState(false)

  useEffect(() => {
    const update = () => setOffline(!navigator.onLine)
    update()
    window.addEventListener("online", update)
    window.addEventListener("offline", update)
    return () => {
      window.removeEventListener("online", update)
      window.removeEventListener("offline", update)
    }
  }, [])

  if (!offline) return null

  return (
    <div
      role="status"
      aria-live="polite"
      className="pointer-events-none fixed inset-x-0 top-2 z-[90] flex justify-center px-4"
    >
      <div className="pointer-events-auto flex items-center gap-2 rounded-full border border-amber-500/40 bg-amber-500/15 px-3 py-1.5 text-xs font-medium text-amber-700 shadow-sm backdrop-blur dark:text-amber-300">
        <WifiOff className="h-3.5 w-3.5" />
        You&apos;re offline — responses can&apos;t be saved until the connection returns.
      </div>
    </div>
  )
}
