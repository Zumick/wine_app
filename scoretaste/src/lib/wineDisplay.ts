import type { Wine } from "../types";

/** Sekundární řádek: odrůda · přívlastek · ročník (přívlastek jen pokud neprázdný). */
export function wineSecondaryLine(wine: Wine): string {
  const parts = [wine.variety.trim()];
  const pred = wine.predicate.trim();
  if (pred) parts.push(pred);
  parts.push(wine.vintage.trim());
  return parts.join(" · ");
}
