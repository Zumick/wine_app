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
  loadVisitorActions,
  markWineryVisited,
  pruneVisitorActionsBlob,
  setWineLiked,
  setWineWantToBuy,
  toggleWineryVisited,
} from "../lib/visitorStorage";
import type {
  EventCatalog,
  VisitorActionsBlob,
  VisitorWineActionRecord,
} from "../types";

type VisitorActionsValue = {
  blob: VisitorActionsBlob;
  setLiked: (wineId: string, liked: boolean) => void;
  setWantToBuy: (wineId: string, wantToBuy: boolean) => void;
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

  const setLiked = useCallback(
    (wineId: string, liked: boolean) => {
      let next = setWineLiked(eventId, wineId, liked);
      if (liked) {
        const wineryId = wineryIdByWineId[wineId];
        if (wineryId) {
          next = markWineryVisited(eventId, wineryId);
        }
      }
      setBlob(next);
    },
    [eventId, wineryIdByWineId],
  );

  const setWantToBuy = useCallback(
    (wineId: string, wantToBuy: boolean) => {
      let next = setWineWantToBuy(eventId, wineId, wantToBuy);
      if (wantToBuy) {
        next = setWineLiked(eventId, wineId, true);
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
      setLiked,
      setWantToBuy,
      getRecord,
      isWineryVisited,
      toggleWineryVisited: toggleWineryVisitedCb,
    }),
    [
      blob,
      setLiked,
      setWantToBuy,
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
