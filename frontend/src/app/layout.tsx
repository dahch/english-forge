import type { Metadata, Viewport } from "next"
import { Inter } from "next/font/google"
import { ServiceWorkerRegistrar } from "@/components/service-worker-registrar"
import "./globals.css"

const inter = Inter({ subsets: ["latin"] })

export const metadata: Metadata = {
  title: "EnglishForge",
  description: "Personal English practice app — BYOK, self-hosted",
  manifest: "/manifest.json",
  appleWebApp: {
    capable: true,
    statusBarStyle: "black-translucent",
    title: "EnglishForge",
  },
  icons: {
    icon: "/icon-192.png",
    apple: "/apple-touch-icon.png",
  },
}

export const viewport: Viewport = {
  themeColor: "#09090b",
  width: "device-width",
  initialScale: 1,
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className={`${inter.className} bg-background text-foreground antialiased`}>
        {children}
        <ServiceWorkerRegistrar />
      </body>
    </html>
  )
}
