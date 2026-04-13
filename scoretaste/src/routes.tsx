import { Navigate, Route, Routes } from "react-router-dom";
import { t } from "./i18n";
import { EventSessionLayout } from "./layout/EventSessionLayout";
import { ContributorPage } from "./pages/ContributorPage";
import { EventEntryPage } from "./pages/EventEntryPage";
import { MapEditorPage } from "./pages/MapEditorPage";
import { PilotMonitorPage } from "./pages/PilotMonitorPage";
import { MyWinesPage } from "./pages/MyWinesPage";
import { SavedSelectionOpenPage } from "./pages/SavedSelectionOpenPage";
import { WineryDetailPage } from "./pages/WineryDetailPage";
import { WineryListPage } from "./pages/WineryListPage";

function GuideRootPlaceholder() {
  return (
    <main>
      <h1>{t("guide.title")}</h1>
      <p>{t("guide.skeletonNote")}</p>
    </main>
  );
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<GuideRootPlaceholder />} />
      <Route path="s/:token" element={<SavedSelectionOpenPage />} />
      <Route path="e/:eventId/map-editor" element={<MapEditorPage />} />
      <Route path="e/:eventId/monitor" element={<PilotMonitorPage />} />
      <Route path="e/:eventId" element={<EventSessionLayout />}>
        <Route index element={<EventEntryPage />} />
        <Route path="wineries" element={<WineryListPage />} />
        <Route path="wineries/:wineryId" element={<WineryDetailPage />} />
        <Route path="my" element={<MyWinesPage />} />
      </Route>
      <Route
        path="contribute/:eventId/:wineryId"
        element={<ContributorPage />}
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
