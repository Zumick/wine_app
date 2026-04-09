import type { EventCatalog, MapHotspot, Wine, Winery } from "../types";
import { normalizeWineColor } from "./wineSort";

function parseWinery(raw: unknown): Winery {
  if (!raw || typeof raw !== "object") {
    throw new Error("INVALID_EVENT");
  }
  const o = raw as Record<string, unknown>;
  const id = typeof o.id === "string" ? o.id : String(o.id ?? "");
  const eventId =
    typeof o.eventId === "string" ? o.eventId : String(o.eventId ?? "");
  const name = String(o.name ?? "").trim();
  const locationNumber = String(o.locationNumber ?? "").trim();
  if (!id || !eventId || !name) {
    throw new Error("INVALID_EVENT");
  }
  return { id, eventId, name, locationNumber };
}

function parseMapHotspot(raw: unknown): MapHotspot | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const o = raw as Record<string, unknown>;
  const wineryId = String(o.wineryId ?? "").trim();
  const xPercent = Number(o.xPercent);
  const yPercent = Number(o.yPercent);
  if (!wineryId || !Number.isFinite(xPercent) || !Number.isFinite(yPercent)) {
    return null;
  }
  return {
    wineryId,
    cellarNumber: String(o.cellarNumber ?? "").trim(),
    xPercent,
    yPercent,
  };
}

function parseMapHotspotsList(raw: unknown): MapHotspot[] {
  if (!Array.isArray(raw)) {
    return [];
  }
  const byWid = new Map<string, MapHotspot>();
  for (const item of raw) {
    const h = parseMapHotspot(item);
    if (h) {
      byWid.set(h.wineryId, h);
    }
  }
  return Array.from(byWid.values());
}

function parseWine(raw: unknown): Wine {
  if (!raw || typeof raw !== "object") {
    throw new Error("INVALID_EVENT");
  }
  const o = raw as Record<string, unknown>;
  const id = typeof o.id === "string" ? o.id : String(o.id ?? "");
  const wineryId =
    typeof o.wineryId === "string" ? o.wineryId : String(o.wineryId ?? "");
  const label = String(o.label ?? "").trim();
  const variety = String(o.variety ?? "").trim();
  const vintage = String(o.vintage ?? "").trim();
  const predicate = String(o.predicate ?? "").trim();
  if (!id || !wineryId || !label || !variety || !vintage) {
    throw new Error("INVALID_EVENT");
  }
  const w: Wine = {
    id,
    wineryId,
    label,
    variety,
    predicate,
    vintage,
    color: normalizeWineColor(
      typeof o.color === "string" ? o.color : undefined,
    ),
  };
  const desc = o.description;
  if (typeof desc === "string" && desc.trim()) {
    w.description = desc.trim();
  }
  return w;
}

export async function fetchEventCatalog(eventId: string): Promise<EventCatalog> {
  const EVENT_BASE = "/guide/data/events";
  const url = `${EVENT_BASE}/${encodeURIComponent(eventId)}.json`;

  const res = await fetch(url, { cache: "no-store" });

  if (res.status === 404) {
    throw new Error("NOT_FOUND");
  }
  if (!res.ok) {
    throw new Error("LOAD_FAILED");
  }

  const data = (await res.json()) as Record<string, unknown>;

  const ev = data.event as EventCatalog["event"] | undefined;
  if (!ev || typeof ev.id !== "string") {
    throw new Error("INVALID_EVENT");
  }
  if (ev.id !== eventId) {
    throw new Error("INVALID_EVENT");
  }

  const rawWineries = Array.isArray(data.wineries) ? data.wineries : [];
  const rawWines = Array.isArray(data.wines) ? data.wines : [];
  let wineries: Winery[];
  let wines: Wine[];
  try {
    wineries = rawWineries.map(parseWinery);
  } catch {
    throw new Error("INVALID_EVENT");
  }
  const seenLoc = new Set<string>();
  for (const w of wineries) {
    const ln = w.locationNumber.trim();
    if (ln) {
      if (seenLoc.has(ln)) {
        throw new Error("INVALID_EVENT");
      }
      seenLoc.add(ln);
    }
  }
  try {
    wines = rawWines.map(parseWine);
  } catch {
    throw new Error("INVALID_EVENT");
  }

  const mapHotspots = parseMapHotspotsList(data.mapHotspots);

  return { event: ev, wineries, wines, mapHotspots };
}