import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { TransformComponent, TransformWrapper } from "react-zoom-pan-pinch";
import { guideEventMapUrl } from "../lib/guideAssetsUrls";
import { t } from "../i18n";
import type { EventCatalog, MapHotspot } from "../types";

type Props = {
  eventId: string;
  catalog: EventCatalog;
};

function markerLabel(h: MapHotspot): string {
  const c = h.cellarNumber.trim();
  if (c) return c.length <= 3 ? c : c.slice(0, 3);
  return h.wineryId.slice(0, 3);
}

export function EventWineryMapView({ eventId, catalog }: Props) {
  const navigate = useNavigate();
  const mapSrc = guideEventMapUrl(eventId);
  const [mapBroken, setMapBroken] = useState(false);

  useEffect(() => {
    setMapBroken(false);
  }, [eventId]);

  const wineryIds = useMemo(
    () => new Set(catalog.wineries.map((w) => w.id)),
    [catalog.wineries],
  );

  const hotspots = useMemo(() => {
    const raw = catalog.mapHotspots ?? [];
    return raw.filter((h) => wineryIds.has(h.wineryId));
  }, [catalog.mapHotspots, wineryIds]);

  if (mapBroken) {
    return null;
  }

  return (
    <div className="visitor-map-zoom-root">
      <TransformWrapper
        initialScale={1}
        minScale={0.85}
        maxScale={5}
        centerOnInit
        limitToBounds
        wheel={{ wheelDisabled: true }}
        doubleClick={{ mode: "zoomIn", step: 0.7 }}
        panning={{ excluded: ["visitor-map-marker"] }}
        pinch={{ step: 5 }}
      >
        {({ state }) => {
          const markerScale = 1 / Math.max(state.scale || 1, 0.0001);
          return (
            <TransformComponent
              wrapperClass="visitor-map-transform-wrapper"
              contentClass="visitor-map-transform-content"
              wrapperStyle={{
                width: "100%",
                maxHeight: "min(72vh, 640px)",
                borderRadius: "var(--radius-sm)",
                overflow: "hidden",
                touchAction: "none",
              }}
              contentStyle={{ width: "100%" }}
            >
              <div className="visitor-map-layer">
                <img
                  className="visitor-map-img"
                  src={mapSrc}
                  alt={t("winery.mapImageAlt")}
                  decoding="async"
                  draggable={false}
                  onError={() => setMapBroken(true)}
                />
                {hotspots.map((h) => (
                  <button
                    key={h.wineryId}
                    type="button"
                    className="visitor-map-marker"
                    style={{
                      left: `${h.xPercent}%`,
                      top: `${h.yPercent}%`,
                      transform: `translate(-50%, -50%) scale(${markerScale})`,
                    }}
                    aria-label={t("winery.mapMarkerAria")}
                    onClick={() => navigate(h.wineryId)}
                  >
                    <span className="visitor-map-marker-dot">{markerLabel(h)}</span>
                  </button>
                ))}
              </div>
            </TransformComponent>
          );
        }}
      </TransformWrapper>
    </div>
  );
}
