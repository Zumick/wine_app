import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  fetchSavedSelection,
  PENDING_SAVED_SELECTION_KEY,
} from "../lib/saveSelectionApi";

export function SavedSelectionOpenPage() {
  const { token } = useParams<{ token: string }>();
  const navigate = useNavigate();
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!token || !token.trim()) {
      setErr("Chybí odkaz.");
      return;
    }
    let cancelled = false;
    (async () => {
      const data = await fetchSavedSelection(token.trim());
      if (cancelled) return;
      if (!data.ok) {
        setErr("Uložený výběr se nepodařilo najít nebo už není platný.");
        return;
      }
      try {
        sessionStorage.setItem(
          PENDING_SAVED_SELECTION_KEY,
          JSON.stringify({
            eventId: data.event_id,
            epochId: data.epoch_id,
            wines: data.wines,
          }),
        );
      } catch {
        setErr("Nelze uložit výběr do prohlížeče.");
        return;
      }
      navigate(`/e/${encodeURIComponent(data.event_id)}/my`, { replace: true });
    })();
    return () => {
      cancelled = true;
    };
  }, [token, navigate]);

  if (err) {
    return (
      <main className="visitor-saved-open visitor-saved-open--err">
        <p>{err}</p>
        <p>
          <Link to="/">Zpět</Link>
        </p>
      </main>
    );
  }

  return (
    <main className="visitor-saved-open">
      <p>Načítám váš výběr…</p>
    </main>
  );
}
