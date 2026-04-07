import { Link, useParams } from "react-router-dom";
import { ErrorBlock, LoadingBlock, PageMain } from "../components/LoadState";
import { useSessionEventCatalog, useWineryListFilter } from "../hooks/useSessionEventCatalog";
import { catalogErrorTitle } from "../lib/errorCopy";
import { t } from "../i18n";
import type { EventCatalog, Winery } from "../types";

function sortedWineries(catalog: EventCatalog): Winery[] {
  return [...catalog.wineries].sort((a, b) =>
    a.locationNumber.localeCompare(b.locationNumber, "cs", { numeric: true }),
  );
}

function wineCount(catalog: EventCatalog, wineryId: string): number {
  return catalog.wines.filter((w) => w.wineryId === wineryId).length;
}

function matchesFilter(w: Winery, q: string): boolean {
  const s = q.trim().toLowerCase();
  if (!s) return true;
  return (
    w.name.toLowerCase().includes(s) ||
    w.locationNumber.toLowerCase().includes(s)
  );
}

export function WineryListPage() {
  const { eventId } = useParams<{ eventId: string }>();
  const state = useSessionEventCatalog();
  const [wineryFilter] = useWineryListFilter();

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

  const { catalog } = state;
  const allRows = sortedWineries(catalog);
  const rows = allRows.filter((w) => matchesFilter(w, wineryFilter));
  const emptyBecauseFilter =
    allRows.length > 0 && rows.length === 0 && wineryFilter.trim();

  return (
    <PageMain>
      <h2 className="visitor-page-heading">{t("winery.title")}</h2>
      {rows.length === 0 ? (
        <p>
          {emptyBecauseFilter
            ? t("visitor.filterNoResults")
            : t("winery.noneInEvent")}
        </p>
      ) : (
        <ul className="visitor-winery-list">
          {rows.map((w) => {
            const n = wineCount(catalog, w.id);
            return (
              <li key={w.id}>
                <Link to={w.id} className="visitor-winery-card">
                  <div className="visitor-winery-card-name">{w.name}</div>
                  <div className="visitor-winery-card-meta">
                    <span>
                      {t("winery.cellarWord")} {w.locationNumber}
                    </span>
                    <span>
                      {n} {t("visitor.wineCountLabel")}
                    </span>
                  </div>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </PageMain>
  );
}
