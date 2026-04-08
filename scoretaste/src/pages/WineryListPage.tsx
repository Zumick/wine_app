import { Link, useParams } from "react-router-dom";
import { ErrorBlock, LoadingBlock, PageMain } from "../components/LoadState";
import { useVisitorActions } from "../context/VisitorActionsContext";
import { useSessionEventCatalog, useWineryListFilter } from "../hooks/useSessionEventCatalog";
import { catalogErrorTitle } from "../lib/errorCopy";
import { t } from "../i18n";
import type { EventCatalog, Winery } from "../types";

function sortedWineries(catalog: EventCatalog): Winery[] {
  return [...catalog.wineries].sort((a, b) => {
    const ae = a.locationNumber.trim() ? 0 : 1;
    const be = b.locationNumber.trim() ? 0 : 1;
    if (ae !== be) return ae - be;
    return a.locationNumber.localeCompare(b.locationNumber, "cs", {
      numeric: true,
    });
  });
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
  const { isWineryVisited, toggleWineryVisited } = useVisitorActions();

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
            const visited = isWineryVisited(w.id);
            return (
              <li key={w.id} className="visitor-winery-li">
                <div className="visitor-winery-row">
                  <Link to={w.id} className="visitor-winery-main">
                    <span className="visitor-loc-badge">
                      {w.locationNumber.trim() || "—"}
                    </span>
                    <span className="visitor-winery-name">{w.name}</span>
                  </Link>
                  <button
                    type="button"
                    role="checkbox"
                    aria-checked={visited}
                    className={`visitor-winery-visited-toggle${visited ? " visitor-winery-visited-toggle-on" : ""}`}
                    aria-label={
                      visited
                        ? t("winery.visitedToggleAriaOff")
                        : t("winery.visitedToggleAriaOn")
                    }
                    onClick={(e) => {
                      e.preventDefault();
                      toggleWineryVisited(w.id);
                    }}
                  >
                    {visited ? "✓" : ""}
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </PageMain>
  );
}
