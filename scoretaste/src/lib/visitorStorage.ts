import type {
  EventCatalog,
  VisitorActionsBlob,
  VisitorWineActionRecord,
} from "../types";

/** 0 = bez, 1 = oblíbené, 2 = TOP (ukládá se jako liked+wantToBuy). */
export type WineStarLevel = 0 | 1 | 2;

/** null = žádná aktivní epocha (legacy / příprava). */
export type VisitorEpochScope = number | null;

export function wineStarLevel(rec: VisitorWineActionRecord): WineStarLevel {
  if (rec.liked && rec.wantToBuy) return 2;
  if (rec.liked) return 1;
  return 0;
}

function starLevelToFlags(level: WineStarLevel): {
  liked: boolean;
  wantToBuy: boolean;
} {
  switch (level) {
    case 2:
      return { liked: true, wantToBuy: true };
    case 1:
      return { liked: true, wantToBuy: false };
    default:
      return { liked: false, wantToBuy: false };
  }
}

/** Segment do localStorage klíče (`none` | číslo epochy). */
export function visitorEpochSegment(epochScope: VisitorEpochScope): string {
  if (epochScope === null || epochScope === undefined) {
    return "none";
  }
  return String(epochScope);
}

/**
 * Klíč: scoretaste:visitor:{eventId}:epoch:{segment}
 * (dříve jen scoretaste:visitor:{eventId} — migrováno jen pro epoch:none)
 */
export function visitorStorageKey(
  eventId: string,
  epochScope: VisitorEpochScope,
): string {
  return `scoretaste:visitor:${eventId}:epoch:${visitorEpochSegment(epochScope)}`;
}

function legacyVisitorStorageKey(eventId: string): string {
  return `scoretaste:visitor:${eventId}`;
}

const SESSION_STORAGE_KEY = "scoretaste:visitorSession";

