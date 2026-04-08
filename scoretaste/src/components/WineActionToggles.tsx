import type { MouseEvent, ReactNode } from "react";
import { useVisitorActions } from "../context/VisitorActionsContext";
import { t } from "../i18n";

type Props = {
  wineId: string;
  children: ReactNode;
  /** Šipka mezi názvem a košíkem; pouze indikátor (ne samostatný klik). */
  detailChevron?: { open: boolean; visible: boolean };
};

export function WineActionToggles({ wineId, children, detailChevron }: Props) {
  const { getRecord, setLiked, setWantToBuy } = useVisitorActions();
  const r = getRecord(wineId);

  const onBasketClick = (e: MouseEvent<HTMLButtonElement>) => {
    e.stopPropagation();
    if (r.wantToBuy) {
      if (window.confirm(t("wine.confirmRemoveFromBasket"))) {
        setWantToBuy(wineId, false);
      }
      return;
    }
    setWantToBuy(wineId, true);
  };

  return (
    <div className="visitor-wine-actions-row">
      <button
        type="button"
        className="visitor-wine-star"
        onClick={(e) => {
          e.stopPropagation();
          setLiked(wineId, !r.liked);
        }}
        aria-pressed={r.liked}
        aria-label={r.liked ? t("wine.savedAria") : t("wine.saveAria")}
      >
        {r.liked ? "★" : "☆"}
      </button>
      <div className="visitor-wine-title-wrap">{children}</div>
      {detailChevron?.visible ? (
        <span
          className={`visitor-wine-detail-chevron${
            detailChevron.open ? " visitor-wine-detail-chevron-open" : ""
          }`}
          aria-hidden
        >
          ▼
        </span>
      ) : null}
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
