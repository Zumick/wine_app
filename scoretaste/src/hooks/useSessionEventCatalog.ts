import { useOutletContext } from "react-router-dom";
import type { VisitorSessionOutletContext } from "../layout/visitorSessionContext";
import type { EventCatalogState } from "./useEventCatalog";

/**
 * Stav katalogu z `EventSessionLayout` (jedno načtení na event).
 * Používej jen ve stránkách pod `/e/:eventId/*`.
 */
export function useSessionEventCatalog(): EventCatalogState {
  const ctx = useOutletContext<VisitorSessionOutletContext | undefined>();
  if (ctx === undefined) {
    throw new Error(
      "useSessionEventCatalog must be used under EventSessionLayout",
    );
  }
  return ctx.catalogState;
}

export function useWineryListFilter(): [string, (v: string) => void] {
  const ctx = useOutletContext<VisitorSessionOutletContext | undefined>();
  if (ctx === undefined) {
    throw new Error(
      "useWineryListFilter must be used under EventSessionLayout",
    );
  }
  return [ctx.wineryFilter, ctx.setWineryFilter];
}
