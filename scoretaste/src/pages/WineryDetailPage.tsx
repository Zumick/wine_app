import { Link, useParams } from "react-router-dom";
import { WineActionToggles } from "../components/WineActionToggles";
import { ErrorBlock, LoadingBlock, PageMain } from "../components/LoadState";
import { useSessionEventCatalog } from "../hooks/useSessionEventCatalog";
import { catalogErrorTitle } from "../lib/errorCopy";
import { t } from "../i18n";
import { wineSecondaryLine } from "../lib/wineDisplay";
import type { EventCatalog, Wine } from "../types";

function winesForWinery(catalog: EventCatalog, wineryId: string): Wine[] {
  return catalog.wines
    .filter((w) => w.wineryId === wineryId)
    .sort((a, b) => a.label.localeCompare(b.label, "cs"));
}

export function WineryDetailPage() {
  const { eventId, wineryId } = useParams<{
    eventId: string;
    wineryId: string;
  }>();
  const state = useSessionEventCatalog();

  if (!eventId || !wineryId) {
    return (
      <ErrorBlock
        title={t("errors.missingUrlParams")}
        hint={t("common.hintCheckLink")}
      />
    );
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

  const { catalog } = state;
  const winery = catalog.wineries.find((w) => w.id === wineryId);
  if (!winery) {
    return (
      <PageMain>
        <p role="alert">
          <strong>{t("winery.notFoundStrong")}</strong>
        </p>
        <p>
          <Link to={`/e/${eventId}/wineries`}>
            {t("common.backToWineryList")}
          </Link>
        </p>
      </PageMain>
    );
  }

  const wines = winesForWinery(catalog, wineryId);

  return (
    <PageMain>
      <h1 className="visitor-page-heading" style={{ marginTop: 0 }}>
        {winery.name}
      </h1>
      <p style={{ margin: "0 0 1rem", color: "#555", fontSize: "0.92rem" }}>
        {t("winery.cellarWord")} {winery.locationNumber}
      </p>
      <h2 style={{ fontSize: "1rem", margin: "0 0 0.5rem" }}>
        {t("winery.winesHeading")}
      </h2>
      {wines.length === 0 ? (
        <p>{t("winery.noWinesHere")}</p>
      ) : (
        <ul style={{ listStyle: "none", paddingLeft: 0, margin: 0 }}>
          {wines.map((wine) => (
            <li key={wine.id} className="visitor-wine-card">
              <div className="visitor-wine-label">{wine.label}</div>
              <div className="visitor-wine-line2">{wineSecondaryLine(wine)}</div>
              {wine.description ? (
                <p style={{ margin: "0.35rem 0 0", color: "#333", fontSize: "0.9rem" }}>
                  {wine.description}
                </p>
              ) : null}
              <WineActionToggles wineId={wine.id} />
            </li>
          ))}
        </ul>
      )}
    </PageMain>
  );
}
