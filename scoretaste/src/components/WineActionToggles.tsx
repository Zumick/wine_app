import type { MouseEvent, ReactNode } from "react";
import { useEffect, useRef } from "react";
import { useVisitorActions } from "../context/VisitorActionsContext";
import { t } from "../i18n";

const STAR_LONG_PRESS_MS = 450;

type Props = {
  wineId: string;
  children: ReactNode;
  /** Zobrazí stříšku vpravo, pokud řádek má rozbalitelný detail (Moje vína / detail vinařství). */
  expandChevron?: { open: boolean };
  /** Nahradí výchozí cyklování hvězdy (např. Moje vína → odstranění s animací). */
  onStarClick?: (e: MouseEvent<HTMLButtonElement>) => void;
  /** Volitelná akce při podržení hvězdy (např. odstranění v Moje vína). */
  onStarLongPress?: () => void;
  /** Volitelně přepíše ARIA popis hvězdy v konkrétním kontextu. */
  starAriaLabel?: string;
};

function defaultStarAriaLabel(level: number): string {
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
  onStarLongPress,
  starAriaLabel: starAriaLabelOverride,
}: Props) {
  const { getStarLevel, cycleStarRating } = useVisitorActions();
  const level = getStarLevel(wineId);
  const longPressTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const longPressTriggeredRef = useRef(false);

  const onStarClick = (e: MouseEvent<HTMLButtonElement>) => {
    e.stopPropagation();
    if (longPressTriggeredRef.current) {
      longPressTriggeredRef.current = false;
      return;
    }
    if (onStarClickOverride) {
      onStarClickOverride(e);
    } else {
      cycleStarRating(wineId);
    }
  };

  const clearLongPressTimer = () => {
    if (longPressTimerRef.current !== null) {
      clearTimeout(longPressTimerRef.current);
      longPressTimerRef.current = null;
    }
  };

  const handleStarPointerDown = () => {
    longPressTriggeredRef.current = false;
    if (!onStarLongPress) return;
    clearLongPressTimer();
    longPressTimerRef.current = setTimeout(() => {
      longPressTimerRef.current = null;
      longPressTriggeredRef.current = true;
      onStarLongPress();
    }, STAR_LONG_PRESS_MS);
  };

  const handleStarPointerUp = () => {
    clearLongPressTimer();
  };

  useEffect(() => () => clearLongPressTimer(), []);

  return (
    <div className="visitor-wine-actions-row">
      <button
        type="button"
        className={`visitor-wine-star visitor-wine-star--lvl-${level}`}
        onClick={onStarClick}
        onPointerDown={handleStarPointerDown}
        onPointerUp={handleStarPointerUp}
        onPointerCancel={handleStarPointerUp}
        onPointerLeave={handleStarPointerUp}
        aria-label={starAriaLabelOverride ?? defaultStarAriaLabel(level)}
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
