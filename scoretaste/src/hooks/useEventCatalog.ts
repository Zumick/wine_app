import { useCallback, useEffect, useState } from "react";
import { fetchEventCatalog } from "../lib/eventLoader";
import type { EventCatalog } from "../types";

export type EventCatalogState =
  | { status: "loading" }
  | { status: "ok"; catalog: EventCatalog }
  | { status: "error"; code: string };

/**
 * Načte katalog pro event. Používej v `EventSessionLayout` (jeden zdroj pravdy);
 * child stránky berou stav přes `useSessionEventCatalog`.
 */
export function useEventCatalog(eventId: string | undefined): {
  state: EventCatalogState;
  refetch: () => void;
} {
  const [reloadNonce, setReloadNonce] = useState(0);
  const [state, setState] = useState<EventCatalogState>({ status: "loading" });

  const refetch = useCallback(() => {
    setReloadNonce((n) => n + 1);
  }, []);

  useEffect(() => {
    if (!eventId) {
      setState({ status: "error", code: "MISSING_EVENT_ID" });
      return;
    }
    let cancelled = false;
    setState({ status: "loading" });
    fetchEventCatalog(eventId)
      .then((catalog) => {
        if (!cancelled) setState({ status: "ok", catalog });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : "LOAD_FAILED";
        setState({ status: "error", code: msg });
      });
    return () => {
      cancelled = true;
    };
  }, [eventId, reloadNonce]);

  return { state, refetch };
}
