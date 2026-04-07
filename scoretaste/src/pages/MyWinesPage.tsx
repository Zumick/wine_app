import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { WineActionToggles } from "../components/WineActionToggles";
import { ErrorBlock, LoadingBlock, PageMain } from "../components/LoadState";
import { useVisitorActions } from "../context/VisitorActionsContext";
import { useSessionEventCatalog } from "../hooks/useSessionEventCatalog";
import { catalogErrorTitle } from "../lib/errorCopy";
import { t } from "../i18n";
import { wineIdsWithValidWinery } from "../lib/visitorStorage";
import { wineSecondaryLine } from "../lib/wineDisplay";
import type { EventCatalog, Wine, Winery } from "../types";

function sortedWineries(catalog: EventCatalog): Winery[] {
  return [...catalog.wineries].sort((a, b) =>
    a.locationNumber.localeCompare(b.locationNumber, "cs", { numeric: true }),
  );
}

function sortedWines(wines: Wine[]): Wine[] {
  return [...wines].sort((a, b) => a.label.localeCompare(b.label, "cs"));
}

type Segment = "saved" | "buy";

function WineShortlistRow({ wine }: { wine: Wine }) {
  return (
    <li className="visitor-wine-card" style={{ listStyle: "none" }}>
      <div className="visitor-wine-label">{wine.label}</div>
      <div className="visitor-wine-line2">{wineSecondaryLine(wine)}</div>
      <WineActionToggles wineId={wine.id} />
    </li>
  );
}

export function MyWinesPage() {
  const { eventId } = useParams<{ eventId: string }>();
  const catalogState = useSessionEventCatalog();
  const { getRecord } = useVisitorActions();
  const [segment, setSegment] = useState<Segment>("buy");

  const winesMatchingSegment = useMemo(() => {
    if (catalogState.status !== "ok") return [];
    const { catalog } = catalogState;
    const validWineIds = wineIdsWithValidWinery(catalog);
    return catalog.wines
      .filter((w) => validWineIds.has(w.id))
      .filter((w) => {
        const r = getRecord(w.id);
        if (segment === "buy") return r.wantToBuy;
        return r.liked || r.wantToBuy;
      });
  }, [catalogState, segment, getRecord]);

  const grouped = useMemo(() => {
    if (catalogState.status !== "ok") return [];
    const catalog = catalogState.catalog;
    const rows: { winery: Winery; wines: Wine[] }[] = [];
    for (const winery of sortedWineries(catalog)) {
      const winesHere = sortedWines(
        winesMatchingSegment.filter((w) => w.wineryId === winery.id),
      );
      if (winesHere.length > 0) {
        rows.push({ winery, wines: winesHere });
      }
    }
    return rows;
  }, [catalogState, winesMatchingSegment]);

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
    <PageMain>
      <h1 className="visitor-page-heading" style={{ marginTop: 0 }}>
        {t("myWines.title")}
      </h1>

      <div
        role="tablist"
        aria-label={t("myWines.tablistAria")}
        style={{
          display: "flex",
          gap: "0.35rem",
          marginBottom: "0.75rem",
          flexWrap: "wrap",
        }}
      >
        <button
          type="button"
          role="tab"
          id="mywines-tab-buy"
          aria-selected={segment === "buy"}
          onClick={() => setSegment("buy")}
          style={{
            padding: "0.45rem 0.9rem",
            cursor: "pointer",
            fontWeight: segment === "buy" ? 700 : 400,
            border: "1px solid #ccc",
            borderRadius: "6px",
            background: segment === "buy" ? "#f5f5f5" : "#fff",
          }}
        >
          {t("myWines.segmentBuy")}
        </button>
        <button
          type="button"
          role="tab"
          id="mywines-tab-saved"
          aria-selected={segment === "saved"}
          onClick={() => setSegment("saved")}
          style={{
            padding: "0.45rem 0.9rem",
            cursor: "pointer",
            fontWeight: segment === "saved" ? 700 : 400,
            border: "1px solid #ccc",
            borderRadius: "6px",
            background: segment === "saved" ? "#f5f5f5" : "#fff",
          }}
        >
          {t("myWines.segmentSaved")}
        </button>
      </div>

      {segment === "buy" ? (
        <h2 style={{ margin: "0 0 1rem", fontSize: "1.35rem" }}>
          {t("myWines.buyTitle")}
        </h2>
      ) : null}

      {winesMatchingSegment.length === 0 ? (
        <div
          role="status"
          style={{
            padding: "1rem 0",
            maxWidth: "28rem",
            lineHeight: 1.5,
          }}
        >
          <p style={{ marginTop: 0, marginBottom: "0.75rem" }}>
            {segment === "saved" ? t("myWines.empty") : t("myWines.buyEmptyShort")}
          </p>
          <p style={{ margin: 0 }}>
            <Link
              to={wineryListPath}
              style={{ fontWeight: 600, textDecoration: "underline" }}
            >
              {segment === "saved"
                ? t("myWines.emptySavedCta")
                : t("myWines.buyEmptyCta")}
            </Link>
          </p>
        </div>
      ) : (
        grouped.map(({ winery, wines }) => (
          <section
            key={winery.id}
            style={{
              marginBottom: "1.75rem",
            }}
          >
            <h2
              style={{
                fontSize: "1.15rem",
                margin: "0 0 0.65rem",
                paddingBottom: "0.4rem",
                borderBottom: "1px solid #ddd",
                fontWeight: 700,
              }}
            >
              <span style={{ display: "block" }}>{winery.name}</span>
              <span
                style={{
                  display: "block",
                  fontSize: "0.88rem",
                  fontWeight: 500,
                  color: "#555",
                  marginTop: "0.2rem",
                }}
              >
                {t("winery.cellarWord")} {winery.locationNumber}
              </span>
            </h2>
            <ul style={{ listStyle: "none", paddingLeft: 0, margin: 0 }}>
              {wines.map((wine) => (
                <WineShortlistRow key={wine.id} wine={wine} />
              ))}
            </ul>
          </section>
        ))
      )}
    </PageMain>
  );
}
