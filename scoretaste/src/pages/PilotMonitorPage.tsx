import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

type MonitorEvent = {
  id: string;
  title: string;
  mode: "live" | "preparation";
  startedAt: string | null;
  startedDisplay: string | null;
  epochNumber: number | null;
};

type MonitorKpis = {
  activeDevices: number;
  devicesWithSelection: number;
  totalFavorites: number;
  totalTop: number;
  qualifiedReturnRate?: {
    value: number | null;
    numerator: number;
    denominator: number;
  };
  readyToBuyRate?: {
    value: number | null;
    numerator: number;
    denominator: number;
  };
};

type MonitorActivityRow = {
  time: string;
  deviceShort: string;
  actionType: string;
  wineLabel: string;
  cellar: string | null;
};

type MonitorErrors = {
  message: string;
  epochMismatchCount: number | null;
  syncErrorCount: number | null;
};

type MonitorPayload = {
  ok: boolean;
  event: MonitorEvent;
  kpis: MonitorKpis;
  recentActivity: MonitorActivityRow[];
  errors: MonitorErrors;
};

function fmtTime(iso: string): string {
  const s = iso.trim();
  if (!s) return "—";
  try {
    let t = s;
    if (t.endsWith("Z")) t = t.slice(0, -1) + "+00:00";
    const d = new Date(t);
    if (Number.isNaN(d.getTime())) {
      const m = s.match(/T(\d{2}):(\d{2})/);
      return m ? `${m[1]}:${m[2]}` : s.slice(0, 16);
    }
    return d.toLocaleTimeString("cs-CZ", {
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return s;
  }
}

function modeLabel(mode: MonitorEvent["mode"]): string {
  return mode === "live" ? "Ostrý sběr aktivní" : "Příprava";
}

function fmtRate(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${Math.round(value)} %`;
}

export function PilotMonitorPage() {
  const { eventId } = useParams<{ eventId: string }>();
  const [state, setState] = useState<
    "loading" | "error" | "ok" | "disabled"
  >("loading");
  const [refreshing, setRefreshing] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [data, setData] = useState<MonitorPayload | null>(null);
  const dataRef = useRef<MonitorPayload | null>(null);
  useEffect(() => {
    dataRef.current = data;
  }, [data]);

  const load = useCallback(() => {
    if (!eventId) return;
    setErr(null);
    const hadData = dataRef.current != null;
    if (hadData) setRefreshing(true);
    else setState("loading");

    const url = `/guide/data/events/${encodeURIComponent(eventId)}/pilot-monitor.json`;
    fetch(url, { cache: "no-store" })
      .then((res) => {
        if (res.status === 404) {
          setState("disabled");
          setData(null);
          setRefreshing(false);
          return;
        }
        if (!res.ok) {
          throw new Error(`HTTP ${res.status}`);
        }
        return res.json() as Promise<MonitorPayload>;
      })
      .then((json) => {
        if (!json) return;
        if (!json.ok || !json.event || !json.kpis) {
          throw new Error("INVALID");
        }
        setData(json);
        setState("ok");
        setRefreshing(false);
      })
      .catch((e: unknown) => {
        setRefreshing(false);
        const msg = e instanceof Error ? e.message : "LOAD_FAILED";
        if (!hadData) {
          setState("error");
          setErr(msg);
        } else {
          setState("ok");
          setErr(msg);
        }
      });
  }, [eventId]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (state !== "ok" || !eventId) return;
    const t = window.setInterval(load, 22_000);
    return () => window.clearInterval(t);
  }, [state, load, eventId]);

  if (!eventId) {
    return (
      <main className="pilot-monitor">
        <p className="pilot-monitor-muted">Chybí ID akce.</p>
      </main>
    );
  }

  return (
    <main className="pilot-monitor">
      <div className="pilot-monitor-top">
        <Link to={`/e/${eventId}/wineries`} className="pilot-monitor-back">
          ← Akce
        </Link>
        <button
          type="button"
          className="pilot-monitor-refresh"
          onClick={() => load()}
          disabled={state === "loading" || refreshing}
        >
          {state === "loading" || refreshing ? "…" : "Obnovit"}
        </button>
      </div>

      {state === "error" || (state === "ok" && err) ? (
        <p className="pilot-monitor-err">{err ?? "Chyba načtení."}</p>
      ) : null}

      {state === "disabled" ? (
        <p className="pilot-monitor-muted">
          Pilot monitor není na tomto serveru zapnutý (API), nebo akce neexistuje.
        </p>
      ) : null}

      {state === "ok" && data ? (
        <>
          <section className="pilot-monitor-card pilot-monitor-status">
            <div className="pilot-monitor-status-title">{data.event.title}</div>
            <div className="pilot-monitor-mode">{modeLabel(data.event.mode)}</div>
            {data.event.mode === "live" && data.event.startedDisplay ? (
              <div className="pilot-monitor-meta">
                Zahájeno: {data.event.startedDisplay}
              </div>
            ) : null}
            {data.event.mode === "live" &&
            data.event.epochNumber != null &&
            data.event.epochNumber > 0 ? (
              <div className="pilot-monitor-meta">
                Běh č. {data.event.epochNumber}
              </div>
            ) : null}
          </section>

          <section className="pilot-monitor-section" aria-label="KPI">
            <div className="pilot-monitor-kpi-head">
              <h2 className="pilot-monitor-h2">KPI</h2>
              <button
                type="button"
                className="pilot-monitor-kpi-info-btn"
                onClick={() => setHelpOpen((v) => !v)}
                aria-expanded={helpOpen}
                aria-controls="pilot-monitor-kpi-help"
              >
                info
              </button>
            </div>

            {helpOpen ? (
              <div id="pilot-monitor-kpi-help" className="pilot-monitor-kpi-help">
                <p>
                  <strong>Vytvořený výběr pro nákup</strong>: Podíl návštěvníků, kteří
                  si označili alespoň 1 TOP víno a zároveň mají alespoň 3 označená vína.
                  Ukazuje, kolik lidí si vytvořilo užší výběr pro nákup.
                </p>
                <p>
                  <strong>Kvalifikovaný návrat</strong>: Podíl aktivních návštěvníků,
                  kteří se po označování vín vrátili do seznamu Moje vína. Počítají se
                  jen uživatelé s alespoň 2 akcemi označení.
                </p>
                <p>
                  <strong>Aktivní zařízení</strong>: Počet anonymních zařízení, která v
                  aktuálním běhu provedla aktivitu.
                </p>
                <p>
                  <strong>Zařízení s označením</strong>: Počet zařízení, která si v
                  aktuálním běhu označila alespoň jedno víno.
                </p>
                <p>
                  <strong>Oblíbené</strong>: Celkový počet vín označených jako oblíbené v
                  aktuálním běhu.
                </p>
                <p>
                  <strong>TOP</strong>: Celkový počet vín označených jako TOP v aktuálním
                  běhu.
                </p>
              </div>
            ) : null}

            <div className="pilot-monitor-kpis">
              <div className="pilot-monitor-kpi pilot-monitor-kpi-main">
                <span className="pilot-monitor-kpi-main-val">
                  {fmtRate(data.kpis.readyToBuyRate?.value)}
                </span>
                <span className="pilot-monitor-kpi-main-lbl">
                  Vytvořený výběr pro nákup
                </span>
                <span className="pilot-monitor-kpi-main-sub">
                  {data.kpis.readyToBuyRate
                    ? `${data.kpis.readyToBuyRate.numerator} z ${data.kpis.readyToBuyRate.denominator}`
                    : "—"}
                </span>
              </div>
              <div className="pilot-monitor-kpi">
                <span className="pilot-monitor-kpi-val">
                  {fmtRate(data.kpis.qualifiedReturnRate?.value)}
                </span>
                <span className="pilot-monitor-kpi-lbl">Kvalifikovaný návrat</span>
                <span className="pilot-monitor-kpi-sub">
                  {data.kpis.qualifiedReturnRate
                    ? `${data.kpis.qualifiedReturnRate.numerator} z ${data.kpis.qualifiedReturnRate.denominator}`
                    : "—"}
                </span>
              </div>
              <div className="pilot-monitor-kpi">
                <span className="pilot-monitor-kpi-val">{data.kpis.activeDevices}</span>
                <span className="pilot-monitor-kpi-lbl">Aktivní zařízení</span>
              </div>
              <div className="pilot-monitor-kpi">
                <span className="pilot-monitor-kpi-val">
                  {data.kpis.devicesWithSelection}
                </span>
                <span className="pilot-monitor-kpi-lbl">Zařízení s označením</span>
              </div>
              <div className="pilot-monitor-kpi">
                <span className="pilot-monitor-kpi-val">
                  {data.kpis.totalFavorites}
                </span>
                <span className="pilot-monitor-kpi-lbl">Oblíbené</span>
              </div>
              <div className="pilot-monitor-kpi">
                <span className="pilot-monitor-kpi-val">{data.kpis.totalTop}</span>
                <span className="pilot-monitor-kpi-lbl">TOP</span>
              </div>
            </div>
          </section>

          <section className="pilot-monitor-section">
            <h2 className="pilot-monitor-h2">Poslední aktivita</h2>
            <ul className="pilot-monitor-log">
              {data.recentActivity.length === 0 ? (
                <li className="pilot-monitor-muted">Zatím žádné záznamy v aktivní epoše.</li>
              ) : (
                data.recentActivity.map((row, i) => (
                  <li key={`${row.time}-${row.deviceShort}-${i}`} className="pilot-monitor-log-row">
                    <span className="pilot-monitor-log-t">{fmtTime(row.time)}</span>
                    <span className="pilot-monitor-log-d">{row.deviceShort}</span>
                    <span className="pilot-monitor-log-a">{row.actionType}</span>
                    <span className="pilot-monitor-log-w">
                      {row.wineLabel}
                      {row.cellar ? ` · sklep ${row.cellar}` : ""}
                    </span>
                  </li>
                ))
              )}
            </ul>
          </section>

          <section className="pilot-monitor-section pilot-monitor-errors">
            <h2 className="pilot-monitor-h2">Provozní upozornění</h2>
            <p className="pilot-monitor-err-msg">{data.errors.message}</p>
            {data.errors.epochMismatchCount != null ||
            data.errors.syncErrorCount != null ? (
              <p className="pilot-monitor-meta">
                {data.errors.epochMismatchCount != null
                  ? `Nesoulad epoch: ${data.errors.epochMismatchCount}. `
                  : ""}
                {data.errors.syncErrorCount != null
                  ? `Chyby synchronizace: ${data.errors.syncErrorCount}.`
                  : ""}
              </p>
            ) : null}
          </section>
        </>
      ) : null}

      {state === "loading" && !data ? (
        <p className="pilot-monitor-muted">Načítání…</p>
      ) : null}

      {refreshing && data ? (
        <p className="pilot-monitor-muted pilot-monitor-refresh-hint">Obnovuji…</p>
      ) : null}
    </main>
  );
}
