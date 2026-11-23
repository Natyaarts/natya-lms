"use client";

import { useEffect, useState } from "react";
import Image from "next/image";
import { Download, Share, X } from "lucide-react";

// Chromium's install event (not in the standard DOM typings).
interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

const DISMISS_KEY = "natya_pwa_install_dismissed_at";
const DISMISS_DAYS = 14;

function isStandalone() {
  return (
    window.matchMedia("(display-mode: standalone)").matches ||
    (navigator as Navigator & { standalone?: boolean }).standalone === true
  );
}

function recentlyDismissed() {
  try {
    const at = Number(localStorage.getItem(DISMISS_KEY) || 0);
    return at > 0 && Date.now() - at < DISMISS_DAYS * 24 * 60 * 60 * 1000;
  } catch {
    return false;
  }
}

/**
 * Registers the service worker (production only) and offers installation:
 * the native prompt on Chromium/Android, and "Share -> Add to Home Screen"
 * instructions on iOS Safari, which has no install prompt.
 */
export default function PwaSetup() {
  const [installEvent, setInstallEvent] = useState<BeforeInstallPromptEvent | null>(null);
  const [showIosHint, setShowIosHint] = useState(false);

  useEffect(() => {
    if (process.env.NODE_ENV === "production" && "serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js", { scope: "/", updateViaCache: "none" }).catch(() => {});
    }

    if (isStandalone() || recentlyDismissed()) return;

    const onBeforeInstall = (e: Event) => {
      e.preventDefault();
      setInstallEvent(e as BeforeInstallPromptEvent);
    };
    const onInstalled = () => setInstallEvent(null);
    window.addEventListener("beforeinstallprompt", onBeforeInstall);
    window.addEventListener("appinstalled", onInstalled);

    const ua = navigator.userAgent;
    const isIos = /iPad|iPhone|iPod/.test(ua) || (ua.includes("Macintosh") && navigator.maxTouchPoints > 1);
    const isSafari = /Safari/.test(ua) && !/CriOS|FxiOS|EdgiOS/.test(ua);
    const timer = isIos && isSafari ? window.setTimeout(() => setShowIosHint(true), 2500) : undefined;

    return () => {
      window.removeEventListener("beforeinstallprompt", onBeforeInstall);
      window.removeEventListener("appinstalled", onInstalled);
      if (timer) window.clearTimeout(timer);
    };
  }, []);

  const dismiss = () => {
    try {
      localStorage.setItem(DISMISS_KEY, String(Date.now()));
    } catch {}
    setInstallEvent(null);
    setShowIosHint(false);
  };

  const install = async () => {
    if (!installEvent) return;
    await installEvent.prompt();
    await installEvent.userChoice.catch(() => null);
    setInstallEvent(null);
  };

  if (!installEvent && !showIosHint) return null;

  return (
    <div
      role="dialog"
      aria-label="Install the Natya app"
      className="fixed z-30 left-3 right-3 bottom-3 sm:left-auto sm:right-4 sm:bottom-4 sm:w-80 bg-zinc-900/95 backdrop-blur border border-white/10 rounded-2xl shadow-2xl shadow-black/60 p-4 flex gap-3"
      style={{ marginBottom: "env(safe-area-inset-bottom)" }}
    >
      <Image src="/icon-192.png" alt="" width={44} height={44} className="w-11 h-11 rounded-xl shrink-0" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-white">Install the Natya app</p>
        {installEvent ? (
          <>
            <p className="text-xs text-zinc-400 mt-0.5">Open your classes and lessons straight from your home screen.</p>
            <button
              type="button"
              onClick={install}
              className="mt-3 inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg bg-[#facc15] hover:bg-yellow-400 text-black text-xs font-semibold transition-colors"
            >
              <Download className="w-3.5 h-3.5" /> Install
            </button>
          </>
        ) : (
          <p className="text-xs text-zinc-400 mt-0.5 leading-relaxed">
            Tap <Share className="inline w-3.5 h-3.5 -mt-0.5 text-[#facc15]" /> <span className="text-zinc-200">Share</span>, then{" "}
            <span className="text-zinc-200">Add to Home Screen</span>.
          </p>
        )}
      </div>
      <button
        type="button"
        onClick={dismiss}
        aria-label="Dismiss"
        className="self-start p-1 -m-1 rounded-lg text-zinc-500 hover:text-white hover:bg-white/5 transition-colors"
      >
        <X className="w-4 h-4" />
      </button>
    </div>
  );
}
