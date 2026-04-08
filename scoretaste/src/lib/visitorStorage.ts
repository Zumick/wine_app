import type {
  EventCatalog,
  VisitorActionsBlob,
  VisitorWineActionRecord,
} from "../types";

export function visitorStorageKey(eventId: string): string {
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
  try {
    await fetch(`/guide/data/events/${encodeURIComponent(eventId)}/visitor-sync`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionKey, wines }),
    });
  } catch {
    /* offline nebo jiný host */
  }
}

function nowIso(): string {
  return new Date().toISOString();
}

function emptyBlob(eventId: string): VisitorActionsBlob {
  return { schemaVersion: 1, eventId, actions: {}, visitedWineries: {} };
}

export function loadVisitorActions(eventId: string): VisitorActionsBlob {
  if (typeof localStorage === "undefined") {
    return emptyBlob(eventId);
  }
  const raw = localStorage.getItem(visitorStorageKey(eventId));
  if (!raw) return emptyBlob(eventId);
  try {
    const parsed = JSON.parse(raw) as VisitorActionsBlob;
    if (parsed.schemaVersion !== 1 || parsed.eventId !== eventId) {
      return emptyBlob(eventId);
    }
    const visitedRaw = (parsed as VisitorActionsBlob).visitedWineries;
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
      actions:
        parsed.actions && typeof parsed.actions === "object"
          ? parsed.actions
          : {},
      visitedWineries,
    };
  } catch {
    return emptyBlob(eventId);
  }
}

export function saveVisitorActions(blob: VisitorActionsBlob): void {
  if (typeof localStorage === "undefined") return;
  localStorage.setItem(
    visitorStorageKey(blob.eventId),
    JSON.stringify(blob),
  );
  schedulePushVisitorSync(blob.eventId, blob);
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

/** Toggle liked for one wine; returns the new blob. */
export function toggleWineLiked(
  eventId: string,
  wineId: string,
): VisitorActionsBlob {
  const blob = loadVisitorActions(eventId);
  const cur = getOrCreate(blob.actions, wineId);
  blob.actions[wineId] = {
    ...cur,
    liked: !cur.liked,
    updatedAt: nowIso(),
  };
  saveVisitorActions(blob);
  return blob;
}

/** Toggle want-to-buy for one wine; returns the new blob. */
export function toggleWineWantToBuy(
  eventId: string,
  wineId: string,
): VisitorActionsBlob {
  const blob = loadVisitorActions(eventId);
  const cur = getOrCreate(blob.actions, wineId);
  blob.actions[wineId] = {
    ...cur,
    wantToBuy: !cur.wantToBuy,
    updatedAt: nowIso(),
  };
  saveVisitorActions(blob);
  return blob;
}

/** Toggle navštívený sklep; vrací nový blob. */
export function toggleWineryVisited(
  eventId: string,
  wineryId: string,
): VisitorActionsBlob {
  const blob = loadVisitorActions(eventId);
  const prev = blob.visitedWineries ?? {};
  const next = { ...prev };
  if (next[wineryId]) {
    delete next[wineryId];
  } else {
    next[wineryId] = true;
  }
  const out: VisitorActionsBlob = {
    ...blob,
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
    actions: actionsNeedPrune ? nextActions : blob.actions,
    visitedWineries: visitedNeedPrune ? nextVisited : blob.visitedWineries,
  };
  saveVisitorActions(out);
  return out;
}
