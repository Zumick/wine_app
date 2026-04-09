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

const PENDING_REMOVE_SECONDS = 5;
const PENDING_REMOVE_MS = PENDING_REMOVE_SECONDS * 1000;
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
  onRemovalExitComplete: (
    wineId: string,
    previousLevel: Exclude<WineStarLevel, 0>,
  ) => void;
};

function WineShortlistRow({
  wine,
  expandedWineryName,
  cellarNumber,
  showCellarInline = false,
  onRemovalExitComplete,
}: WineShortlistRowProps) {
  const [open, setOpen] = useState(false);
  const [pendingRemove, setPendingRemove] = useState(false);
  const [remainingSeconds, setRemainingSeconds] = useState<number | null>(null);
  const [exiting, setExiting] = useState(false);
  const removalLevelRef = useRef<Exclude<WineStarLevel, 0>>(2);
  const pendingTickRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pendingFinishRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const line2 = wineSecondaryLine(wine);
  const hasDescription = Boolean(wine.description?.trim());
  const hasDetail = wineHasExpandableDetail(wine) || Boolean(expandedWineryName);
  const { getStarLevel, cycleStarRating } = useVisitorActions();
  const level = getStarLevel(wine.id);
  const isTop = level === 2;

  const clearPendingTimers = useCallback(() => {
    if (pendingTickRef.current !== null) {
      clearInterval(pendingTickRef.current);
      pendingTickRef.current = null;
    }
    if (pendingFinishRef.current !== null) {
      clearTimeout(pendingFinishRef.current);
      pendingFinishRef.current = null;
    }
  }, []);

  const cancelPendingRemoval = useCallback(() => {
    clearPendingTimers();
    setPendingRemove(false);
    setRemainingSeconds(null);
  }, [clearPendingTimers]);

  const startPendingRemoval = useCallback(() => {
    clearPendingTimers();
    setPendingRemove(true);
    setRemainingSeconds(PENDING_REMOVE_SECONDS);

    pendingTickRef.current = setInterval(() => {
      setRemainingSeconds((prev) => {
        if (prev === null) return null;
        return prev > 1 ? prev - 1 : 1;
      });
    }, 1000);

    pendingFinishRef.current = setTimeout(() => {
      clearPendingTimers();
      setPendingRemove(false);
      setRemainingSeconds(null);
      setExiting(true);
    }, PENDING_REMOVE_MS);
  }, [clearPendingTimers]);

  useEffect(() => {
    if (!pendingRemove) return;
    if (level < 1) {
      cancelPendingRemoval();
    }
  }, [pendingRemove, level, cancelPendingRemoval]);

  useEffect(() => () => clearPendingTimers(), [clearPendingTimers]);

  const toggleRow = () => {
    if (hasDetail && !exiting && !pendingRemove) setOpen((v) => !v);
  };

  const handleStarClick = (_e: MouseEvent<HTMLButtonElement>) => {
    if (exiting) return;
    if (pendingRemove) {
      cancelPendingRemoval();
      return;
    }
    cycleStarRating(wine.id);
  };

  const handleStarLongPress = () => {
    if (exiting || pendingRemove) return;
    if (level < 1) return;
    removalLevelRef.current = level as Exclude<WineStarLevel, 0>;
    startPendingRemoval();
  };

  const handleTransitionEnd = (e: TransitionEvent<HTMLLIElement>) => {
    if (e.target !== e.currentTarget) return;
    if (!exiting) return;
    if (e.propertyName !== "opacity") return;
    onRemovalExitComplete(wine.id, removalLevelRef.current);
  };

  return (
    <li
      className={`visitor-wine-card${hasDetail ? " visitor-wine-card--expandable" : ""}${isTop ? " visitor-wine-card--top-pick" : ""}${pendingRemove ? " visitor-wine-card--mywines-pending" : ""}${exiting ? " visitor-wine-card--mywines-exiting" : ""}`}
      style={{ listStyle: "none" }}
      onClick={toggleRow}
      onTransitionEnd={handleTransitionEnd}
      onKeyDown={(e) => {
        if (!hasDetail || exiting || pendingRemove) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          setOpen((v) => !v);
        }
      }}
      role={hasDetail ? "button" : undefined}
      tabIndex={hasDetail && !exiting && !pendingRemove ? 0 : undefined}
      aria-expanded={hasDetail ? open : undefined}
    >
      <WineActionToggles
        wineId={wine.id}
        expandChevron={hasDetail && !exiting && !pendingRemove ? { open } : undefined}
        onStarClick={handleStarClick}
        onStarLongPress={handleStarLongPress}
        starAriaLabel={
          pendingRemove
            ? t("myWines.cancelRemovalAria")
            : level >= 1
              ? t("myWines.topLongPressAria")
              : undefined
        }
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
      {pendingRemove ? (
        <div className="visitor-wine-removal-pending" role="status" aria-live="polite">
          <span className="visitor-wine-removal-countdown">
            {t("myWines.removalCountdown").replace(
              "{seconds}",
              String(remainingSeconds ?? PENDING_REMOVE_SECONDS),
            )}
          </span>
          <button
            type="button"
            className="visitor-wine-removal-cancel"
            onClick={(e) => {
              e.stopPropagation();
              cancelPendingRemoval();
            }}
          >
            {t("myWines.cancelRemoval")}
          </button>
        </div>
      ) : null}
      {open && hasDetail && !exiting && !pendingRemove ? (
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
        </div>
      ) : null}
    </li>
  );
}

