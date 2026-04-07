import { cs } from "./cs";

function getStringAtPath(
  root: Record<string, unknown>,
  path: string,
): string {
  const parts = path.split(".");
  let current: unknown = root;
  for (const p of parts) {
    if (current !== null && typeof current === "object" && p in current) {
      current = (current as Record<string, unknown>)[p];
    } else {
      throw new Error(`Missing i18n key: ${path}`);
    }
  }
  if (typeof current !== "string") {
    throw new Error(`i18n key is not a string: ${path}`);
  }
  return current;
}

/** Vrátí řetězec pro klíč ve tvaru `segment.key` (např. `wine.liked`). */
export function t(key: string): string {
  return getStringAtPath(cs as unknown as Record<string, unknown>, key);
}