export function getOrCreateVisitorSessionKey(): string {
  if (typeof localStorage === "undefined") {
    return `tmp-${Math.random().toString(36).slice(2)}`;
  }
  let k = localStorage.getItem(SESSION_STORAGE_KEY);
  if (!k || k.length < 8) {
    k =
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `s-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    localStorage.setItem(SESSION_STORAGE_KEY, k);
  }
  return k;
}

let syncTimer: ReturnType<typeof setTimeout> | null = null;

let visitorEpochMismatchHandler: ((eventId: string) => void) | null = null;

/** Volá se z layoutu: při 409 epoch_mismatch znovu načte katalog. */
export function setVisitorEpochMismatchHandler(
  fn: ((eventId: string) => void) | null,
): void {
  visitorEpochMismatchHandler = fn;
}

function clearAllVisitorStorageForEvent(eventId: string): void {
  if (typeof localStorage === "undefined") return;
  const prefix = `scoretaste:visitor:${eventId}`;
  const toRemove: string[] = [];
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    if (!k) continue;
    if (k === prefix || k.startsWith(`${prefix}:`)) {
      toRemove.push(k);
    }
  }
  for (const k of toRemove) {
    localStorage.removeItem(k);
  }
}

function schedulePushVisitorSync(eventId: string, blob: VisitorActionsBlob): void {
  if (typeof window === "undefined") return;
  if (syncTimer) clearTimeout(syncTimer);
  syncTimer = setTimeout(() => {
    syncTimer = null;
    void pushVisitorSync(eventId, blob);
  }, 900);
}

async function pushVisitorSync(
  eventId: string,
  blob: VisitorActionsBlob,
): Promise<void> {
  const sessionKey = getOrCreateVisitorSessionKey();
  const wines: Record<string, { liked: boolean; wantToBuy: boolean }> = {};
  for (const [wineId, rec] of Object.entries(blob.actions)) {
    if (rec.liked || rec.wantToBuy) {
      wines[wineId] = { liked: rec.liked, wantToBuy: rec.wantToBuy };
    }
  }
  const epochScope = blob.epochScope ?? null;
  const payload: Record<string, unknown> = { sessionKey, wines };
  if (epochScope !== null) {
    payload.epochId = epochScope;
  }
  try {
    const res = await fetch(
      `/guide/data/events/${encodeURIComponent(eventId)}/visitor-sync`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    );
    if (!res.ok) {
      let code: string | undefined;
      try {
        const j = (await res.json()) as { code?: string };
        code = j.code;
      } catch {
        /* ignore */
      }
      if (
        (res.status === 409 && code === "epoch_mismatch") ||
        (res.status === 400 &&
          (code === "epoch_id_required" || code === "epoch_mismatch"))
      ) {
        clearAllVisitorStorageForEvent(eventId);
        visitorEpochMismatchHandler?.(eventId);
      }
      return;
    }
  } catch {
    /* offline nebo jiný host */
  }
}

function nowIso(): string {
  return new Date().toISOString();
}

function emptyBlob(
  eventId: string,
  epochScope: VisitorEpochScope = null,
): VisitorActionsBlob {
  return {
    schemaVersion: 1,
    eventId,
    epochScope,
    actions: {},
    visitedWineries: {},
  };
}

function normalizeLoadedBlob(
  eventId: string,
  epochScope: VisitorEpochScope,
  parsed: VisitorActionsBlob,
): VisitorActionsBlob {
  const storedEs: VisitorEpochScope =
    parsed.epochScope === undefined ? null : parsed.epochScope;
  if (storedEs !== epochScope) {
    return emptyBlob(eventId, epochScope);
  }
  const visitedRaw = parsed.visitedWineries;
  const visitedWineries: Record<string, boolean> =
    visitedRaw && typeof visitedRaw === "object"
      ? Object.fromEntries(
          Object.entries(visitedRaw).filter(
            ([, v]) => typeof v === "boolean",
          ),
        )
      : {};
  return {
    schemaVersion: 1,
    eventId,
    epochScope,
    actions:
      parsed.actions && typeof parsed.actions === "object"
        ? parsed.actions
        : {},
    visitedWineries,
  };
}

export function loadVisitorActions(
  eventId: string,
  epochScope: VisitorEpochScope,
): VisitorActionsBlob {
  if (typeof localStorage === "undefined") {
    return emptyBlob(eventId, epochScope);
  }
  const key = visitorStorageKey(eventId, epochScope);
  let raw = localStorage.getItem(key);
  if (
    !raw &&
    epochScope === null &&
    typeof localStorage.getItem(legacyVisitorStorageKey(eventId)) === "string"
  ) {
    const leg = localStorage.getItem(legacyVisitorStorageKey(eventId));
    if (leg) {
      raw = leg;
      try {
        localStorage.setItem(key, leg);
        localStorage.removeItem(legacyVisitorStorageKey(eventId));
      } catch {
        /* quota */
      }
    }
  }
  if (!raw) return emptyBlob(eventId, epochScope);
  try {
    const parsed = JSON.parse(raw) as VisitorActionsBlob;
    if (parsed.schemaVersion !== 1 || parsed.eventId !== eventId) {
      return emptyBlob(eventId, epochScope);
    }
    return normalizeLoadedBlob(eventId, epochScope, parsed);
  } catch {
    return emptyBlob(eventId, epochScope);
  }
}

export function saveVisitorActions(blob: VisitorActionsBlob): void {
  if (typeof localStorage === "undefined") return;
  const epochScope = blob.epochScope ?? null;
  const key = visitorStorageKey(blob.eventId, epochScope);
  const toStore: VisitorActionsBlob = { ...blob, epochScope };
  localStorage.setItem(key, JSON.stringify(toStore));
  schedulePushVisitorSync(blob.eventId, toStore);
}

function getOrCreate(
  actions: Record<string, VisitorWineActionRecord>,
  wineId: string,
): VisitorWineActionRecord {
  return (
    actions[wineId] ?? {
      liked: false,
      wantToBuy: false,
      updatedAt: nowIso(),
    }
  );
}

export function setWineStarLevel(
  eventId: string,
  wineId: string,
  level: WineStarLevel,
  epochScope: VisitorEpochScope,
): VisitorActionsBlob {
  const blob = loadVisitorActions(eventId, epochScope);
  const cur = getOrCreate(blob.actions, wineId);
  const { liked, wantToBuy } = starLevelToFlags(level);
  blob.actions[wineId] = {
    ...cur,
    liked,
    wantToBuy,
    updatedAt: nowIso(),
  };
  blob.epochScope = epochScope;
  saveVisitorActions(blob);
  return blob;
}

export function cycleWineStarLevel(
  eventId: string,
  wineId: string,
  epochScope: VisitorEpochScope,
): VisitorActionsBlob {
  const blob = loadVisitorActions(eventId, epochScope);
  const cur = getOrCreate(blob.actions, wineId);
  const current = wineStarLevel(cur);
  const next: WineStarLevel =
    current === 0 ? 1 : current === 1 ? 2 : 1;
  return setWineStarLevel(eventId, wineId, next, epochScope);
}

export function markWineryVisited(
  eventId: string,
  wineryId: string,
  epochScope: VisitorEpochScope,
): VisitorActionsBlob {
  const blob = loadVisitorActions(eventId, epochScope);
  if (blob.visitedWineries?.[wineryId]) {
    return blob;
  }
  const out: VisitorActionsBlob = {
    ...blob,
    epochScope,
    visitedWineries: { ...(blob.visitedWineries ?? {}), [wineryId]: true },
  };
  saveVisitorActions(out);
  return out;
}

/** Toggle navštívený sklep; vrací nový blob. */
export function toggleWineryVisited(
  eventId: string,
  wineryId: string,
  epochScope: VisitorEpochScope,
): VisitorActionsBlob {
  const blob = loadVisitorActions(eventId, epochScope);
  const prev = blob.visitedWineries ?? {};
  const next = { ...prev };
  if (next[wineryId]) {
    delete next[wineryId];
  } else {
    next[wineryId] = true;
  }
  const out: VisitorActionsBlob = {
    ...blob,
    epochScope,
    visitedWineries: next,
  };
  saveVisitorActions(out);
  return out;
}

/** Vína, která patří do katalogu (existuje vinařství pro wineryId). */
export function wineIdsWithValidWinery(catalog: EventCatalog): Set<string> {
  const wineryIds = new Set(catalog.wineries.map((w) => w.id));
  return new Set(
    catalog.wines
      .filter((w) => wineryIds.has(w.wineryId))
      .map((w) => w.id),
  );
}

/**
 * Odstraní z blobu akce pro wineId, která už v katalogu nejsí (smazaná vína / vinařství).
 * Volá se po načtení katalogu, aby localStorage neobsahoval mrtvé záznamy.
 */
export function pruneVisitorActionsBlob(
  catalog: EventCatalog,
  blob: VisitorActionsBlob,
  epochScope: VisitorEpochScope,
): VisitorActionsBlob {
  const validWineIds = wineIdsWithValidWinery(catalog);
  const validWineryIds = new Set(catalog.wineries.map((w) => w.id));

  let actionsNeedPrune = false;
  for (const id of Object.keys(blob.actions)) {
    if (!validWineIds.has(id)) {
      actionsNeedPrune = true;
      break;
    }
  }

  const vw = blob.visitedWineries;
  const nextVisited: Record<string, boolean> = {};
  if (vw && typeof vw === "object") {
    for (const [wid, v] of Object.entries(vw)) {
      if (validWineryIds.has(wid) && v) {
        nextVisited[wid] = true;
      }
    }
  }
  const visitedNeedPrune =
    vw &&
    typeof vw === "object" &&
    Object.keys(vw).some((wid) => !validWineryIds.has(wid));

  if (!actionsNeedPrune && !visitedNeedPrune) {
    return blob;
  }

  const nextActions: Record<string, VisitorWineActionRecord> = {};
  if (actionsNeedPrune) {
    for (const [id, rec] of Object.entries(blob.actions)) {
      if (validWineIds.has(id)) {
        nextActions[id] = rec;
      }
    }
  }

  const out: VisitorActionsBlob = {
    schemaVersion: 1,
    eventId: blob.eventId,
    epochScope,
    actions: actionsNeedPrune ? nextActions : blob.actions,
    visitedWineries: visitedNeedPrune ? nextVisited : blob.visitedWineries,
  };
  saveVisitorActions(out);
  return out;
}
