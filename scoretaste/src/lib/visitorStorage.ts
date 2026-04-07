import type {
  EventCatalog,
  VisitorActionsBlob,
  VisitorWineActionRecord,
} from "../types";

export function visitorStorageKey(eventId: string): string {
  return `scoretaste:visitor:${eventId}`;
}

function nowIso(): string {
  return new Date().toISOString();
}

function emptyBlob(eventId: string): VisitorActionsBlob {
  return { schemaVersion: 1, eventId, actions: {} };
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
    return {
      schemaVersion: 1,
      eventId,
      actions: parsed.actions && typeof parsed.actions === "object"
        ? parsed.actions
        : {},
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
  let removed = false;
  for (const id of Object.keys(blob.actions)) {
    if (!validWineIds.has(id)) {
      removed = true;
      break;
    }
  }
  if (!removed) {
    return blob;
  }
  const nextActions: Record<string, VisitorWineActionRecord> = {};
  for (const [id, rec] of Object.entries(blob.actions)) {
    if (validWineIds.has(id)) {
      nextActions[id] = rec;
    }
  }
  const out: VisitorActionsBlob = {
    schemaVersion: 1,
    eventId: blob.eventId,
    actions: nextActions,
  };
  saveVisitorActions(out);
  return out;
}
