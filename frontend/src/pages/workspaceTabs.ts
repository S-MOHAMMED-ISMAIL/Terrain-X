export type Tab = "datasets" | "analysis" | "terrain" | "measurements" | "disaster" | "reports";

export interface TabConfig {
  id: Tab;
  label: string;
  enabled: boolean;
}

export const TABS: TabConfig[] = [
  { id: "datasets", label: "Datasets", enabled: true },
  { id: "analysis", label: "Analysis", enabled: true },
  { id: "terrain", label: "Terrain", enabled: true },
  // Phase 7: real point elevation / distance / profile / coordinate
  // measurements, integrated into the same Terrain workspace component
  // (see TerrainWorkspace.tsx's measurement-mode UI) rather than a
  // duplicate map/3D implementation.
  { id: "measurements", label: "Measurements", enabled: true },
  // Phase 8's disaster-screening controls (DisasterPanel) live inside the
  // same Terrain/Measurements workspace component — this tab activates
  // that identical real workspace (exactly like "Measurements" already
  // does) rather than a second, duplicate implementation.
  { id: "disaster", label: "Disaster", enabled: true },
  // Phase 9: real report/export generation and retrieval.
  { id: "reports", label: "Reports", enabled: true },
];

// The tabs that all activate the one real Terrain/Measurements/Disaster
// workspace component (TerrainWorkspace, which itself embeds LayerPanel,
// the 2D/3D views, measurement mode, and DisasterPanel) — never a second,
// parallel implementation per tab.
const TERRAIN_WORKSPACE_TABS: ReadonlySet<Tab> = new Set(["terrain", "measurements", "disaster"]);

export function tabRoutesToTerrainWorkspace(tab: Tab): boolean {
  return TERRAIN_WORKSPACE_TABS.has(tab);
}
