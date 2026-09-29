import { describe, expect, it } from "vitest";
import { TABS, tabRoutesToTerrainWorkspace } from "./workspaceTabs";

describe("workspace tabs", () => {
  it("Disaster is no longer falsely disabled", () => {
    const disaster = TABS.find((t) => t.id === "disaster");
    expect(disaster).toBeDefined();
    expect(disaster!.enabled).toBe(true);
  });

  it("every tab is enabled (nothing is falsely marked as not yet built)", () => {
    for (const tab of TABS) {
      expect(tab.enabled).toBe(true);
    }
  });

  it("Disaster navigation reaches the real, existing TerrainWorkspace/DisasterPanel flow", () => {
    expect(tabRoutesToTerrainWorkspace("disaster")).toBe(true);
  });

  it("Disaster routes to the exact same real workspace as Terrain and Measurements — no duplicate implementation", () => {
    expect(tabRoutesToTerrainWorkspace("disaster")).toBe(tabRoutesToTerrainWorkspace("terrain"));
    expect(tabRoutesToTerrainWorkspace("disaster")).toBe(
      tabRoutesToTerrainWorkspace("measurements"),
    );
  });

  it("unrelated tabs (datasets, analysis, reports) do not route to the terrain workspace", () => {
    expect(tabRoutesToTerrainWorkspace("datasets")).toBe(false);
    expect(tabRoutesToTerrainWorkspace("analysis")).toBe(false);
    expect(tabRoutesToTerrainWorkspace("reports")).toBe(false);
  });
});
