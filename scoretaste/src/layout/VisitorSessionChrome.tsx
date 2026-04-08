import { useEffect, useState } from "react";
import { NavLink, Outlet, useMatch } from "react-router-dom";
import type { EventCatalog } from "../types";
import { t } from "../i18n";
import type { VisitorSessionOutletContext } from "./visitorSessionContext";

type Props = {
  eventId: string;
  catalog: EventCatalog;
  outletContext: VisitorSessionOutletContext;
};

function visitorLogoUrls(eventId: string) {
  const base = import.meta.env.BASE_URL;
  const primary = `${base}assets/logo_${eventId}.png`;
  const fallback = `${base}assets/logo_def.png`;
  return { primary, fallback };
}

function VisitorEventLogo({ eventId }: { eventId: string }) {
  const { primary, fallback } = visitorLogoUrls(eventId);
  const [src, setSrc] = useState(primary);

  useEffect(() => {
    setSrc(primary);
  }, [primary]);

  return (
    <img
      className="visitor-logo-img"
      src={src}
      alt=""
      width={112}
      height={40}
      decoding="async"
      onError={() => {
        setSrc((current) => (current === fallback ? current : fallback));
      }}
    />
  );
}

export function VisitorSessionChrome({ eventId, catalog, outletContext }: Props) {
  const [infoOpen, setInfoOpen] = useState(false);
  const listMatch = useMatch({
    path: "/e/:eventId/wineries",
    end: true,
  });
  const showFilter = Boolean(listMatch);

  const { wineryFilter, setWineryFilter } = outletContext;
  const eventName = catalog.event.name.trim() || t("guide.title");

  return (
    <div className="visitor-shell">
      <header className="visitor-header">
        <div className="visitor-content-width">
        <div className="visitor-header-row1">
          <div className="visitor-brand">
            <VisitorEventLogo eventId={eventId} />
            <span className="visitor-event-title">{eventName}</span>
          </div>
          <button
            type="button"
            className="visitor-info-btn"
            onClick={() => setInfoOpen(true)}
            aria-haspopup="dialog"
            aria-expanded={infoOpen}
            aria-label={t("visitor.infoAria")}
          >
            ⓘ
          </button>
        </div>

        <nav className="visitor-nav" aria-label={t("visitor.navAria")}>
          <NavLink
            className={({ isActive }) =>
              `visitor-nav-link${isActive ? " visitor-nav-link-active" : ""}`
            }
            to={`/e/${eventId}/wineries`}
            end
          >
            {t("visitor.navWineries")}
          </NavLink>
          <NavLink
            className={({ isActive }) =>
              `visitor-nav-link${isActive ? " visitor-nav-link-active" : ""}`
            }
            to={`/e/${eventId}/my`}
          >
            {t("visitor.navMyWines")}
          </NavLink>
        </nav>

        {showFilter ? (
          <div className="visitor-filter-row">
            <input
              type="search"
              className="visitor-filter-input"
              value={wineryFilter}
              onChange={(e) => setWineryFilter(e.target.value)}
              placeholder={t("visitor.filterPlaceholder")}
              enterKeyHint="search"
              autoComplete="off"
              aria-label={t("visitor.filterAria")}
            />
            <button
              type="button"
              className="visitor-filter-clear"
              onClick={() => setWineryFilter("")}
              disabled={!wineryFilter.trim()}
              aria-label={t("visitor.filterClearAria")}
            >
              ×
            </button>
          </div>
        ) : null}
        </div>
      </header>

      {infoOpen ? (
        <div
          className="visitor-modal-backdrop"
          role="presentation"
          onClick={() => setInfoOpen(false)}
        >
          <div
            className="visitor-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="visitor-info-title"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="visitor-modal-head">
              <h2 id="visitor-info-title" className="visitor-modal-title">
                {t("visitor.infoTitle")}
              </h2>
              <button
                type="button"
                className="visitor-modal-close"
                onClick={() => setInfoOpen(false)}
                aria-label={t("visitor.modalCloseAria")}
              >
                ×
              </button>
            </div>
            <div className="visitor-modal-body">
              <div className="visitor-modal-body-main">{t("visitor.infoBody")}</div>
              <p className="visitor-modal-madeby">
                {t("visitor.infoFooterMadeBy")}{" "}
                <a
                  href="https://scoretaste.cz"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  ScoreTaste.cz
                </a>
              </p>
            </div>
            <button
              type="button"
              className="visitor-modal-ok"
              onClick={() => setInfoOpen(false)}
            >
              {t("visitor.modalOk")}
            </button>
          </div>
        </div>
      ) : null}

      <div className="visitor-body">
        <Outlet context={outletContext} />
      </div>
    </div>
  );
}
