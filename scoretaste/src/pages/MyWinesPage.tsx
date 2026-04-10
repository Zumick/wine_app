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
  const [undoSecondsLeft, setUndoSecondsLeft] = useState<number>(UNDO_SECONDS);
  const [copiedToast, setCopiedToast] = useState(false);
  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const toastTickRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const copiedToastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
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
      if (copiedToastTimerRef.current !== null) {
        clearTimeout(copiedToastTimerRef.current);
      }
    },
    [],
  );

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
            <span className="visitor-mywines-share-icon" aria-hidden={true}>
              ↗
            </span>
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
