import type { Metadata } from 'next'
// Fonts ship with the app (no third-party font CDN): the weights globals.css used.
import '@fontsource/inter/400.css'
import '@fontsource/inter/500.css'
import '@fontsource/inter/600.css'
import '@fontsource/inter/700.css'
import '@fontsource/jetbrains-mono/400.css'
import '@fontsource/jetbrains-mono/500.css'
import '@fontsource/jetbrains-mono/600.css'
import './globals.css'
import { CookiePrivacyConsent } from '@/components/CookiePrivacyConsent'
import { IS_SELF_HOSTED } from '@/lib/constants'
import { MarketingSubscribePopup } from '@/components/MarketingSubscribePopup'

const TAGLINE =
  "Operational Database For AI Agents. Open source. Self-host free or managed cloud.";
const SITE_URL = process.env.DASHBOARD_URL || "https://db.zizka.ai";

export const viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export const metadata: Metadata = {
  title: {
    default: "ZizkaDB — Operational database for AI agents",
    template: "%s · ZizkaDB",
  },
  description: TAGLINE,
  metadataBase: new URL(SITE_URL),
  icons: {
    icon: [
      { url: "/icon-32.png", type: "image/png", sizes: "32x32" },
      { url: "/zizka-logo.png", type: "image/png" },
    ],
    shortcut: "/icon-32.png",
    apple: "/apple-touch-icon.png",
  },
  openGraph: {
    title: "ZizkaDB — Operational database for AI agents",
    description: TAGLINE,
    url: SITE_URL,
    siteName: "ZizkaDB",
    type: "website",
    images: [
      { url: "/opengraph-image", width: 1200, height: 630, alt: "ZizkaDB" },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "ZizkaDB — Operational database for AI agents",
    description: TAGLINE,
    images: ["/opengraph-image"],
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        {children}
        {/* Website-only: a self-hosted instance has no marketing or tracking. */}
        {!IS_SELF_HOSTED && <CookiePrivacyConsent />}
        {!IS_SELF_HOSTED && <MarketingSubscribePopup />}
      </body>
    </html>
  );
}
