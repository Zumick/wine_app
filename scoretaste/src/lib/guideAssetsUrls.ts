/** Veřejné assety průvodce — servíruje Flask z `/guide/assets/` (Vite `base` je `/guide/`). */

function trimTrailingSlashes(s: string): string {
  return s.replace(/\/+$/, "");
}

export function guidePublicAssetsPrefix(): string {
  const base = import.meta.env.BASE_URL || "/guide/";
  return `${trimTrailingSlashes(base)}/assets/`;
}

/** Volitelné logo akce; při chybě načtení použij fallback `guideDefaultGuideLogoUrl()`. */
export function guideEventLogoPrimaryUrl(eventId: string): string {
  return `${guidePublicAssetsPrefix()}logo_${encodeURIComponent(eventId)}.png`;
}

export function guideDefaultGuideLogoUrl(): string {
  return `${guidePublicAssetsPrefix()}guide_logo.png`;
}

/** Volitelná mapa akce (JPEG). */
export function guideEventMapUrl(eventId: string): string {
  return `${guidePublicAssetsPrefix()}mapa_${encodeURIComponent(eventId)}.jpg`;
}

/** Zjištění existence mapy na serveru (bez globu / build seznamu). */
export async function probeEventMapExists(eventId: string): Promise<boolean> {
  const url = guideEventMapUrl(eventId);
  try {
    const res = await fetch(url, { method: "HEAD" });
    return res.ok;
  } catch {
    return false;
  }
}
