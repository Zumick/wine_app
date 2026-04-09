import { useEffect, useMemo, useRef, useState } from "react";
import { Navigate, Outlet, useParams } from "react-router-dom";
import { VisitorActionsProvider } from "../context/VisitorActionsContext";
import { useEventCatalog } from "../hooks/useEventCatalog";
import { setVisitorEpochMismatchHandler } from "../lib/visitorStorage";
import { t } from "../i18n";
import type {
  VisitorSessionOutletContext,
  WineryBrowseView,
} from "./visitorSessionContext";
import { VisitorSessionChrome } from "./VisitorSessionChrome";

export function EventSessionLayout() {
  const { eventId } = useParams<{ eventId: string }>();
  const { state: catalogState, refetch: refetchCatalog } = useEventCatalog(
    eventId,
  );
  const [epochSyncFlash, setEpochSyncFlash] = useState(false);
  const flashTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
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

  useEffect(() => {
    if (!eventId) return;
    const onMismatch = () => {
      refetchCatalog();
      setEpochSyncFlash(true);
      if (flashTimerRef.current) clearTimeout(flashTimerRef.current);
      flashTimerRef.current = setTimeout(() => {
        setEpochSyncFlash(false);
        flashTimerRef.current = null;
      }, 2600);
    };
    setVisitorEpochMismatchHandler(() => {
      onMismatch();
    });
    return () => {
      setVisitorEpochMismatchHandler(null);
      if (flashTimerRef.current) clearTimeout(flashTimerRef.current);
    };
  }, [eventId, refetchCatalog]);

  if (!eventId) {
    return <Navigate to="/" replace />;
  }
  const catalog =
    catalogState.status === "ok" ? catalogState.catalog : undefined;
  return (
    <VisitorActionsProvider eventId={eventId} catalog={catalog}>
      {epochSyncFlash ? (
        <p
          className="visitor-epoch-sync-hint"
          role="status"
          aria-live="polite"
          style={{
            position: "fixed",
            bottom: 12,
            left: "50%",
            transform: "translateX(-50%)",
            margin: 0,
            padding: "6px 12px",
            fontSize: 12,
            color: "#374151",
            background: "#f3f4f6",
            border: "1px solid #e5e7eb",
            borderRadius: 8,
            zIndex: 50,
            boxShadow: "0 1px 4px rgba(0,0,0,0.08)",
          }}
        >
          {t("visitor.epochSyncNote")}
        </p>
      ) : null}
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
