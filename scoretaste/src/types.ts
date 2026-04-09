export type Event = {
  id: string;
  /** Stejné jako `id` (z API). */
  eventId?: string;
  name: string;
  date?: string;
  /** Aktivní epocha ostrého sběru; null = žádný ostrý běh. */
  activeEpochId?: number | null;
  /** ISO začátek aktivní epochy. */
  liveStartedAt?: string | null;
};

export type Winery = {
  id: string;
  eventId: string;
  name: string;
  /** Číslo sklepu — unikátní v rámci akce */
  locationNumber: string;
  /** Poznámka pro návštěvníky (admin) */
  note?: string;
  web?: string;
};

export type WineColor = "white" | "red" | "rose" | "orange";

export type Wine = {
  id: string;
  wineryId: string;
  /** Hlavní zobrazovaný název vína */
  label: string;
  variety: string;
  /** Přívlastek (např. pozdní sběr); může být prázdný řetězec */
  predicate: string;
  vintage: string;
  description?: string;
  color?: WineColor;
};

/** Pozice hotspotu na statické mapě akce (souřadnice v % rozměru obrázku). */
export type MapHotspot = {
  wineryId: string;
  cellarNumber: string;
  xPercent: number;
  yPercent: number;
};

/** Root shape of `/guide/data/events/:eventId.json` */
export type EventCatalog = {
  event: Event;
  wineries: Winery[];
  wines: Wine[];
  /** Volitelné; chybí u starších odpovědí serveru. */
  mapHotspots?: MapHotspot[];
};

/** One wine’s persisted flags (wine id is the key in `VisitorActionsBlob.actions`). */
export type VisitorWineActionRecord = {
  liked: boolean;
  /** V UI reprezentuje stav „TOP“ (3. stupeň hvězdy); synchronizace s backendem beze změny API. */
  wantToBuy: boolean;
  updatedAt: string;
};

/** Convenience type when passing a wine id with its flags in UI. */
export type VisitorWineAction = {
  wineId: string;
  liked: boolean;
  wantToBuy: boolean;
  updatedAt: string;
};

/**
 * Ukládá se pod `scoretaste:visitor:{eventId}:epoch:{epochSegment}`.
 * Legacy klíč bez epochy se při `activeEpochId === null` jednorázově migruje.
 */
export type VisitorActionsBlob = {
  schemaVersion: 1;
  eventId: string;
  /** Odpovídá serverovému `activeEpochId` pro tento blob; null = režim bez aktivní epochy. */
  epochScope?: number | null;
  actions: Record<string, VisitorWineActionRecord>;
  /** wineryId → navštívený sklep (jen v tomto zařízení) */
  visitedWineries?: Record<string, boolean>;
};
