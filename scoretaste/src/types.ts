export type Event = {
  id: string;
  name: string;
  date?: string;
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

/** Root shape of `/guide/data/events/:eventId.json` */
export type EventCatalog = {
  event: Event;
  wineries: Winery[];
  wines: Wine[];
};

/** One wine’s persisted flags (wine id is the key in `VisitorActionsBlob.actions`). */
export type VisitorWineActionRecord = {
  liked: boolean;
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

/** Stored under `scoretaste:visitor:{eventId}` */
export type VisitorActionsBlob = {
  schemaVersion: 1;
  eventId: string;
  actions: Record<string, VisitorWineActionRecord>;
  /** wineryId → navštívený sklep (jen v tomto zařízení) */
  visitedWineries?: Record<string, boolean>;
};
