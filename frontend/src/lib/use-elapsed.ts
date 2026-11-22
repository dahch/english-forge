"use client"

import { useEffect, useState } from "react"

/**
 * Seconds since `active` became true, re-rendering only when the displayed
 * second changes. Used to show "thinking… 12 s" so a long LLM call never looks
 * frozen.
 *
 * The tick is driven by requestAnimationFrame (state updates happen in the
 * callback, never synchronously inside the effect) and stops when the tab is
 * hidden or the operation ends.
 */
export function useElapsed(active: boolean): number {
  const [seconds, setSeconds] = useState(0)

  useEffect(() => {
    if (!active) return
    const startedAt = Date.now()
    let displayed = -1
    let frame = 0

    const tick = () => {
      const elapsed = Math.floor((Date.now() - startedAt) / 1000)
      if (elapsed !== displayed) {
        displayed = elapsed
        setSeconds(elapsed)
      }
      frame = requestAnimationFrame(tick)
    }

    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [active])

  // Returning 0 while inactive avoids a stale value flashing on the next run.
  return active ? seconds : 0
}

export function formatElapsed(totalSeconds: number): string {
  if (totalSeconds < 60) return `${totalSeconds} s`
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes} min ${seconds.toString().padStart(2, "0")} s`
}
