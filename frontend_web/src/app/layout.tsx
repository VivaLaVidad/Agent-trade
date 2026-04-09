import type { Metadata, Viewport } from "next";
import { Providers } from "./providers";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "OmniEdge — 全域工联",
    template: "%s | OmniEdge",
  },
  description:
    "AI-native cross-border industrial B2B trade platform. Compliance-first procurement, zero-trust audit trails, and frictionless ASEAN export workflows.",
  keywords: [
    "B2B trade",
    "cross-border",
    "industrial procurement",
    "ASEAN export",
    "AI trade platform",
    "OmniEdge",
    "全域工联",
    "supply chain",
    "compliance",
  ],
  authors: [{ name: "OmniEdge" }],
  creator: "OmniEdge (全域工联)",
  metadataBase: new URL("https://omniedge.trade"),
  openGraph: {
    type: "website",
    locale: "en_US",
    url: "https://omniedge.trade",
    siteName: "OmniEdge — 全域工联",
    title: "OmniEdge — AI-Native Industrial Trade Network",
    description:
      "Compliance-first procurement, zero-trust audit trails, and frictionless ASEAN export workflows.",
  },
  twitter: {
    card: "summary_large_image",
    title: "OmniEdge — 全域工联",
    description:
      "AI-native cross-border industrial B2B trade platform.",
  },
  manifest: "/manifest.json",
  icons: {
    icon: "/globe.svg",
  },
};

export const viewport: Viewport = {
  themeColor: "#09090b",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="min-h-screen antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
