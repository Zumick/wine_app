import type { ReactNode } from "react";
import { useVisitorActions } from "../context/VisitorActionsContext";
import { t } from "../i18n";

type Props = { wineId: string; children: ReactNode };

export function WineActionToggles({ wineId, children }: Props) {
  const { getRecord, toggleLiked, toggleWantToBuy } = useVisitorActions();
  const r = getRecord(wineId);

  const onBasketClick = () => {
    if (r.wantToBuy) {
      if (window.confirm(t("wine.confirmRemoveFromBasket"))) {
        toggleWantToBuy(wineId);
      }
      return;
    }
    toggleWantToBuy(wineId);
  };

  return (
    <div className="visitor-wine-actions-row">
      <button
        type="button"
        className="visitor-wine-star"
        onClick={() => toggleLiked(wineId)}
        aria-pressed={r.liked}
        aria-label={r.liked ? t("wine.savedAria") : t("wine.saveAria")}
      >
        {r.liked ? "★" : "☆"}
      </button>
      <div className="visitor-wine-title-wrap">{children}</div>
      <button
        type="button"
        className="visitor-wine-basket"
        onClick={onBasketClick}
        aria-pressed={r.wantToBuy}
        aria-label={
          r.wantToBuy ? t("wine.wantToBuyActiveAria") : t("wine.wantToBuyAria")
        }
      >
        🛒
      </button>
    </div>
  );
}
