import type { MouseEvent, ReactNode } from "react";
import { useVisitorActions } from "../context/VisitorActionsContext";
import { t } from "../i18n";

type Props = {
  wineId: string;
  children: ReactNode;
  /** Zobrazí stříšku vpravo, pokud řádek má rozbalitelný detail (Moje vína / detail vinařství). */
  expandChevron?: { open: boolean };
  /** Nahradí výchozí cyklování hvězdy (např. Moje vína → odstranění s animací). */
  onStarClick?: (e: MouseEvent<HTMLButtonElement>) => void;
};

function starAriaLabel(level: number): string {
  switch (level) {
    case 2:
      return t("wine.starAriaTop");
    case 1:
      return t("wine.starAriaFavorite");
    default:
      return t("wine.starAriaNone");
  }
}

export function WineActionToggles({
  wineId,
  children,
  expandChevron,
  onStarClick: onStarClickOverride,
}: Props) {
  const { getStarLevel, cycleStarRating } = useVisitorActions();
  const level = getStarLevel(wineId);

  const onStarClick = (e: MouseEvent<HTMLButtonElement>) => {
    e.stopPropagation();
    if (onStarClickOverride) {
      onStarClickOverride(e);
    } else {
      cycleStarRating(wineId);
    }
  };

  return (
    <div className="visitor-wine-actions-row">
      <button
        type="button"
        className={`visitor-wine-star visitor-wine-star--lvl-${level}`}
        onClick={onStarClick}
        aria-label={starAriaLabel(level)}
      >
        {level === 0 ? "☆" : "★"}
      </button>
      <div className="visitor-wine-title-wrap">{children}</div>
      {expandChevron ? (
        <span
          className={`visitor-wine-detail-chevron${expandChevron.open ? " visitor-wine-detail-chevron-open" : ""}`}
          aria-hidden
        >
          ▼
        </span>
      ) : null}
    </div>
  );
}
