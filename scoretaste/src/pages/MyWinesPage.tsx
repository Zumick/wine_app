import type { MouseEvent, TransitionEvent } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { WineActionToggles } from "../components/WineActionToggles";
import { ErrorBlock, LoadingBlock, PageMain } from "../components/LoadState";
import { useVisitorActions } from "../context/VisitorActionsContext";
import { useSessionEventCatalog } from "../hooks/useSessionEventCatalog";
import { catalogErrorTitle } from "../lib/errorCopy";
import { t } from "../i18n";
import {
  wineIdsWithValidWinery,
  wineStarLevel,
  type WineStarLevel,
} from "../lib/visitorStorage";
import { wineHasExpandableDetail, wineSecondaryLine } from "../lib/wineDisplay";
import { compareWinesByColorThenLabel } from "../lib/wineSort";
import type { EventCatalog, Wine, Winery } from "../types";

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

type WineShortlistRowProps = {
  wine: Wine;
  onRemovalExitComplete: (
    wineId: string,
    previousLevel: Exclude<WineStarLevel, 0>,
  ) => void;
};

function WineShortlistRow({ wine, onRemovalExitComplete }: WineShortlistRowProps) {
  const [open, setOpen] = useState(false);
  const [exiting, setExiting] = useState(false);
  const removalLevelRef = useRef<Exclude<WineStarLevel, 0>>(2);
  const line2 = wineSecondaryLine(wine);
  const hasDescription = Boolean(wine.description?.trim());
  const hasDetail = wineHasExpandableDetail(wine);
  const { getStarLevel, cycleStarRating } = useVisitorActions();
  const level = getStarLevel(wine.id);
  const isTop = level === 2;

  const toggleRow = () => {
    if (hasDetail && !exiting) setOpen((v) => !v);
  };

  const handleStarClick = (_e: MouseEvent<HTMLButtonElement>) => {
    if (exiting) return;
    if (level === 2) {
      removalLevelRef.current = 2;
      setExiting(true);
      return;
    }
    cycleStarRating(wine.id);
  };

  const handleTransitionEnd = (e: TransitionEvent<HTMLLIElement>) => {
    if (e.target !== e.currentTarget) return;
    if (!exiting) return;
    if (e.propertyName !== "opacity") return;
    onRemovalExitComplete(wine.id, removalLevelRef.current);
  };

  return (
    <li
      className={`visitor-wine-card${hasDetail ? " visitor-wine-card--expandable" : ""}${isTop ? " visitor-wine-card--top-pick" : ""}${exiting ? " visitor-wine-card--mywines-exiting" : ""}`}
      style={{ listStyle: "none" }}
      onClick={toggleRow}
      onTransitionEnd={handleTransitionEnd}
      onKeyDown={(e) => {
        if (!hasDetail || exiting) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          setOpen((v) => !v);
        }
      }}
      role={hasDetail ? "button" : undefined}
      tabIndex={hasDetail && !exiting ? 0 : undefined}
      aria-expanded={hasDetail ? open : undefined}
    >
      <WineActionToggles
        wineId={wine.id}
        expandChevron={hasDetail && !exiting ? { open } : undefined}
        onStarClick={handleStarClick}
      >
        <span className="visitor-wine-label">{wine.label}</span>
      </WineActionToggles>
      {open && hasDetail && !exiting ? (
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

type UndoPayload = { wineId: string; previousLevel: Exclude<WineStarLevel, 0> };

export function MyWinesPage() {
  const { eventId } = useParams<{ eventId: string }>();
  const catalogState = useSessionEventCatalog();
  const { getRecord, setStarLevel } = useVisitorActions();
  const [undoToast, setUndoToast] = useState<UndoPayload | null>(null);
  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearToastTimer = useCallback(() => {
    if (toastTimerRef.current !== null) {
      clearTimeout(toastTimerRef.current);
      toastTimerRef.current = null;
    }
  }, []);

  useEffect(() => () => clearToastTimer(), [clearToastTimer]);

  const showRemovalToast = useCallback(
    (payload: UndoPayload) => {
      clearToastTimer();
      setUndoToast(payload);
      toastTimerRef.current = setTimeout(() => {
        setUndoToast(null);
        toastTimerRef.current = null;
      }, 2500);
    },
    [clearToastTimer],
  );

  const dismissToast = useCallback(() => {
    clearToastTimer();
    setUndoToast(null);
  }, [clearToastTimer]);

  const handleUndo = useCallback(() => {
    if (!undoToast) return;
    setStarLevel(undoToast.wineId, undoToast.previousLevel);
    dismissToast();
  }, [undoToast, setStarLevel, dismissToast]);

  const handleRemovalExitComplete = useCallback(
    (wineId: string, previousLevel: Exclude<WineStarLevel, 0>) => {
      setStarLevel(wineId, 0);
      showRemovalToast({ wineId, previousLevel });
    },
    [setStarLevel, showRemovalToast],
  );

  const starredWines = useMemo(() => {
    if (catalogState.status !== "ok") return [];
    const { catalog } = catalogState;
    const validWineIds = wineIdsWithValidWinery(catalog);
    return catalog.wines
      .filter((w) => validWineIds.has(w.id))
      .filter((w) => wineStarLevel(getRecord(w.id)) >= 1)
      .sort(compareWinesByColorThenLabel);
  }, [catalogState, getRecord]);

  const grouped = useMemo(() => {
    if (catalogState.status !== "ok") return [];
    const catalog = catalogState.catalog;
    const rows: { winery: Winery; wines: Wine[] }[] = [];
    for (const winery of sortedWineries(catalog)) {
      const winesHere = starredWines.filter((w) => w.wineryId === winery.id);
      if (winesHere.length > 0) {
        rows.push({ winery, wines: winesHere });
      }
    }
    return rows;
  }, [catalogState, starredWines]);

  if (!eventId) {
    return <ErrorBlock title={catalogErrorTitle("MISSING_EVENT_ID")} />;
  }
  if (catalogState.status === "loading") {
    return <LoadingBlock />;
  }
  if (catalogState.status === "error") {
    return (
      <ErrorBlock
        title={catalogErrorTitle(catalogState.code)}
        hint={t("common.hintRetry")}
      />
    );
  }

  const wineryListPath = `/e/${eventId}/wineries`;

  return (
    <PageMain
      className={`visitor-mywines-page${undoToast ? " visitor-mywines-page--toast" : ""}`}
    >
      <h1 className="visitor-page-heading visitor-page-heading--tight">
        {t("myWines.title")}
      </h1>

      {starredWines.length === 0 ? (
        <div role="status" className="visitor-mywines-empty">
          <p className="visitor-mywines-empty-lead">{t("myWines.empty")}</p>
          <p className="visitor-mywines-empty-cta">
            <Link to={wineryListPath}>{t("myWines.emptySavedCta")}</Link>
          </p>
        </div>
      ) : (
        grouped.map(({ winery, wines }) => (
          <section key={winery.id} className="visitor-mywines-section">
            <h2 className="visitor-mywines-winery-heading">
              <span
                className="visitor-loc-badge"
                aria-label={`${t("winery.cellarWord")} ${winery.locationNumber.trim() || "—"}`}
              >
                {winery.locationNumber.trim() || "—"}
              </span>
              <span className="visitor-mywines-winery-name">{winery.name}</span>
            </h2>
            <ul className="visitor-mywines-ul">
              {wines.map((wine) => (
                <WineShortlistRow
                  key={wine.id}
                  wine={wine}
                  onRemovalExitComplete={handleRemovalExitComplete}
                />
              ))}
            </ul>
          </section>
        ))
      )}

      {undoToast ? (
        <div
          className="visitor-mywines-toast"
          role="status"
          aria-live="polite"
        >
          <span className="visitor-mywines-toast-text">
            {t("myWines.removedToast")}
          </span>
          <button
            type="button"
            className="visitor-mywines-toast-undo"
            onClick={handleUndo}
          >
            {t("myWines.undo")}
          </button>
        </div>
      ) : null}
    </PageMain>
  );
}
