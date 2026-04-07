import { Navigate, useParams } from "react-router-dom";
import { ErrorBlock, LoadingBlock } from "../components/LoadState";
import { useSessionEventCatalog } from "../hooks/useSessionEventCatalog";
import { catalogErrorTitle } from "../lib/errorCopy";
import { t } from "../i18n";

export function EventEntryPage() {
  const { eventId } = useParams<{ eventId: string }>();
  const state = useSessionEventCatalog();

  if (!eventId) {
    return <ErrorBlock title={catalogErrorTitle("MISSING_EVENT_ID")} />;
  }
  if (state.status === "loading") {
    return <LoadingBlock />;
  }
  if (state.status === "error") {
    return (
      <ErrorBlock
        title={catalogErrorTitle(state.code)}
        hint={t("common.hintRetry")}
      />
    );
  }
  return <Navigate to="wineries" replace />;
}
