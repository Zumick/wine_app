import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { WineActionToggles } from "../components/WineActionToggles";
import { ErrorBlock, LoadingBlock, PageMain } from "../components/LoadState";
import { useSessionEventCatalog } from "../hooks/useSessionEventCatalog";
import { catalogErrorTitle } from "../lib/errorCopy";
import { groupWinesByColorSections } from "../lib/wineSort";
import { wineSecondaryLine, wineryWebHref } from "../lib/wineDisplay";
import { t } from "../i18n";
import type { EventCatalog, Wine } from "../types";

function winesForWinery(catalog: EventCatalog, wineryId: string): Wine[] {
  return catalog.wines.filter((w) => w.wineryId === wineryId);
}

function colorSectionTitle(color: string): string {
  switch (color) {
    case "white":
      return t("winery.colorWhiteWines");
    case "rose":
      return t("winery.colorRoseWines");
    case "red":
      return t("winery.colorRedWines");
    case "orange":
      return t("winery.colorOrangeWines");
    default:
      return t("winery.colorWhiteWines");
  }
}

function wineryHasExpandableDetail(note?: string, web?: string): boolean {
  return Boolean((note && note.trim()) || (web && web.trim()));
}

export function WineryDetailPage() {
  const { eventId, wineryId } = useParams<{
    eventId: string;
    wineryId: string;
  }>();
  const state = useSessionEventCatalog();
  const [detailOpen, setDetailOpen] = useState(false);

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
  const sections = groupWinesByColorSections(wines);
  const expandable = wineryHasExpandableDetail(winery.note, winery.web);

  return (
    <PageMain>
      <div className="visitor-winery-page-head">
        <div className="visitor-winery-page-head-main">
          <span
            className="visitor-loc-badge"
            aria-label={`${t("winery.cellarWord")} ${winery.locationNumber.trim() || "—"}`}
          >
            {winery.locationNumber.trim() || "—"}
          </span>
          <h1 className="visitor-winery-page-title">{winery.name}</h1>
        </div>
        {expandable ? (
          <button
            type="button"
            className={`visitor-winery-head-chevron${detailOpen ? " visitor-winery-head-chevron-open" : ""}`}
            aria-expanded={detailOpen}
            aria-label={t("winery.detailToggleAria")}
            onClick={() => setDetailOpen((o) => !o)}
          >
            ▼
          </button>
        ) : null}
      </div>
      {detailOpen && expandable ? (
        <div className="visitor-winery-detail visitor-winery-detail-below-head">
          {winery.note?.trim() ? (
            <p className="visitor-winery-note">{winery.note.trim()}</p>
          ) : null}
          {winery.web?.trim() ? (
            <p className="visitor-winery-web-wrap">
              <a
                href={wineryWebHref(winery.web)}
                target="_blank"
                rel="noopener noreferrer"
                className="visitor-winery-web"
              >
                {winery.web.trim()}
              </a>
            </p>
          ) : null}
        </div>
      ) : null}
      {wines.length === 0 ? (
        <p>{t("winery.noWinesHere")}</p>
      ) : (
        <div className="visitor-wine-sections">
          {sections.map(({ color, wines: groupWines }) => (
            <section
              key={color}
              className="visitor-wine-color-section"
              aria-labelledby={`wine-color-${color}`}
            >
              <h3
                id={`wine-color-${color}`}
                className="visitor-wine-color-heading"
              >
                {colorSectionTitle(color)}
              </h3>
              <ul className="visitor-wine-list-block">
                {groupWines.map((wine: Wine) => (
                  <li key={wine.id} className="visitor-wine-card">
                    <WineActionToggles wineId={wine.id}>
                      <span className="visitor-wine-label">{wine.label}</span>
                    </WineActionToggles>
                    <div className="visitor-wine-line2">
                      {wineSecondaryLine(wine)}
                    </div>
                    {wine.description ? (
                      <p
                        style={{
                          margin: "0.35rem 0 0",
                          color: "#333",
                          fontSize: "0.9rem",
                        }}
                      >
                        {wine.description}
                      </p>
                    ) : null}
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </PageMain>
  );
}
