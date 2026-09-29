import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "@/auth/AuthContext";
import { usePageHeaderContext } from "@/components/PageHeaderContext";
import { DynamicScientificBackground } from "@/components/visual/DynamicScientificBackground";

function LayersIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4" aria-hidden="true">
      <path
        d="M12 3 2 8l10 5 10-5-10-5Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <path
        d="m2 13 10 5 10-5"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="m2 18 10 5 10-5"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function GridIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4" aria-hidden="true">
      <rect x="3" y="3" width="7" height="7" rx="1.2" stroke="currentColor" strokeWidth="1.6" />
      <rect x="14" y="3" width="7" height="7" rx="1.2" stroke="currentColor" strokeWidth="1.6" />
      <rect x="3" y="14" width="7" height="7" rx="1.2" stroke="currentColor" strokeWidth="1.6" />
      <rect x="14" y="14" width="7" height="7" rx="1.2" stroke="currentColor" strokeWidth="1.6" />
    </svg>
  );
}

function FolderIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4" aria-hidden="true">
      <path
        d="M3 6.5A1.5 1.5 0 0 1 4.5 5h4l2 2h9A1.5 1.5 0 0 1 21 8.5v9A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5v-11Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function AppShell() {
  const { user, logout } = useAuth();
  const { breadcrumb, backgroundVariant, backgroundActivity } = usePageHeaderContext();
  const location = useLocation();
  const isProjectWorkspace = /^\/projects\/[^/]+$/.test(location.pathname);

  return (
    <div className="relative flex min-h-screen flex-col">
      {/* Mounted once at the layout level (not per-page) so navigating
          between Dashboard/Projects/Project Workspace never tears down or
          re-creates the animation loop — only `variant`/`activity` change,
          read live via refs inside the component itself. */}
      <DynamicScientificBackground variant={backgroundVariant} activity={backgroundActivity} />

      <header className="sticky top-0 z-20 border-b border-terrain-800 bg-terrain-950/85 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-2 px-3 py-3 sm:px-6 sm:py-3.5">
          <div className="flex shrink-0 items-center gap-2 sm:gap-2.5">
            <span className="flex h-7 w-7 items-center justify-center rounded-md bg-terrain-900 text-brand-400 ring-1 ring-brand-500/30">
              <LayersIcon />
            </span>
            <span className="whitespace-nowrap text-sm font-semibold text-white sm:text-base">
              TERRAIN<span className="text-brand-400">-X</span>
            </span>
          </div>

          <nav className="flex min-w-0 items-center gap-1 text-sm font-medium text-slate-400">
            <NavLink
              to="/dashboard"
              aria-label="Dashboard"
              className={({ isActive }) =>
                `flex min-h-control min-w-control items-center justify-center gap-1.5 rounded-md px-2 py-1.5 transition-colors duration-150 sm:px-3 ${
                  isActive
                    ? "bg-terrain-800 text-white"
                    : "hover:bg-terrain-900 hover:text-slate-200"
                }`
              }
            >
              <GridIcon />
              <span className="hidden sm:inline">Dashboard</span>
            </NavLink>
            <NavLink
              to="/projects"
              aria-label="Projects"
              className={({ isActive }) =>
                `flex min-h-control min-w-control items-center justify-center gap-1.5 rounded-md px-2 py-1.5 transition-colors duration-150 sm:px-3 ${
                  isActive
                    ? "bg-terrain-800 text-white"
                    : "hover:bg-terrain-900 hover:text-slate-200"
                }`
              }
            >
              <FolderIcon />
              <span className="hidden sm:inline">Projects</span>
            </NavLink>
            <span className="mx-2 hidden h-5 w-px bg-terrain-800 sm:block" />
            <span className="hidden text-slate-500 sm:inline">{user?.email}</span>
            <button
              onClick={logout}
              className="min-h-control whitespace-nowrap rounded-md border border-terrain-800 px-2 py-1.5 text-slate-300 transition-colors duration-150 hover:border-terrain-700 hover:bg-terrain-900 sm:px-3"
            >
              Logout
            </button>
          </nav>
        </div>

        {breadcrumb.length > 0 && (
          <div className="border-t border-terrain-800 bg-terrain-950/70">
            <div className="mx-auto flex max-w-6xl items-center gap-1.5 overflow-hidden px-3 py-1.5 text-xs text-slate-500 sm:px-6">
              {breadcrumb.map((item, index) => (
                <span key={`${item.label}-${index}`} className="flex min-w-0 items-center gap-1.5">
                  {index > 0 && <span className="text-slate-600">/</span>}
                  {item.to ? (
                    <NavLink to={item.to} className="hover:text-slate-300 hover:underline">
                      {item.label}
                    </NavLink>
                  ) : (
                    <span className="truncate font-medium text-slate-300">{item.label}</span>
                  )}
                </span>
              ))}
            </div>
          </div>
        )}
      </header>

      <main
        className={`relative z-10 mx-auto w-full flex-1 ${
          isProjectWorkspace ? "max-w-[1600px] px-2 py-3 sm:px-4" : "max-w-6xl px-6 py-8"
        }`}
      >
        <div key={location.pathname} className="animate-fade-in-up">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
