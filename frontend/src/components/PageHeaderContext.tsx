import { createContext, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

export interface BreadcrumbItem {
  label: string;
  to?: string;
}

// Purely a decoration selector for DynamicScientificBackground — never a
// source of real data. "activity" reflects a real, already-known
// application state (e.g. whether a real analysis/disaster job is
// currently queued/running) so the ambient motion can settle or become
// slightly more active accordingly; it is never a fabricated timer.
export type BackgroundVariant =
  | "default"
  | "dashboard"
  | "projects"
  | "analysis"
  | "terrain"
  | "disaster"
  | "reports";
export type BackgroundActivity = "idle" | "active";

interface PageHeaderContextValue {
  breadcrumb: BreadcrumbItem[];
  setBreadcrumb: (items: BreadcrumbItem[]) => void;
  backgroundVariant: BackgroundVariant;
  setBackgroundVariant: (variant: BackgroundVariant) => void;
  backgroundActivity: BackgroundActivity;
  setBackgroundActivity: (activity: BackgroundActivity) => void;
}

const PageHeaderContext = createContext<PageHeaderContextValue | null>(null);

/** Lets AppShell render a real navigation breadcrumb (e.g. "Projects / My
 * Coastal Study") without re-fetching data the current page already has —
 * a page calls `usePageBreadcrumb` with real values it already loaded
 * (a project's actual name, never a placeholder). Also carries the current
 * page's decorative background variant/activity for the same reason. */
export function PageHeaderProvider({ children }: { children: ReactNode }) {
  const [breadcrumb, setBreadcrumb] = useState<BreadcrumbItem[]>([]);
  const [backgroundVariant, setBackgroundVariant] = useState<BackgroundVariant>("default");
  const [backgroundActivity, setBackgroundActivity] = useState<BackgroundActivity>("idle");
  const value = useMemo(
    () => ({
      breadcrumb,
      setBreadcrumb,
      backgroundVariant,
      setBackgroundVariant,
      backgroundActivity,
      setBackgroundActivity,
    }),
    [breadcrumb, backgroundVariant, backgroundActivity],
  );
  return <PageHeaderContext.Provider value={value}>{children}</PageHeaderContext.Provider>;
}

export function usePageHeaderContext(): PageHeaderContextValue {
  const ctx = useContext(PageHeaderContext);
  if (!ctx) throw new Error("usePageHeaderContext must be used within PageHeaderProvider");
  return ctx;
}

/** Call from any page with the real breadcrumb trail for that page. Clears
 * itself on unmount so a stale trail never lingers after navigating away. */
export function usePageBreadcrumb(items: BreadcrumbItem[]): void {
  const { setBreadcrumb } = usePageHeaderContext();
  const key = items.map((i) => `${i.label}|${i.to ?? ""}`).join(">");

  useEffect(() => {
    setBreadcrumb(items);
    return () => setBreadcrumb([]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
}

/** Call from any page to select the decorative ambient-background variant
 * while that page is mounted — resets to "default" on unmount. Purely
 * cosmetic; never affects real data or layout. */
export function useBackgroundVariant(variant: BackgroundVariant): void {
  const { setBackgroundVariant } = usePageHeaderContext();
  useEffect(() => {
    setBackgroundVariant(variant);
    return () => setBackgroundVariant("default");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [variant]);
}

/** Call with a real, already-known activity state (e.g. whether a real job
 * is currently queued/running) to let the ambient background settle or
 * become slightly more active — never a fabricated/simulated state. */
export function useBackgroundActivity(activity: BackgroundActivity): void {
  const { setBackgroundActivity } = usePageHeaderContext();
  useEffect(() => {
    setBackgroundActivity(activity);
    return () => setBackgroundActivity("idle");
  }, [activity, setBackgroundActivity]);
}
