import type { MouseEvent } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { WineActionToggles } from "../components/WineActionToggles";
import { ErrorBlock, LoadingBlock, PageMain } from "../components/LoadState";
import { useVisitorActions } from "../context/VisitorActionsContext";
import { useSessionEventCatalog } from "../hooks/useSessionEventCatalog";
import { catalogErrorTitle } from "../lib/errorCopy";
import { t } from "../i18n";
import {
  postSaveSelection,
  type SavedWinesPayload,
  SHOW_RESTORED_TOAST_KEY,
} from "../lib/saveSelectionApi";
import {
  logVisitorEvent,
  wineIdsWithValidWinery,
  wineStarLevel,
  type WineStarLevel,
} from "../lib/visitorStorage";
import { wineHasExpandableDetail, wineSecondaryLine } from "../lib/wineDisplay";
import type { EventCatalog, Wine, Winery } from "../types";

const UNDO_SECONDS = 5;
type MyWinesViewMode = "flat" | "grouped";

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
  expandedWineryName?: string;
  cellarNumber?: string;
  showCellarInline?: boolean;
  onRemove: (
    wine: Wine,
    previousLevel: Exclude<WineStarLevel, 0>,
    cellarNumber?: string,
  ) => void;
};

function WineShortlistRow({
  wine,
  expandedWineryName,
  cellarNumber,
  showCellarInline = false,
  onRemove,
}: WineShortlistRowProps) {
  const [open, setOpen] = useState(false);
  const line2 = wineSecondaryLine(wine);
  const hasDescription = Boolean(wine.description?.trim());
  const hasDetail = wineHasExpandableDetail(wine) || Boolean(expandedWineryName);
  const { getStarLevel, cycleStarRating } = useVisitorActions();
  const level = getStarLevel(wine.id);
  const isTop = level === 2;

  const toggleRow = () => {
    if (hasDetail) setOpen((v) => !v);
  };

  const handleStarClick = (_e: MouseEvent<HTMLButtonElement>) => {
    cycleStarRating(wine.id);
  };

  return (
    <li
      className={`visitor-wine-card${hasDetail ? " visitor-wine-card--expandable" : ""}${isTop ? " visitor-wine-card--top-pick" : ""}`}
      style={{ listStyle: "none" }}
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
        onStarClick={handleStarClick}
      >
        <span className="visitor-wine-label-row">
          <span className="visitor-wine-label">{wine.label}</span>
          {showCellarInline && (cellarNumber ?? "").trim() ? (
            <span className="visitor-wine-cellar-inline">
              {(cellarNumber ?? "").trim()}
            </span>
          ) : null}
        </span>
      </WineActionToggles>
      {open && hasDetail ? (
        <div className="visitor-wine-extra-wrap">
          {expandedWineryName ? (
            <div className="visitor-wine-line2 visitor-wine-line2--winery">
              {(cellarNumber ?? "").trim() ? (
                <span className="visitor-wine-cellar-inline visitor-wine-cellar-inline--detail">
                  {(cellarNumber ?? "").trim()}
                </span>
              ) : null}
              <span className="visitor-wine-winery-name-inline">
                {expandedWineryName}
              </span>
            </div>
          ) : null}
          {line2 ? <div className="visitor-wine-line2">{line2}</div> : null}
          {hasDescription ? (
            <p className="visitor-wine-extra-note">{wine.description?.trim()}</p>
          ) : null}
          {level >= 1 ? (
            <div className="visitor-wine-detail-actions">
              <button
                type="button"
                className="visitor-wine-remove-btn"
                aria-label="Odebrat víno"
                onClick={(e) => {
                  e.stopPropagation();
                  onRemove(wine, level as Exclude<WineStarLevel, 0>, cellarNumber);
                }}
              >
                ×
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

type UndoPayload = {
  wineId: string;
  previousLevel: Exclude<WineStarLevel, 0>;
  wineLabel: string;
  cellarNumber?: string;
};
type StarredWineRow = { wine: Wine; level: WineStarLevel; winery: Winery | undefined };

function printableVintage(vintage: string): string {
  const v = vintage.trim();
  if (!v || v === "9999" || v === "1000") return "";
  return v;
}

function buildShareableSelectionText(
  rows: StarredWineRow[],
  shareUrl: string,
): string {
  const lines: string[] = ["Moje vína z degustace:", ""];
  for (const { wine, winery } of rows) {
    const cellar = winery?.locationNumber?.trim() || "—";
    const year = printableVintage(wine.vintage);
    const part = year
      ? `Sklep ${cellar} – ${wine.label} ${year}`
      : `Sklep ${cellar} – ${wine.label}`;
    lines.push(part);
  }
  lines.push("");
  lines.push("Otevřít průvodce:");
  lines.push(shareUrl);
  return lines.join("\n");
}

export function MyWinesPage() {
  const { eventId } = useParams<{ eventId: string }>();
  const catalogState = useSessionEventCatalog();
  const { getRecord, setStarLevel } = useVisitorActions();
  const [viewMode, setViewMode] = useState<MyWinesViewMode>("flat");
  const [undoToast, setUndoToast] = useState<UndoPayload | null>(null);
  const [undoSecondsLeft, setUndoSecondsLeft] = useState<number>(UNDO_SECONDS);
  const [savedShareToast, setSavedShareToast] = useState(false);
  const [shareErrorToast, setShareErrorToast] = useState(false);
  const [restoredToast, setRestoredToast] = useState(false);
  const [shareBusy, setShareBusy] = useState(false);
  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const toastTickRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const feedbackToastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const loggedOpenRef = useRef<string | null>(null);

  const clearToastTimer = useCallback(() => {
    if (toastTimerRef.current !== null) {
      clearTimeout(toastTimerRef.current);
      toastTimerRef.current = null;
    }
    if (toastTickRef.current !== null) {
      clearInterval(toastTickRef.current);
      toastTickRef.current = null;
    }
  }, []);

  useEffect(() => () => clearToastTimer(), [clearToastTimer]);

  useEffect(
    () => () => {
      if (feedbackToastTimerRef.current !== null) {
        clearTimeout(feedbackToastTimerRef.current);
      }
    },
    [],
  );

  useEffect(() => {
    try {
      if (sessionStorage.getItem(SHOW_RESTORED_TOAST_KEY) === "1") {
        sessionStorage.removeItem(SHOW_RESTORED_TOAST_KEY);
        setRestoredToast(true);
        feedbackToastTimerRef.current = window.setTimeout(() => {
          setRestoredToast(false);
          feedbackToastTimerRef.current = null;
        }, 4000);
      }
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    if (!eventId) return;
    if (catalogState.status !== "ok") return;
    const epochScope = catalogState.catalog.event.activeEpochId ?? null;
    const key = `${eventId}:${epochScope === null ? "none" : String(epochScope)}`;
    if (loggedOpenRef.current === key) return;
    loggedOpenRef.current = key;
    logVisitorEvent(eventId, "open_my_wines", epochScope);
  }, [catalogState, eventId]);

  const showRemovalToast = useCallback(
    (payload: UndoPayload) => {
      clearToastTimer();
      setUndoToast(payload);
      setUndoSecondsLeft(UNDO_SECONDS);
      toastTickRef.current = setInterval(() => {
        setUndoSecondsLeft((prev) => (prev > 1 ? prev - 1 : 1));
      }, 1000);
      toastTimerRef.current = setTimeout(() => {
        setUndoToast(null);
        setUndoSecondsLeft(UNDO_SECONDS);
        if (toastTickRef.current !== null) {
          clearInterval(toastTickRef.current);
          toastTickRef.current = null;
        }
        toastTimerRef.current = null;
      }, UNDO_SECONDS * 1000);
    },
    [clearToastTimer],
  );

  const dismissToast = useCallback(() => {
    clearToastTimer();
    setUndoToast(null);
    setUndoSecondsLeft(UNDO_SECONDS);
  }, [clearToastTimer]);

  const handleUndo = useCallback(() => {
    if (!undoToast) return;
    setStarLevel(undoToast.wineId, undoToast.previousLevel);
    dismissToast();
  }, [undoToast, setStarLevel, dismissToast]);

  const handleRemoveWine = useCallback(
    (
      wine: Wine,
      previousLevel: Exclude<WineStarLevel, 0>,
      cellarNumber?: string,
    ) => {
      setStarLevel(wine.id, 0);
      showRemovalToast({
        wineId: wine.id,
        previousLevel,
        wineLabel: wine.label,
        cellarNumber: (cellarNumber ?? "").trim() || undefined,
      });
    },
    [setStarLevel, showRemovalToast],
  );

  const showFeedbackToast = useCallback(
    (kind: "saved" | "error") => {
      if (feedbackToastTimerRef.current !== null) {
        clearTimeout(feedbackToastTimerRef.current);
      }
      if (kind === "saved") {
        setSavedShareToast(true);
        setShareErrorToast(false);
      } else {
        setShareErrorToast(true);
        setSavedShareToast(false);
      }
      feedbackToastTimerRef.current = window.setTimeout(() => {
        setSavedShareToast(false);
        setShareErrorToast(false);
        feedbackToastTimerRef.current = null;
      }, 4000);
    },
    [],
  );

  const starredRows = useMemo<StarredWineRow[]>(() => {
    if (catalogState.status !== "ok") return [];
    const { catalog } = catalogState;
    const validWineIds = wineIdsWithValidWinery(catalog);
    const wineryById = new Map(catalog.wineries.map((w) => [w.id, w] as const));
    return catalog.wines
      .filter((w) => validWineIds.has(w.id))
      .map((wine) => ({ wine, level: wineStarLevel(getRecord(wine.id)) }))
      .filter((row) => row.level >= 1)
      .sort((a, b) =>
        a.wine.label.localeCompare(b.wine.label, "cs", { sensitivity: "base" }),
      )
      .map((row) => ({
        ...row,
        winery: wineryById.get(row.wine.wineryId),
      }));
  }, [catalogState, getRecord]);

  const grouped = useMemo(() => {
    if (catalogState.status !== "ok") return [];
    const catalog = catalogState.catalog;
    const rows: { winery: Winery; wines: StarredWineRow[] }[] = [];
    for (const winery of sortedWineries(catalog)) {
      const winesHere = starredRows.filter((w) => w.wine.wineryId === winery.id);
      if (winesHere.length > 0) {
        rows.push({ winery, wines: winesHere });
      }
    }
    return rows;
  }, [catalogState, starredRows]);

  const eventName = useMemo(() => {
    if (catalogState.status !== "ok") return t("myWines.title");
    return catalogState.catalog.event.name.trim() || t("myWines.title");
  }, [catalogState]);

  const handleShareList = useCallback(async () => {
    if (starredRows.length === 0 || catalogState.status !== "ok" || !eventId) {
      return;
    }
    setShareBusy(true);
    const wines: SavedWinesPayload = {};
    for (const row of starredRows) {
      const rec = getRecord(row.wine.id);
      wines[row.wine.id] = {
        liked: rec.liked,
        wantToBuy: rec.wantToBuy,
      };
    }
    const epochId = catalogState.catalog.event.activeEpochId ?? null;
    const res = await postSaveSelection({
      event_id: eventId,
      epoch_id: epochId,
      wines,
    });
    setShareBusy(false);
    if (!res.ok) {
      showFeedbackToast("error");
      return;
    }
    const text = buildShareableSelectionText(starredRows, res.share_url);
    const nav = navigator as Navigator & {
      share?: (data: { title?: string; text?: string }) => Promise<void>;
      clipboard?: { writeText: (value: string) => Promise<void> };
    };

    if (nav.share) {
      try {
        await nav.share({ title: eventName, text });
        showFeedbackToast("saved");
        return;
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") {
          return;
        }
      }
    }
    if (nav.clipboard?.writeText) {
      try {
        await nav.clipboard.writeText(text);
      } catch {
        /* no-op */
      }
    }
    showFeedbackToast("saved");
  }, [
    starredRows,
    catalogState,
    eventId,
    getRecord,
    eventName,
    showFeedbackToast,
  ]);

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
      <div className="visitor-mywines-heading-row">
        <h1 className="visitor-page-heading visitor-page-heading--tight">
          {t("myWines.title")}
        </h1>
        <div className="visitor-mywines-head-actions">
          <button
            type="button"
            className="visitor-mywines-mode-toggle"
            onClick={() =>
              setViewMode((prev) => (prev === "flat" ? "grouped" : "flat"))
            }
          >
            {viewMode === "flat"
              ? t("myWines.showGrouped")
              : t("myWines.showFlat")}
          </button>
          <button
            type="button"
            className="visitor-mywines-share-btn"
            aria-label={t("myWines.shareAria")}
            onClick={() => void handleShareList()}
            disabled={starredRows.length === 0 || shareBusy}
          >
            <span className="visitor-mywines-share-icon" aria-hidden={true}>
              ↗
            </span>
            <span className="visitor-mywines-share-label">{t("myWines.shareMySelection")}</span>
          </button>
        </div>
      </div>

      {starredRows.length === 0 ? (
        <div role="status" className="visitor-mywines-empty">
          <p className="visitor-mywines-empty-lead">{t("myWines.empty")}</p>
          <p className="visitor-mywines-empty-cta">
            <Link to={wineryListPath}>{t("myWines.emptySavedCta")}</Link>
          </p>
        </div>
      ) : viewMode === "flat" ? (
        <ul className="visitor-mywines-ul visitor-mywines-flat-list">
          {starredRows.map(({ wine, winery }) => (
            <WineShortlistRow
              key={wine.id}
              wine={wine}
              showCellarInline
              cellarNumber={winery?.locationNumber ?? ""}
              expandedWineryName={winery?.name}
              onRemove={handleRemoveWine}
            />
          ))}
        </ul>
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
              {wines.map(({ wine }) => (
                <WineShortlistRow
                  key={wine.id}
                  wine={wine}
                  onRemove={handleRemoveWine}
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
          <div className="visitor-mywines-toast-text">
            <div>
              Odebráno: {undoToast.wineLabel}
              {undoToast.cellarNumber
                ? ` · sklep ${undoToast.cellarNumber}`
                : ""}
            </div>
            <div className="visitor-mywines-toast-meta">{undoSecondsLeft}s</div>
          </div>
          <button type="button" className="visitor-mywines-toast-undo" onClick={handleUndo}>
            Zpět
          </button>
        </div>
      ) : null}
      {!undoToast && savedShareToast ? (
        <div
          className="visitor-mywines-toast"
          role="status"
          aria-live="polite"
        >
          <span className="visitor-mywines-toast-text">
            {t("myWines.savedShareToast")}
          </span>
        </div>
      ) : null}
      {!undoToast && shareErrorToast ? (
        <div
          className="visitor-mywines-toast visitor-mywines-toast--warn"
          role="alert"
        >
          <span className="visitor-mywines-toast-text">
            {t("myWines.shareSaveError")}
          </span>
        </div>
      ) : null}
      {!undoToast && restoredToast ? (
        <div
          className="visitor-mywines-toast"
          role="status"
          aria-live="polite"
        >
          <span className="visitor-mywines-toast-text">
            {t("myWines.restoredSelectionToast")}
          </span>
        </div>
      ) : null}
    </PageMain>
  );
}
