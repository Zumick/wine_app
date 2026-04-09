import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  cycleWineStarLevel,
  loadVisitorActions,
  markWineryVisited,
  pruneVisitorActionsBlob,
  setWineStarLevel,
  toggleWineryVisited,
  wineStarLevel,
  type VisitorEpochScope,
  type WineStarLevel,
} from "../lib/visitorStorage";
import type {
  EventCatalog,
  VisitorActionsBlob,
  VisitorWineActionRecord,
} from "../types";

type VisitorActionsValue = {
  blob: VisitorActionsBlob;
  cycleStarRating: (wineId: string) => void;
  /** Nastaví přesnou úroveň (0–2); při ≥ 1 označí sklep jako navštívený. */
  setStarLevel: (wineId: string, level: WineStarLevel) => void;
  getStarLevel: (wineId: string) => WineStarLevel;
  getRecord: (wineId: string) => VisitorWineActionRecord;
  isWineryVisited: (wineryId: string) => boolean;
  toggleWineryVisited: (wineryId: string) => void;
};

const VisitorActionsContext = createContext<VisitorActionsValue | null>(null);

function catalogEpochScope(catalog: EventCatalog | undefined): VisitorEpochScope {
  if (!catalog) return null;
  const v = catalog.event.activeEpochId;
  if (v === undefined || v === null) return null;
  return v;
}

export function VisitorActionsProvider({
  eventId,
  catalog,
  children,
}: {
  eventId: string;
  catalog?: EventCatalog;
  children: ReactNode;
}) {
  const epochScope = catalogEpochScope(catalog);
  const epochDep = catalog
    ? `${eventId}:${epochScope === null ? "none" : String(epochScope)}`
    : "loading";

  const [blob, setBlob] = useState<VisitorActionsBlob>(() =>
    emptyBlobForScope(eventId, catalog),
  );

  function emptyBlobForScope(
    eid: string,
    cat: EventCatalog | undefined,
  ): VisitorActionsBlob {
    const es = catalogEpochScope(cat);
    return {
      schemaVersion: 1,
      eventId: eid,
      epochScope: es,
      actions: {},
      visitedWineries: {},
    };
  }

  useEffect(() => {
    if (!catalog) {
      setBlob(emptyBlobForScope(eventId, undefined));
      return;
    }
    const es = catalogEpochScope(catalog);
    let b = loadVisitorActions(eventId, es);
    b = pruneVisitorActionsBlob(catalog, b, es);
    setBlob(b);
  }, [eventId, epochDep, catalog]);

  const wineryIdByWineId = useMemo(() => {
    const map: Record<string, string> = {};
    for (const w of catalog?.wines ?? []) {
      map[w.id] = w.wineryId;
    }
    return map;
  }, [catalog]);

  const cycleStarRating = useCallback(
    (wineId: string) => {
      if (!catalog) return;
      const es = catalogEpochScope(catalog);
      let next = cycleWineStarLevel(eventId, wineId, es);
      const rec = next.actions[wineId];
      if (rec && wineStarLevel(rec) >= 1) {
        const wineryId = wineryIdByWineId[wineId];
        if (wineryId) {
          next = markWineryVisited(eventId, wineryId, es);
        }
      }
      setBlob(next);
    },
    [eventId, wineryIdByWineId, catalog],
  );

  const setStarLevel = useCallback(
    (wineId: string, level: WineStarLevel) => {
      if (!catalog) return;
      const es = catalogEpochScope(catalog);
      let next = setWineStarLevel(eventId, wineId, level, es);
      if (level >= 1) {
        const wineryId = wineryIdByWineId[wineId];
        if (wineryId) {
          next = markWineryVisited(eventId, wineryId, es);
        }
      }
      setBlob(next);
    },
    [eventId, wineryIdByWineId, catalog],
  );

  const getRecord = useCallback(
    (wineId: string): VisitorWineActionRecord => {
      return (
        blob.actions[wineId] ?? {
          liked: false,
          wantToBuy: false,
          updatedAt: "",
        }
      );
    },
    [blob.actions],
  );

  const getStarLevel = useCallback(
    (wineId: string): WineStarLevel => wineStarLevel(getRecord(wineId)),
    [getRecord],
  );

  const isWineryVisited = useCallback(
    (wineryId: string): boolean =>
      Boolean(blob.visitedWineries?.[wineryId]),
    [blob.visitedWineries],
  );

  const toggleWineryVisitedCb = useCallback(
    (wineryId: string) => {
      if (!catalog) return;
      const es = catalogEpochScope(catalog);
      setBlob(toggleWineryVisited(eventId, wineryId, es));
    },
    [eventId, catalog],
  );

  const value = useMemo(
    () => ({
      blob,
      cycleStarRating,
      setStarLevel,
      getStarLevel,
      getRecord,
      isWineryVisited,
      toggleWineryVisited: toggleWineryVisitedCb,
    }),
    [
      blob,
      cycleStarRating,
      setStarLevel,
      getStarLevel,
      getRecord,
      isWineryVisited,
      toggleWineryVisitedCb,
    ],
  );

  return (
    <VisitorActionsContext.Provider value={value}>
      {children}
    </VisitorActionsContext.Provider>
  );
}

export function useVisitorActions(): VisitorActionsValue {
  const ctx = useContext(VisitorActionsContext);
  if (!ctx) {
    throw new Error("useVisitorActions must be used under VisitorActionsProvider");
  }
  return ctx;
}
