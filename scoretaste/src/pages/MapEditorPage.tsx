import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { fetchEventCatalog } from "../lib/eventLoader";
import { guideEventMapUrl } from "../lib/guideAssetsUrls";
import type { MapHotspot, Winery } from "../types";

function sortWineriesForEditor(ws: Winery[]): Winery[] {
  return [...ws].sort((a, b) => {
    const la = a.locationNumber.trim();
    const lb = b.locationNumber.trim();
    const oa = la ? 0 : 1;
    const ob = lb ? 0 : 1;
    if (oa !== ob) return oa - ob;
    if (la !== lb) return la.localeCompare(lb, "cs", { numeric: true });
    return a.name.localeCompare(b.name, "cs");
  });
}

function fmtPct(n: number): string {
  return (Math.round(n * 10) / 10).toFixed(1);
}

export function MapEditorPage() {
  const { eventId } = useParams<{ eventId: string }>();
  const [loadState, setLoadState] = useState<
    "loading" | "error" | "ok"
  >("loading");
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [eventTitle, setEventTitle] = useState("");
  const [wineries, setWineries] = useState<Winery[]>([]);
  const [hotspotsByWinery, setHotspotsByWinery] = useState<
    Record<string, MapHotspot>
  >({});
  const [selectedWineryId, setSelectedWineryId] = useState<string | null>(null);
  const [mapOk, setMapOk] = useState(true);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "ok" | "err">(
    "idle",
  );
  const [hint, setHint] = useState<string | null>(null);
  const [mapDisplaySize, setMapDisplaySize] = useState({ w: 0, h: 0 });
  const mapImgRef = useRef<HTMLImageElement | null>(null);

  const mapSrc = eventId ? guideEventMapUrl(eventId) : undefined;

  const reload = useCallback(() => {
    if (!eventId) return;
    setLoadState("loading");
    setLoadErr(null);
    fetchEventCatalog(eventId)
      .then((cat) => {
        setEventTitle(cat.event.name.trim() || `Akce ${eventId}`);
        setWineries(sortWineriesForEditor(cat.wineries));
        const next: Record<string, MapHotspot> = {};
        for (const h of cat.mapHotspots ?? []) {
          next[h.wineryId] = h;
        }
        setHotspotsByWinery(next);
        setLoadState("ok");
      })
      .catch((e: unknown) => {
        setLoadState("error");
        setLoadErr(e instanceof Error ? e.message : "LOAD_FAILED");
      });
  }, [eventId]);

  useEffect(() => {
    reload();
  }, [reload]);

  useEffect(() => {
    setMapOk(true);
  }, [eventId, mapSrc]);

  useEffect(() => {
    const el = mapImgRef.current;
    if (!el || !mapSrc || !mapOk) return;
    const sync = () =>
      setMapDisplaySize({ w: el.clientWidth, h: el.clientHeight });
    sync();
    const ro = new ResizeObserver(sync);
    ro.observe(el);
    return () => ro.disconnect();
  }, [mapSrc, mapOk]);

  const selectedWinery = useMemo(
    () => wineries.find((w) => w.id === selectedWineryId) ?? null,
    [wineries, selectedWineryId],
  );

  const selectedHotspot = selectedWineryId
    ? hotspotsByWinery[selectedWineryId]
    : undefined;

  const selectedPixelsPx =
    selectedHotspot && mapDisplaySize.w > 0 && mapDisplaySize.h > 0
      ? {
          x: Math.round((selectedHotspot.xPercent / 100) * mapDisplaySize.w),
          y: Math.round((selectedHotspot.yPercent / 100) * mapDisplaySize.h),
        }
      : null;

  const onMapClick = (e: React.MouseEvent<HTMLImageElement>) => {
    setHint(null);
    setSaveState("idle");
    if (!selectedWineryId || !selectedWinery) {
      setHint("Nejprve vyberte vinařství / sklep v seznamu.");
      return;
    }
    const img = e.currentTarget;
    const rect = img.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    const w = rect.width;
    const h = rect.height;
    if (w <= 0 || h <= 0) return;
    const xPercent = (x / w) * 100;
    const yPercent = (y / h) * 100;
    const cellar = selectedWinery.locationNumber.trim();
    setHotspotsByWinery((prev) => ({
      ...prev,
      [selectedWineryId]: {
        wineryId: selectedWineryId,
        cellarNumber: cellar,
        xPercent,
        yPercent,
      },
    }));
  };

  const onMarkerClick = (e: React.MouseEvent, wid: string) => {
    e.stopPropagation();
    setSelectedWineryId(wid);
    setHint(null);
    setSaveState("idle");
  };

  const save = async () => {
    if (!eventId) return;
    const hotspots = Object.values(hotspotsByWinery);
    if (hotspots.length === 0) {
      setHint("Umístěte alespoň jeden bod, nebo uložení není potřeba.");
      return;
    }
    setHint(null);
    setSaveState("saving");
    try {
      const res = await fetch(
        `/guide/data/events/${encodeURIComponent(eventId)}/map-hotspots`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ hotspots }),
        },
      );
      const data = (await res.json().catch(() => ({}))) as {
        ok?: boolean;
      };
      if (!res.ok || !data.ok) {
        setSaveState("err");
        setHint("Uložení se nezdařilo.");
        return;
      }
      setSaveState("ok");
      reload();
    } catch {
      setSaveState("err");
      setHint("Uložení se nezdařilo.");
    }
  };

  if (!eventId) {
    return (
      <main style={{ padding: 24 }}>
        <p>Chybí ID akce.</p>
      </main>
    );
  }

  const adminHref = `/guide/admin/${encodeURIComponent(eventId)}`;

  if (loadState === "loading") {
    return (
      <main style={{ padding: 24 }}>
        <p>Načítání…</p>
      </main>
    );
  }

  if (loadState === "error") {
    return (
      <main style={{ padding: 24 }}>
        <p>Chyba načtení: {loadErr}</p>
        <p>
          <a href={adminHref}>Zpět na správu akce</a>
        </p>
      </main>
    );
  }

  const mapMissing = !mapSrc ? true : !mapOk;
  const hotspotCount = Object.keys(hotspotsByWinery).length;

  return (
    <main style={{ padding: 16, maxWidth: 1100, margin: "0 auto" }}>
      <p style={{ margin: "0 0 12px" }}>
        <a href={adminHref}>← Zpět na správu akce</a>
      </p>
      <h1 style={{ fontSize: "1.25rem", margin: "0 0 8px" }}>
        Editace mapy — {eventTitle}
      </h1>
      <p style={{ margin: "0 0 16px", color: "#444", fontSize: 14 }}>
        Vyberte vinařství, poté klikněte na mapu pro umístění bodu. Uložením
        zapíšete všechny body najednou.
      </p>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "minmax(200px, 280px) 1fr",
          gap: 16,
          alignItems: "start",
        }}
      >
        <div>
          <label
            htmlFor="map-editor-winery"
            style={{ fontWeight: 600, display: "block", marginBottom: 6 }}
          >
            Vinařství / sklep
          </label>
          <select
            id="map-editor-winery"
            size={12}
            style={{
              width: "100%",
              minHeight: 220,
              fontSize: 13,
              padding: 6,
            }}
            value={selectedWineryId ?? ""}
            onChange={(e) => {
              setSelectedWineryId(e.target.value || null);
              setHint(null);
              setSaveState("idle");
            }}
          >
            <option value="">— vyberte —</option>
            {wineries.map((w) => {
              const loc = w.locationNumber.trim();
              const label = loc
                ? `#${loc} — ${w.name} (id ${w.id})`
                : `${w.name} (id ${w.id})`;
              return (
                <option key={w.id} value={w.id}>
                  {label}
                </option>
              );
            })}
          </select>
        </div>

        <div>
          {mapMissing ? (
            <div
              style={{
                border: "1px solid #ccc",
                padding: 24,
                background: "#fafafa",
                borderRadius: 6,
              }}
            >
              Mapa pro tuto akci nebyla nalezena
            </div>
          ) : (
            <div
              style={{
                position: "relative",
                display: "inline-block",
                maxWidth: "100%",
                border: "1px solid #ccc",
                lineHeight: 0,
              }}
            >
              {/* eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions */}
              <img
                ref={mapImgRef}
                src={mapSrc}
                alt="Mapa akce"
                style={{
                  maxWidth: "100%",
                  height: "auto",
                  display: "block",
                  cursor: selectedWineryId ? "crosshair" : "default",
                }}
                onClick={onMapClick}
                onError={() => setMapOk(false)}
                onLoad={(ev) => {
                  setMapOk(true);
                  const el = ev.currentTarget;
                  setMapDisplaySize({ w: el.clientWidth, h: el.clientHeight });
                }}
              />
              {Object.values(hotspotsByWinery).map((h) => {
                const isSel = h.wineryId === selectedWineryId;
                const label =
                  h.cellarNumber.trim() ||
                  h.wineryId.slice(0, 4);
                return (
                  <button
                    key={h.wineryId}
                    type="button"
                    title={`Sklep ${h.cellarNumber || "—"} · id ${h.wineryId}`}
                    onClick={(ev) => onMarkerClick(ev, h.wineryId)}
                    style={{
                      position: "absolute",
                      left: `${h.xPercent}%`,
                      top: `${h.yPercent}%`,
                      transform: "translate(-50%, -50%)",
                      width: 28,
                      height: 28,
                      borderRadius: "50%",
                      border: isSel
                        ? "3px solid #1d4ed8"
                        : "2px solid #111",
                      background: isSel ? "#dbeafe" : "#fff",
                      color: "#111",
                      fontSize: 11,
                      fontWeight: 700,
                      cursor: "pointer",
                      padding: 0,
                      lineHeight: "24px",
                    }}
                  >
                    {label.slice(0, 3)}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </div>

      <div
        style={{
          marginTop: 20,
          padding: 12,
          border: "1px solid #ddd",
          borderRadius: 6,
          background: "#fff",
          maxWidth: 480,
        }}
      >
        <h2 style={{ fontSize: "1rem", margin: "0 0 8px" }}>Souřadnice</h2>
        {selectedWinery ? (
          <dl
            style={{
              margin: 0,
              display: "grid",
              gridTemplateColumns: "auto 1fr",
              gap: "4px 12px",
              fontSize: 14,
            }}
          >
            <dt style={{ color: "#666" }}>Vinařství</dt>
            <dd style={{ margin: 0 }}>{selectedWinery.name}</dd>
            <dt style={{ color: "#666" }}>Číslo sklepu</dt>
            <dd style={{ margin: 0 }}>
              {selectedWinery.locationNumber.trim() || "—"}
            </dd>
            <dt style={{ color: "#666" }}>wineryId</dt>
            <dd style={{ margin: 0 }}>{selectedWinery.id}</dd>
            <dt style={{ color: "#666" }}>xPercent</dt>
            <dd style={{ margin: 0 }}>
              {selectedHotspot ? fmtPct(selectedHotspot.xPercent) : "—"}
            </dd>
            <dt style={{ color: "#666" }}>yPercent</dt>
            <dd style={{ margin: 0 }}>
              {selectedHotspot ? fmtPct(selectedHotspot.yPercent) : "—"}
            </dd>
            <dt style={{ color: "#666" }}>x px / y px (náhled)</dt>
            <dd style={{ margin: 0 }}>
              {selectedPixelsPx
                ? `${selectedPixelsPx.x} / ${selectedPixelsPx.y}`
                : "—"}
            </dd>
          </dl>
        ) : (
          <p style={{ margin: 0, color: "#666" }}>Není vybráno vinařství.</p>
        )}
      </div>

      {hint ? (
        <p style={{ color: "#b45309", marginTop: 12 }}>{hint}</p>
      ) : null}
      {saveState === "ok" ? (
        <p style={{ color: "#166534", marginTop: 8 }}>Uloženo.</p>
      ) : null}

      <div style={{ marginTop: 16 }}>
        <button
          type="button"
          className="btn"
          style={{
            padding: "8px 16px",
            fontSize: 14,
            cursor: saveState === "saving" ? "wait" : "pointer",
          }}
          disabled={saveState === "saving" || hotspotCount === 0}
          onClick={() => void save()}
        >
          {saveState === "saving" ? "Ukládám…" : "Uložit pozice na mapě"}
        </button>
      </div>
    </main>
  );
}
