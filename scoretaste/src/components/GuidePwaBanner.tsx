import { useEffect, useMemo, useState } from "react";
import { useVisitorActions } from "../context/VisitorActionsContext";
import { wineStarLevel } from "../lib/visitorStorage";
import type { VisitorActionsBlob } from "../types";

function dismissKey(eventId: string): string {
  return `guide_pwa_banner_dismissed_${eventId}`;
}

function hasMarkedWine(blob: VisitorActionsBlob): boolean {
  for (const rec of Object.values(blob.actions)) {
    if (wineStarLevel(rec) >= 1) return true;
  }
  return false;
}

function addToHomeHint(): string {
  if (typeof navigator === "undefined") {
    return "";
  }
  const ua = navigator.userAgent || "";
  const isIOS = /iPhone|iPad|iPod/i.test(ua);
  const isAndroid = /Android/i.test(ua);
  if (isIOS) {
    return "Přidejte si průvodce na plochu → Sdílet → Přidat na plochu";
  }
  if (isAndroid) {
    return "Přidejte si průvodce na plochu → menu ⋮ → Přidat na plochu";
  }
  return "Přidejte si průvodce na plochu (v menu prohlížeče „Přidat na plochu“).";
}

type Props = { eventId: string };

export function GuidePwaBanner({ eventId }: Props) {
  const { blob } = useVisitorActions();
  const [dismissed, setDismissed] = useState(true);

  useEffect(() => {
    try {
      setDismissed(localStorage.getItem(dismissKey(eventId)) === "1");
    } catch {
      setDismissed(false);
    }
  }, [eventId]);

  const eligible = useMemo(() => hasMarkedWine(blob), [blob]);

  const visible = eligible && !dismissed;

  const dismiss = () => {
    try {
      localStorage.setItem(dismissKey(eventId), "1");
    } catch {
      /* ignore */
    }
    setDismissed(true);
  };

  if (!visible) return null;

  return (
    <div className="visitor-pwa-banner" role="status">
      <p className="visitor-pwa-banner-text">{addToHomeHint()}</p>
      <button
        type="button"
        className="visitor-pwa-banner-close"
        onClick={dismiss}
        aria-label="Zavřít"
      >
        ×
      </button>
    </div>
  );
}
