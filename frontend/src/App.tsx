import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "@/components/AppShell";
import { PageHeaderProvider } from "@/components/PageHeaderContext";
import { ProtectedRoute } from "@/routes/ProtectedRoute";
import { LoginPage } from "@/pages/Login";
import { RegisterPage } from "@/pages/Register";
import { DashboardPage } from "@/pages/Dashboard";
import { ProjectsPage } from "@/pages/Projects";

const ProjectWorkspacePage = lazy(() =>
  import("@/pages/ProjectWorkspace").then((module) => ({ default: module.ProjectWorkspacePage })),
);

function WorkspaceRoute() {
  return (
    <Suspense fallback={<div role="status" className="p-4 text-sm text-slate-300">Loading project workspace...</div>}>
      <ProjectWorkspacePage />
    </Suspense>
  );
}

export default function App() {
  return (
    <PageHeaderProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />

        <Route element={<ProtectedRoute />}>
          <Route element={<AppShell />}>
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/projects" element={<ProjectsPage />} />
            <Route path="/projects/:projectId" element={<WorkspaceRoute />} />
          </Route>
        </Route>

        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </PageHeaderProvider>
  );
}
