import Link from "next/link"
import { Compass } from "lucide-react"

import { Button } from "@/components/ui/button"

export default function NotFound() {
  return (
    <div className="flex min-h-[60vh] items-center justify-center p-6">
      <div className="w-full max-w-md space-y-4 rounded-xl border bg-card p-6 text-center shadow-sm">
        <Compass className="mx-auto h-10 w-10 text-primary" />
        <div className="space-y-1">
          <h1 className="text-lg font-semibold">Page not found</h1>
          <p className="text-sm text-muted-foreground">
            The page you were looking for doesn&apos;t exist or was moved.
          </p>
        </div>
        <Button asChild>
          <Link href="/conversation">Go to conversation</Link>
        </Button>
      </div>
    </div>
  )
}
