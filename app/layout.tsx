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

const anchorBootstrap = `
  (function () {
    if (!window.location.hash) return;

    window.__tmcAnchorAlignmentInterrupted = false;
    var frameRequest = 0;

    function cancelPendingAlignment() {
      window.__tmcAnchorAlignmentInterrupted = true;
      if (frameRequest) {
        window.cancelAnimationFrame(frameRequest);
        frameRequest = 0;
      }
    }

    function cancelPendingAlignmentFromKey(event) {
      var target = event.target;
      var isInteractive =
        target instanceof Element &&
        target.closest(
          'a[href], button, input, select, textarea, summary, [contenteditable="true"]'
        );
      if (
        isInteractive &&
        (
          event.key === 'ArrowUp' ||
          event.key === 'ArrowDown' ||
          event.key === 'Home' ||
          event.key === 'End' ||
          event.key === ' '
        )
      ) {
        return;
      }

      if (
        event.key === 'ArrowUp' ||
        event.key === 'ArrowDown' ||
        event.key === 'PageUp' ||
        event.key === 'PageDown' ||
        event.key === 'Home' ||
        event.key === 'End' ||
        event.key === ' '
      ) {
        cancelPendingAlignment();
      }
    }

    function cancelPendingAlignmentFromWheel(event) {
      if (
        !event.ctrlKey &&
        Number.isFinite(event.deltaY) &&
        event.deltaY !== 0
      ) {
        cancelPendingAlignment();
      }
    }

    function cancelPendingAlignmentFromFrame(event) {
      var frame = document.querySelector('.contact__tentacles');
      var deltaY = Number(event.data && event.data.deltaY);
      if (
        event.origin === window.location.origin &&
        frame &&
        event.source === frame.contentWindow &&
        event.data &&
        event.data.type === 'muta-tentacles-scroll-input' &&
        Number.isFinite(deltaY) &&
        deltaY !== 0
      ) {
        cancelPendingAlignment();
      }
    }

    function findHashTarget() {
      var encodedId = window.location.hash.slice(1);
      var id = encodedId;

      try {
        id = decodeURIComponent(encodedId);
      } catch (_) {}

      return document.getElementById(id);
    }

    function scrollToHash() {
      if (window.__tmcAnchorAlignmentInterrupted) return;

      var target = findHashTarget();
      if (!target) return;

      var root = document.documentElement;
      var previousScrollBehavior = root.style.scrollBehavior;
      root.style.scrollBehavior = 'auto';
      if (target.id === 'top') {
        window.scrollTo({ top: 0, behavior: 'auto' });
      } else {
        target.scrollIntoView({ block: 'start' });
      }
      root.style.scrollBehavior = previousScrollBehavior;
    }

    function alignHashTarget() {
      if (window.__tmcAnchorAlignmentInterrupted) return;

      scrollToHash();
      if (frameRequest) {
        window.cancelAnimationFrame(frameRequest);
      }
      frameRequest = window.requestAnimationFrame(function () {
        frameRequest = 0;
        scrollToHash();
      });
    }

    function restartHashAlignment() {
      window.__tmcAnchorAlignmentInterrupted = false;
      alignHashTarget();
    }

    function handlePageShow(event) {
      if (event.persisted) {
        restartHashAlignment();
      } else {
        alignHashTarget();
      }
    }

    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', alignHashTarget, {
        once: true,
      });
    } else {
      alignHashTarget();
    }

    window.addEventListener('load', alignHashTarget, { once: true });
    window.addEventListener('pageshow', handlePageShow);
    window.addEventListener('hashchange', restartHashAlignment);
    window.addEventListener('wheel', cancelPendingAlignmentFromWheel, {
      passive: true,
      capture: true,
    });
    window.addEventListener('touchmove', cancelPendingAlignment, {
      passive: true,
      capture: true,
    });
    window.addEventListener(
      'keydown',
      cancelPendingAlignmentFromKey,
      true,
    );
    window.addEventListener(
      'message',
      cancelPendingAlignmentFromFrame,
    );
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
        <script dangerouslySetInnerHTML={{ __html: anchorBootstrap }} />
      </head>
      <body className={`${geistSans.variable} ${geistMono.variable}`}>
        {children}
      </body>
    </html>
  );
}
