import type { Wine } from "../types";

/** Case-insensitive, trim — skrýt odrůdu v sekundárním řádku, pokud je stejná jako label. */
export function labelMatchesVariety(wine: Wine): boolean {
  return (
    wine.label.trim().toLowerCase() === wine.variety.trim().toLowerCase()
  );
}

/** Sekundární řádek: odrůda (pokud ≠ label) · přívlastek · ročník. */
export function wineSecondaryLine(wine: Wine): string {
  const parts: string[] = [];
  if (!labelMatchesVariety(wine)) {
    parts.push(wine.variety.trim());
  }
  const pred = wine.predicate.trim();
  if (pred) parts.push(pred);
  parts.push(wine.vintage.trim());
  return parts.join(" · ");
}

export function wineryWebHref(raw: string): string {
  const w = raw.trim();
  if (!w) return "";
  return /^https?:\/\//i.test(w) ? w : `https://${w.replace(/^\/+/, "")}`;
}
