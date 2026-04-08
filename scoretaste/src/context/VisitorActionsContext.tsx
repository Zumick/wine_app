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

export function VisitorActionsProvider({
  eventId,
  catalog,
  children,
}: {
  eventId: string;
  catalog?: EventCatalog;
  children: ReactNode;
}) {
  const [blob, setBlob] = useState<VisitorActionsBlob>(() =>
    loadVisitorActions(eventId),
  );

  useEffect(() => {
    setBlob(loadVisitorActions(eventId));
  }, [eventId]);

  useEffect(() => {
    if (!catalog) return;
    setBlob((prev) => pruneVisitorActionsBlob(catalog, prev));
  }, [eventId, catalog]);

  const wineryIdByWineId = useMemo(() => {
    const map: Record<string, string> = {};
    for (const w of catalog?.wines ?? []) {
      map[w.id] = w.wineryId;
    }
    return map;
  }, [catalog]);

  const cycleStarRating = useCallback(
    (wineId: string) => {
      let next = cycleWineStarLevel(eventId, wineId);
      const rec = next.actions[wineId];
      if (rec && wineStarLevel(rec) >= 1) {
        const wineryId = wineryIdByWineId[wineId];
        if (wineryId) {
          next = markWineryVisited(eventId, wineryId);
        }
      }
      setBlob(next);
    },
    [eventId, wineryIdByWineId],
  );

  const setStarLevel = useCallback(
    (wineId: string, level: WineStarLevel) => {
      let next = setWineStarLevel(eventId, wineId, level);
      if (level >= 1) {
        const wineryId = wineryIdByWineId[wineId];
        if (wineryId) {
          next = markWineryVisited(eventId, wineryId);
        }
      }
      setBlob(next);
    },
    [eventId, wineryIdByWineId],
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
      setBlob(toggleWineryVisited(eventId, wineryId));
    },
    [eventId],
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
