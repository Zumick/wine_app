import { useEffect, useState } from "react";
import { probeEventMapExists } from "../lib/guideAssetsUrls";

/**
 * `null` = ještě neověřeno, `true` / `false` = výsledek HEAD na /guide/assets/mapa_<id>.jpg
 */
export function useEventMapExists(eventId: string | undefined): boolean | null {
  const [exists, setExists] = useState<boolean | null>(null);

  useEffect(() => {
    if (!eventId) {
      setExists(null);
      return;
    }
    let cancelled = false;
    setExists(null);
    void probeEventMapExists(eventId).then((ok) => {
      if (!cancelled) setExists(ok);
    });
    return () => {
      cancelled = true;
    };
  }, [eventId]);

  return eventId ? exists : null;
}
