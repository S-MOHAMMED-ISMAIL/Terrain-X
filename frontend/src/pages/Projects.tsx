import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "@/api/client";
import type { Project } from "@/api/types";
import { useBackgroundVariant, usePageBreadcrumb } from "@/components/PageHeaderContext";
import { Button, Card, EmptyState, Spinner } from "@/components/ui";

function FolderPlusIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-8 w-8" aria-hidden="true">
      <path
        d="M3 6.5A1.5 1.5 0 0 1 4.5 5h4l2 2h9A1.5 1.5 0 0 1 21 8.5v9A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5v-11Z"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
      <path d="M12 11v4M10 13h4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}

export function ProjectsPage() {
  useBackgroundVariant("projects");
  usePageBreadcrumb([{ label: "Projects" }]);

  const [projects, setProjects] = useState<Project[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [isCreating, setIsCreating] = useState(false);

  async function refresh() {
    try {
      const data = await api.listProjects();
      setProjects(data);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load projects");
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function handleCreate(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setIsCreating(true);
    try {
      await api.createProject(name, description);
      setName("");
      setDescription("");
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create project");
    } finally {
      setIsCreating(false);
    }
  }

  async function handleDelete(id: string) {
    setError(null);
    try {
      await api.deleteProject(id);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to delete project");
    }
  }

  return (
    <div>
      <h1 className="mb-6 text-2xl font-semibold tracking-tight text-white">Projects</h1>

      {error && (
        <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 animate-fade-in">
          {error}
        </div>
      )}

      <Card className="mb-8" padding="md">
        <form onSubmit={handleCreate} className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <div className="flex-1">
            <label htmlFor="project-name" className="mb-1 block text-sm font-medium text-slate-700">Name</label>
            <input
              id="project-name"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm transition-colors focus:border-brand-500"
              placeholder="e.g. Coastal Flood Study"
            />
          </div>
          <div className="flex-1">
            <label htmlFor="project-description" className="mb-1 block text-sm font-medium text-slate-700">Description</label>
            <input
              id="project-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm transition-colors focus:border-brand-500"
              placeholder="optional"
            />
          </div>
          <Button type="submit" disabled={isCreating} className="flex items-center gap-2">
            {isCreating && <Spinner />}
            {isCreating ? "Creating…" : "Create project"}
          </Button>
        </form>
      </Card>

      {projects === null ? (
        <div className="flex flex-col gap-2">
          <div className="h-16 w-full skeleton" />
          <div className="h-16 w-full skeleton" />
        </div>
      ) : projects.length === 0 ? (
        <EmptyState
          icon={<FolderPlusIcon />}
          title="No projects yet"
          description="Create your first project above to upload imagery and run a real terrain analysis."
        />
      ) : (
        <ul className="stagger-children divide-y divide-slate-200 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-panel">
          {projects.map((project) => (
            <li
              key={project.id}
              className="flex items-center justify-between px-4 py-3 transition-colors hover:bg-slate-50"
            >
              <div>
                <Link
                  to={`/projects/${project.id}`}
                  className="font-medium text-slate-900 transition-colors hover:text-brand-700 hover:underline"
                >
                  {project.name}
                </Link>
                {project.description && (
                  <p className="text-sm text-slate-500">{project.description}</p>
                )}
                <p className="text-xs text-slate-500">
                  Created {new Date(project.created_at).toLocaleString()}
                </p>
              </div>
              <Button variant="danger" onClick={() => handleDelete(project.id)}>
                Delete
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
