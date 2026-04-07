import { useParams } from "react-router-dom";
import { t } from "../i18n";

export function ContributorPage() {
  const { eventId, wineryId } = useParams<{
    eventId: string;
    wineryId: string;
  }>();

  return (
    <main>
      <h1>{t("contributor.title")}</h1>
      <p>
        <strong>{t("contributor.eventIdLabel")}</strong>{" "}
        {eventId ?? t("contributor.missing")}
      </p>
      <p>
        <strong>{t("contributor.wineryIdLabel")}</strong>{" "}
        {wineryId ?? t("contributor.missing")}
      </p>
    </main>
  );
}
