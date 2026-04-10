import { useEffect, useRef } from "react";
import { Link, useParams } from "react-router-dom";
import { EventWineryMapView } from "../components/EventWineryMapView";
import { ErrorBlock, LoadingBlock, PageMain } from "../components/LoadState";
import { useVisitorActions } from "../context/VisitorActionsContext";
import {
  useSessionEventCatalog,
  useWineryBrowseView,
  useWineryListFilter,
} from "../hooks/useSessionEventCatalog";
import { hasEventMapImage } from "../lib/eventMapAsset";
import { catalogErrorTitle } from "../lib/errorCopy";
import { logVisitorEvent } from "../lib/visitorStorage";
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
  const [browseView, setBrowseView] = useWineryBrowseView();
  const { isWineryVisited, toggleWineryVisited } = useVisitorActions();
  const loggedOpenRef = useRef<string | null>(null);

  const hasMap = Boolean(eventId && hasEventMapImage(eventId));

  useEffect(() => {
    if (!hasMap && browseView === "map") {
      setBrowseView("list");
    }
  }, [hasMap, browseView, setBrowseView]);

  useEffect(() => {
    if (!eventId) return;
    if (state.status !== "ok") return;
    const epochScope = state.catalog.event.activeEpochId ?? null;
    const key = `${eventId}:${epochScope === null ? "none" : String(epochScope)}`;
    if (loggedOpenRef.current === key) return;
    loggedOpenRef.current = key;
    logVisitorEvent(eventId, "open_winery_list", epochScope);
  }, [state, eventId]);

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
      <div className="visitor-winery-page-head">
        <h2 className="visitor-page-heading visitor-page-heading--winery-row">
          {t("winery.title")}
        </h2>
        {hasMap ? (
          <div
            className="visitor-browse-switch"
            role="group"
            aria-label={t("winery.mapSwitchAria")}
          >
            <button
              type="button"
              className={`visitor-browse-switch-btn${browseView === "list" ? " visitor-browse-switch-btn-active" : ""}`}
              onClick={() => setBrowseView("list")}
            >
              {t("winery.viewList")}
            </button>
            <button
              type="button"
              className={`visitor-browse-switch-btn${browseView === "map" ? " visitor-browse-switch-btn-active" : ""}`}
              onClick={() => setBrowseView("map")}
            >
              {t("winery.viewMap")}
            </button>
          </div>
        ) : null}
      </div>

      {browseView === "map" && hasMap ? (
        <EventWineryMapView eventId={eventId} catalog={catalog} />
      ) : rows.length === 0 ? (
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
