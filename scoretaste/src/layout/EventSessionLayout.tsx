import { useMemo, useState } from "react";
import { Navigate, Outlet, useParams } from "react-router-dom";
import { VisitorActionsProvider } from "../context/VisitorActionsContext";
import { useEventCatalog } from "../hooks/useEventCatalog";
import type {
  VisitorSessionOutletContext,
  WineryBrowseView,
} from "./visitorSessionContext";
import { VisitorSessionChrome } from "./VisitorSessionChrome";

export function EventSessionLayout() {
  const { eventId } = useParams<{ eventId: string }>();
  const catalogState = useEventCatalog(eventId);
  const [wineryFilter, setWineryFilter] = useState("");
  const [wineryBrowseView, setWineryBrowseView] =
    useState<WineryBrowseView>("list");

  const outletContext = useMemo<VisitorSessionOutletContext>(
    () => ({
      catalogState,
      wineryFilter,
      setWineryFilter,
      wineryBrowseView,
      setWineryBrowseView,
    }),
    [catalogState, wineryFilter, wineryBrowseView],
  );

  if (!eventId) {
    return <Navigate to="/" replace />;
  }
  const catalog =
    catalogState.status === "ok" ? catalogState.catalog : undefined;
  return (
    <VisitorActionsProvider eventId={eventId} catalog={catalog}>
      {catalogState.status === "ok" ? (
        <VisitorSessionChrome
          eventId={eventId}
          catalog={catalogState.catalog}
          outletContext={outletContext}
        />
      ) : (
        <Outlet context={outletContext} />
      )}
    </VisitorActionsProvider>
  );
}
