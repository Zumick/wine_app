import type { EventCatalogState } from "../hooks/useEventCatalog";

export type WineryBrowseView = "list" | "map";

export type VisitorSessionOutletContext = {
  catalogState: EventCatalogState;
  wineryFilter: string;
  setWineryFilter: (v: string) => void;
  wineryBrowseView: WineryBrowseView;
  setWineryBrowseView: (v: WineryBrowseView) => void;
};
