import { lazy, Suspense, type ReactNode } from "react";
import { Spin } from "antd";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import AppLayout from "./components/AppLayout";

const AnnouncementsPage = lazy(() => import("./pages/AnnouncementsPage"));
const HomePage = lazy(() => import("./pages/HomePage"));
const NewTaskPage = lazy(() => import("./pages/NewTaskPage"));
const ReportsPage = lazy(() => import("./pages/ReportsPage"));
const SettingsPage = lazy(() => import("./pages/SettingsPage"));
const SourcesPage = lazy(() => import("./pages/SourcesPage"));
const TasksPage = lazy(() => import("./pages/TasksPage"));

function lazyPage(page: ReactNode) {
  return (
    <Suspense
      fallback={(
        <div aria-live="polite" style={{ padding: 48, textAlign: "center" }}>
          <Spin tip="正在加载页面…" />
        </div>
      )}
    >
      {page}
    </Suspense>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppLayout />}>
          <Route index element={lazyPage(<HomePage />)} />
          <Route path="tasks/new" element={lazyPage(<NewTaskPage />)} />
          <Route path="tasks" element={lazyPage(<TasksPage />)} />
          <Route path="announcements" element={lazyPage(<AnnouncementsPage />)} />
          <Route path="sources" element={lazyPage(<SourcesPage />)} />
          <Route path="reports" element={lazyPage(<ReportsPage />)} />
          <Route path="settings" element={lazyPage(<SettingsPage />)} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
