import { useVisitorActions } from "../context/VisitorActionsContext";
import { t } from "../i18n";

type Props = { wineId: string };

export function WineActionToggles({ wineId }: Props) {
  const { getRecord, toggleLiked, toggleWantToBuy } = useVisitorActions();
  const r = getRecord(wineId);

  return (
    <div
      className="visitor-wine-actions"
      style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", marginTop: "0.5rem" }}
    >
      <button
        type="button"
        onClick={() => toggleWantToBuy(wineId)}
        aria-pressed={r.wantToBuy}
        style={{ padding: "0.35rem 0.6rem", cursor: "pointer" }}
      >
        {r.wantToBuy ? t("wine.wantToBuyActive") : t("wine.wantToBuy")}
      </button>
      <button
        type="button"
        onClick={() => toggleLiked(wineId)}
        aria-pressed={r.liked}
        style={{ padding: "0.35rem 0.6rem", cursor: "pointer" }}
      >
        {r.liked ? t("wine.liked") : t("wine.like")}
      </button>
    </div>
  );
}
