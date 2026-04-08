import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { WineActionToggles } from "../components/WineActionToggles";
import { ErrorBlock, LoadingBlock, PageMain } from "../components/LoadState";
import { useVisitorActions } from "../context/VisitorActionsContext";
import { useSessionEventCatalog } from "../hooks/useSessionEventCatalog";
import { catalogErrorTitle } from "../lib/errorCopy";
import { groupWinesByColorSections } from "../lib/wineSort";
import {
  wineHasExpandableDetail,
  wineSecondaryLine,
  wineryWebHref,
} from "../lib/wineDisplay";
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

function WineryWineRow({ wine }: { wine: Wine }) {
  const [open, setOpen] = useState(false);
  const line2 = wineSecondaryLine(wine);
  const hasDescription = Boolean(wine.description?.trim());
  const hasDetail = wineHasExpandableDetail(wine);
  const { getStarLevel } = useVisitorActions();
  const isTop = getStarLevel(wine.id) === 2;

  const toggleRow = () => {
    if (hasDetail) setOpen((v) => !v);
  };

  return (
    <li
      className={`visitor-wine-card${hasDetail ? " visitor-wine-card--expandable" : ""}${isTop ? " visitor-wine-card--top-pick" : ""}`}
      onClick={toggleRow}
      onKeyDown={(e) => {
        if (!hasDetail) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          setOpen((v) => !v);
        }
      }}
      role={hasDetail ? "button" : undefined}
      tabIndex={hasDetail ? 0 : undefined}
      aria-expanded={hasDetail ? open : undefined}
    >
      <WineActionToggles
        wineId={wine.id}
        expandChevron={hasDetail ? { open } : undefined}
      >
        <span className="visitor-wine-label">{wine.label}</span>
      </WineActionToggles>
      {open && hasDetail ? (
        <div className="visitor-wine-extra-wrap">
          {line2 ? <div className="visitor-wine-line2">{line2}</div> : null}
          {hasDescription ? (
            <p className="visitor-wine-extra-note">{wine.description?.trim()}</p>
          ) : null}
        </div>
      ) : null}
    </li>
  );
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
                  <WineryWineRow key={wine.id} wine={wine} />
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </PageMain>
  );
}
