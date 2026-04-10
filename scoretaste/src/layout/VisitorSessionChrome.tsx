import { useCallback, useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useMatch } from "react-router-dom";
import type { EventCatalog } from "../types";
import { t } from "../i18n";
import type { VisitorSessionOutletContext } from "./visitorSessionContext";
import madeByLogo from "../assets/ScorTaste_cz_logo_info.png";

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
  const [infoHighlighted, setInfoHighlighted] = useState(false);
  const infoHighlightTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const { wineryFilter, setWineryFilter, wineryBrowseView } = outletContext;
  const listMatch = useMatch({
    path: "/e/:eventId/wineries",
    end: true,
  });
  const showFilter = Boolean(listMatch) && wineryBrowseView === "list";
  const eventName = catalog.event.name.trim() || t("guide.title");
  const showPilotMonitor =
    import.meta.env.VITE_PILOT_MONITOR === "true" ||
    import.meta.env.VITE_PILOT_MONITOR === "1";

  const triggerInfoHighlight = useCallback(() => {
    setInfoHighlighted(true);
    if (infoHighlightTimerRef.current !== null) {
      clearTimeout(infoHighlightTimerRef.current);
    }
    infoHighlightTimerRef.current = setTimeout(() => {
      setInfoHighlighted(false);
      infoHighlightTimerRef.current = null;
    }, 3000);
  }, []);

  const dismissInfoModal = useCallback(() => {
    setInfoOpen(false);
    try {
      localStorage.setItem(`guide_info_seen_event_${eventId}`, "1");
    } catch {
      /* ignore storage errors */
    }
    triggerInfoHighlight();
  }, [eventId, triggerInfoHighlight]);

  useEffect(() => {
    let seen = false;
    try {
      seen = localStorage.getItem(`guide_info_seen_event_${eventId}`) === "1";
    } catch {
      seen = false;
    }
    if (!seen) {
      setInfoOpen(true);
    }
  }, [eventId]);

  useEffect(() => {
    return () => {
      if (infoHighlightTimerRef.current !== null) {
        clearTimeout(infoHighlightTimerRef.current);
      }
    };
  }, []);

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
            className={`visitor-info-btn${infoHighlighted ? " visitor-info-btn-highlight" : ""}`}
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
          onClick={dismissInfoModal}
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
                Průvodce degustací
              </h2>
              <button
                type="button"
                className="visitor-modal-close"
                onClick={dismissInfoModal}
                aria-label={t("visitor.modalCloseAria")}
              >
                ×
              </button>
            </div>
            <div className="visitor-modal-body">
              <div className="visitor-modal-body-main">
                <p className="visitor-info-flow" aria-label="Postup použití průvodce">
                  Vyber vinařství → vyber víno → lajkuj
                </p>
                <ul className="visitor-info-list" aria-label="Vysvětlení symbolů">
                  <li>
                    <span className="visitor-info-symbol" aria-hidden={true}>
                      ✓
                    </span>
                    navštívený sklep, automaticky po lajku
                  </li>
                  <li>
                    <span className="visitor-info-symbol visitor-info-symbol-star" aria-hidden={true}>
                      ★
                    </span>
                    oblíbené víno
                  </li>
                  <li>
                    <span className="visitor-info-symbol visitor-info-symbol-top" aria-hidden={true}>
                      ★
                    </span>
                    označení top vína - kupuju
                  </li>
                </ul>
                <ul className="visitor-info-list" aria-label="Sdílení seznamu vín">
                  <li>
                    <span className="visitor-info-symbol" aria-hidden={true}>
                      ↗
                    </span>
                    Seznam vín můžete sdílet.
                  </li>
                </ul>
                <p className="visitor-info-p">V Moje vína je seznam označených vzorků</p>
                <ul className="visitor-info-list" aria-label="Vysvětlení symbolů v Moje vína">
                  <li>
                    <span className="visitor-info-symbol" aria-hidden={true}>
                      ▾
                    </span>
                    zobrazí detail vína
                  </li>
                  <li>
                    <span className="visitor-info-symbol" aria-hidden={true}>
                      ×
                    </span>
                    odstranění z moje vína
                  </li>
                </ul>
                <p className="visitor-info-p">
                  Vaše volby se ukládají anonymně pro tuto akci.
                </p>
              </div>
              <p className="visitor-modal-madeby">
                <a
                  href="https://scoretaste.cz"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <img
                    src={madeByLogo}
                    alt="ScoreTaste.cz"
                    className="visitor-madeby-logo"
                    width={128}
                    height={28}
                    decoding="async"
                  />
                </a>
              </p>
            </div>
            <button
              type="button"
              className="visitor-modal-ok"
              onClick={dismissInfoModal}
            >
              {t("visitor.modalOk")}
            </button>
          </div>
        </div>
      ) : null}

      <div className="visitor-body">
        <Outlet context={outletContext} />
      </div>

      {showPilotMonitor ? (
        <div className="visitor-pilot-foot">
          <Link to={`/e/${eventId}/monitor`} className="visitor-pilot-link">
            Pilot monitor
          </Link>
        </div>
      ) : null}
    </div>
  );
}
