/** Veřejný původ (bez koncového lomítka) pro sdílení a QR. */
export function guidePublicOrigin(): string {
  const env = import.meta.env.VITE_PUBLIC_GUIDE_ORIGIN as string | undefined;
  if (env && String(env).trim()) {
    return String(env).replace(/\/$/, "");
  }
  if (typeof window !== "undefined") {
    return window.location.origin;
  }
  return "";
}

/** Kanonický vstup do akce (přesměruje na vinařství). */
export function guideEventEntryUrl(eventId: string): string {
  return `${guidePublicOrigin()}/guide/e/${encodeURIComponent(eventId)}`;
}
