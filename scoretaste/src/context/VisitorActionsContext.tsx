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
  pruneVisitorActionsBlob,
  toggleWineLiked,
  toggleWineWantToBuy,
} from "../lib/visitorStorage";
import type {
  EventCatalog,
  VisitorActionsBlob,
  VisitorWineActionRecord,
} from "../types";

type VisitorActionsValue = {
  blob: VisitorActionsBlob;
  toggleLiked: (wineId: string) => void;
  toggleWantToBuy: (wineId: string) => void;
  getRecord: (wineId: string) => VisitorWineActionRecord;
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

  const toggleLiked = useCallback(
    (wineId: string) => {
      setBlob(toggleWineLiked(eventId, wineId));
    },
    [eventId],
  );

  const toggleWantToBuy = useCallback(
    (wineId: string) => {
      setBlob(toggleWineWantToBuy(eventId, wineId));
    },
    [eventId],
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

  const value = useMemo(
    () => ({
      blob,
      toggleLiked,
      toggleWantToBuy,
      getRecord,
    }),
    [blob, toggleLiked, toggleWantToBuy, getRecord],
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
