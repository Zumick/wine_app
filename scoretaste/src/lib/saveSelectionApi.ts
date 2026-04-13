export const PENDING_SAVED_SELECTION_KEY = "scoretaste:pendingSavedSelection";
export const SHOW_RESTORED_TOAST_KEY = "scoretaste:showRestoredToast";

export type SavedWinesPayload = Record<
  string,
  { liked: boolean; wantToBuy: boolean }
>;

export async function postSaveSelection(body: {
  event_id: string;
  epoch_id: number | null;
  wines: SavedWinesPayload;
}): Promise<{ ok: true; share_url: string; token: string } | { ok: false }> {
  const res = await fetch("/guide/api/save-selection", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      event_id: body.event_id,
      epoch_id: body.epoch_id,
      wines: body.wines,
    }),
  });
  if (!res.ok) return { ok: false };
  const data = (await res.json()) as {
    ok?: boolean;
    share_url?: string;
    token?: string;
  };
  if (!data.ok || !data.share_url) return { ok: false };
  return { ok: true, share_url: data.share_url, token: data.token ?? "" };
}

export async function fetchSavedSelection(token: string): Promise<
  | {
      ok: true;
      event_id: string;
      epoch_id: number | null;
      wines: SavedWinesPayload;
    }
  | { ok: false }
> {
  const res = await fetch(
    `/guide/api/saved-selection/${encodeURIComponent(token)}`,
    { cache: "no-store" },
  );
  if (!res.ok) return { ok: false };
  const data = (await res.json()) as {
    ok?: boolean;
    event_id?: string;
    epoch_id?: number | null;
    wines?: SavedWinesPayload;
  };
  if (!data.ok || !data.event_id || !data.wines) return { ok: false };
  return {
    ok: true,
    event_id: data.event_id,
    epoch_id:
      data.epoch_id === undefined || data.epoch_id === null
        ? null
        : Number(data.epoch_id),
    wines: data.wines,
  };
}
