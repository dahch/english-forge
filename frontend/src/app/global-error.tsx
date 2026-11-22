"use client"

// Last-resort boundary: it replaces the root layout, so it must render its own
// html/body and cannot rely on providers or Tailwind being mounted.
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "#09090b",
          color: "#fafafa",
          fontFamily: "system-ui, sans-serif",
        }}
      >
        <div style={{ maxWidth: 420, padding: 24, textAlign: "center" }}>
          <h1 style={{ fontSize: 20, marginBottom: 8 }}>EnglishForge could not start</h1>
          <p style={{ fontSize: 14, opacity: 0.75, marginBottom: 20 }}>
            An unexpected error broke the app shell. Reload to continue — your data is
            stored server-side.
          </p>
          {error.digest && (
            <p style={{ fontSize: 12, opacity: 0.6, marginBottom: 20 }}>
              Reference: {error.digest}
            </p>
          )}
          <button
            onClick={reset}
            style={{
              cursor: "pointer",
              borderRadius: 8,
              border: "1px solid #3f3f46",
              background: "#18181b",
              color: "#fafafa",
              padding: "10px 16px",
              fontSize: 14,
            }}
          >
            Reload the app
          </button>
        </div>
      </body>
    </html>
  )
}
