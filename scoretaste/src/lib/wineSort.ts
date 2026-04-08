import type { Wine } from "../types";

/** Pořadí skupin: Bílé → Růžové → Červené → Oranžové */
const COLOR_ORDER: Record<string, number> = {
  white: 0,
  rose: 1,
  red: 2,
  orange: 3,
};

const SECTION_ORDER = ["white", "rose", "red", "orange"] as const;

export function normalizeWineColor(
  c: string | undefined,
): "white" | "red" | "rose" | "orange" {
  const v = (c || "white").trim().toLowerCase();
  if (v in COLOR_ORDER) return v as "white" | "red" | "rose" | "orange";
  return "white";
}

/** Řazení: white → rose → red → orange, pak název (cs). */
export function compareWinesByColorThenLabel(a: Wine, b: Wine): number {
  const ca = COLOR_ORDER[normalizeWineColor(a.color)] ?? 99;
  const cb = COLOR_ORDER[normalizeWineColor(b.color)] ?? 99;
  if (ca !== cb) return ca - cb;
  return a.label.localeCompare(b.label, "cs");
}

/**
 * Seskupí vína podle barvy v pevném pořadí; uvnitř skupiny řadí podle názvu.
 * Vrací jen neprázdné skupiny (pro podnadpisy).
 */
export function groupWinesByColorSections(wines: Wine[]): {
  color: (typeof SECTION_ORDER)[number];
  wines: Wine[];
}[] {
  const byColor = new Map<string, Wine[]>();
  for (const w of wines) {
    const c = normalizeWineColor(w.color);
    if (!byColor.has(c)) byColor.set(c, []);
    byColor.get(c)!.push(w);
  }
  const out: { color: (typeof SECTION_ORDER)[number]; wines: Wine[] }[] = [];
  for (const c of SECTION_ORDER) {
    const list = byColor.get(c);
    if (!list?.length) continue;
    list.sort((a, b) => a.label.localeCompare(b.label, "cs"));
    out.push({ color: c, wines: list });
  }
  return out;
}
