import { t } from "../i18n";

/** Maps loader / hook error codes to krátké UI řetězce přes slovník. */
export function catalogErrorTitle(code: string): string {
  switch (code) {
    case "NOT_FOUND":
      return t("errors.notFound");
    case "INVALID_EVENT":
      return t("errors.invalidEvent");
    case "MISSING_EVENT_ID":
      return t("errors.missingEventId");
    case "LOAD_FAILED":
      return t("errors.loadFailed");
    default:
      return t("errors.generic");
  }
}
