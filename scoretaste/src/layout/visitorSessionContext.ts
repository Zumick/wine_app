import type { EventCatalogState } from "../hooks/useEventCatalog";

export type VisitorSessionOutletContext = {
  catalogState: EventCatalogState;
  wineryFilter: string;
  setWineryFilter: (v: string) => void;
};
