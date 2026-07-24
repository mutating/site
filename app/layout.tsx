import type { Metadata } from "next";
import { headers } from "next/headers";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

const title = "The Mutating Company — AI systems that doubt";
const description =
  "Deterministic verification, interpretability, and testing infrastructure for the AI agents writing tomorrow’s software.";

export async function generateMetadata(): Promise<Metadata> {
  const requestHeaders = await headers();
  const host =
    requestHeaders.get("x-forwarded-host") ??
    requestHeaders.get("host") ??
    "localhost:3000";
  const protocol =
    requestHeaders.get("x-forwarded-proto") ??
    (host.includes("localhost") ? "http" : "https");
  const ogImage = `${protocol}://${host}/og.png`;

  return {
    title,
    description,
    keywords: [
      "AI agents",
      "software verification",
      "interpretability",
      "software testing",
      "agent infrastructure",
    ],
    openGraph: {
      title,
      description,
      type: "website",
      images: [{ url: ogImage, width: 1672, height: 941, alt: title }],
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
      images: [ogImage],
    },
  };
}

const themeBootstrap = `
  (function () {
    var theme = 'dark';

    if (typeof matchMedia === 'function') {
      if (matchMedia('(prefers-color-scheme: dark)').matches) {
        theme = 'dark';
      } else if (matchMedia('(prefers-color-scheme: light)').matches) {
        theme = 'light';
      }
    }

    try {
      var stored = localStorage.getItem('tmc-theme');
      if (stored === 'dark' || stored === 'light') {
        theme = stored;
      }
    } catch (_) {}

    document.documentElement.dataset.theme = theme;
  })();
`;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <meta name="theme-color" content="#ff3e9d" />
        <script dangerouslySetInnerHTML={{ __html: themeBootstrap }} />
      </head>
      <body className={`${geistSans.variable} ${geistMono.variable}`}>
        {children}
      </body>
    </html>
  );
}
