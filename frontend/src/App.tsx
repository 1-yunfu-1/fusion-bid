import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import AppLayout from "./components/AppLayout";
import AnnouncementsPage from "./pages/AnnouncementsPage";
import HomePage from "./pages/HomePage";
import NewTaskPage from "./pages/NewTaskPage";
import ReportsPage from "./pages/ReportsPage";
import SettingsPage from "./pages/SettingsPage";
import SourcesPage from "./pages/SourcesPage";
import TasksPage from "./pages/TasksPage";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppLayout />}>
          <Route index element={<HomePage />} />
          <Route path="tasks/new" element={<NewTaskPage />} />
          <Route path="tasks" element={<TasksPage />} />
          <Route path="announcements" element={<AnnouncementsPage />} />
          <Route path="sources" element={<SourcesPage />} />
          <Route path="reports" element={<ReportsPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