type UndoPayload = { wineId: string; previousLevel: Exclude<WineStarLevel, 0> };
type StarredWineRow = { wine: Wine; level: WineStarLevel; winery: Winery | undefined };

function printableVintage(vintage: string): string {
  const v = vintage.trim();
  if (!v || v === "9999" || v === "1000") return "";
  return v;
}

function buildShareListText(rows: StarredWineRow[], eventName: string): string {
  const groups = new Map<string, StarredWineRow[]>();
  for (const row of rows) {
    const cellar = row.winery?.locationNumber?.trim() || "—";
    const bucket = groups.get(cellar);
    if (bucket) {
      bucket.push(row);
    } else {
      groups.set(cellar, [row]);
    }
  }

  const lines: string[] = [];
  lines.push(`Moje vína – ${eventName}`);
  lines.push("");

  for (const [cellar, items] of groups.entries()) {
    lines.push(`Sklep ${cellar}`);
    lines.push("");
    for (const { wine } of items) {
      const year = printableVintage(wine.vintage);
      lines.push(year ? `* ${wine.label} (${year})` : `* ${wine.label}`);
    }
    lines.push("");
  }

  const topRows = rows.filter((row) => row.level === 2);
  if (topRows.length > 0) {
    lines.push("TOP:");
    for (const { wine, winery } of topRows) {
      const cellar = winery?.locationNumber?.trim() || "—";
      lines.push(`Sklep ${cellar} – ${wine.label}`);
    }
  }

  while (lines.length > 0 && lines[lines.length - 1] === "") {
    lines.pop();
  }
  return lines.join("\n");
}

export function MyWinesPage() {
  const { eventId } = useParams<{ eventId: string }>();
  const catalogState = useSessionEventCatalog();
  const { getRecord, setStarLevel } = useVisitorActions();
  const [viewMode, setViewMode] = useState<MyWinesViewMode>("flat");
  const [undoToast, setUndoToast] = useState<UndoPayload | null>(null);
  const [copiedToast, setCopiedToast] = useState(false);
  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const copiedToastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearToastTimer = useCallback(() => {
    if (toastTimerRef.current !== null) {
      clearTimeout(toastTimerRef.current);
      toastTimerRef.current = null;
    }
  }, []);

  useEffect(() => () => clearToastTimer(), [clearToastTimer]);

  useEffect(
    () => () => {
      if (copiedToastTimerRef.current !== null) {
        clearTimeout(copiedToastTimerRef.current);
      }
    },
    [],
  );

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

  const showCopiedToast = useCallback(() => {
    if (copiedToastTimerRef.current !== null) {
      clearTimeout(copiedToastTimerRef.current);
    }
    setCopiedToast(true);
    copiedToastTimerRef.current = setTimeout(() => {
      setCopiedToast(false);
      copiedToastTimerRef.current = null;
    }, 2200);
  }, []);

  const starredRows = useMemo<StarredWineRow[]>(() => {
    if (catalogState.status !== "ok") return [];
    const { catalog } = catalogState;
    const validWineIds = wineIdsWithValidWinery(catalog);
    const wineryById = new Map(catalog.wineries.map((w) => [w.id, w] as const));
    return catalog.wines
      .filter((w) => validWineIds.has(w.id))
      .map((wine) => ({ wine, level: wineStarLevel(getRecord(wine.id)) }))
      .filter((row) => row.level >= 1)
      .sort((a, b) => {
        if (a.level !== b.level) return b.level - a.level;
        return compareWinesByColorThenLabel(a.wine, b.wine);
      })
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
    if (starredRows.length === 0) return;
    const text = buildShareListText(starredRows, eventName);
    const nav = navigator as Navigator & {
      share?: (data: { title?: string; text?: string }) => Promise<void>;
      clipboard?: { writeText: (value: string) => Promise<void> };
    };

    if (nav.share) {
      try {
        await nav.share({ title: eventName, text });
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
        showCopiedToast();
      } catch {
        /* no-op */
      }
    }
  }, [eventName, showCopiedToast, starredRows]);

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
            onClick={handleShareList}
            disabled={starredRows.length === 0}
          >
            <svg
              className="visitor-mywines-share-icon"
              viewBox="0 0 24 24"
              aria-hidden="true"
            >
              <path
                d="M18 8a3 3 0 1 0-2.82-4H15a3 3 0 0 0 .18 1.01L8.9 8.16a3 3 0 0 0-1.9-.66 3 3 0 1 0 1.9 5.34l6.28 3.15A3 3 0 0 0 15 17a3 3 0 1 0 .18 1.01L8.9 14.85A3 3 0 0 0 9 14c0-.3-.04-.59-.1-.85l6.28-3.14c.52.61 1.29.99 2.16.99z"
                fill="currentColor"
              />
            </svg>
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
              onRemovalExitComplete={handleRemovalExitComplete}
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
      {!undoToast && copiedToast ? (
        <div
          className="visitor-mywines-toast"
          role="status"
          aria-live="polite"
        >
          <span className="visitor-mywines-toast-text">
            {t("myWines.copiedToast")}
          </span>
        </div>
      ) : null}
    </PageMain>
  );
}
