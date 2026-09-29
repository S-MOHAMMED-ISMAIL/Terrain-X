import { describe, expect, it, vi } from "vitest";
import {
  mapClickFromLatLng,
  overlayCorners,
  type PixelResolver,
  resolveToSourcePixel,
} from "./mapClick";

describe("mapClickFromLatLng", () => {
  it("passes a georeferenced click on as its map coordinate only — no row/col", () => {
    const click = mapClickFromLatLng(60.02, 17.91, true);
    expect(click).toEqual({ lat: 60.02, lng: 17.91 });
    expect(click.row).toBeUndefined();
    expect(click.col).toBeUndefined();
  });

  it("keeps the existing CRS.Simple local-pixel mapping unchanged", () => {
    expect(mapClickFromLatLng(12.4, 30.6, false)).toEqual({ row: 12, col: 31 });
  });
});

describe("resolveToSourcePixel", () => {
  it("asks the backend resolver for a georeferenced click (lng, lat order)", async () => {
    const resolver = vi.fn<PixelResolver>().mockResolvedValue({ row: 41, col: 7, in_bounds: true });
    await expect(resolveToSourcePixel({ lat: 60.02, lng: 17.91 }, resolver)).resolves.toEqual({
      row: 41,
      col: 7,
    });
    expect(resolver).toHaveBeenCalledTimes(1);
    expect(resolver).toHaveBeenCalledWith(17.91, 60.02);
  });

  it("returns null when the backend says the point is outside the raster", async () => {
    const resolver = vi.fn<PixelResolver>().mockResolvedValue({ row: -3, col: 900, in_bounds: false });
    await expect(resolveToSourcePixel({ lat: 1, lng: 2 }, resolver)).resolves.toBeNull();
  });

  it("uses an already-resolved pixel (3D / CRS.Simple) as-is, without a request", async () => {
    const resolver = vi.fn<PixelResolver>();
    await expect(resolveToSourcePixel({ row: 5, col: 6 }, resolver)).resolves.toEqual({
      row: 5,
      col: 6,
    });
    expect(resolver).not.toHaveBeenCalled();
  });

  it("propagates a resolver failure instead of guessing a pixel", async () => {
    const resolver = vi.fn<PixelResolver>().mockRejectedValue(new Error("422"));
    await expect(resolveToSourcePixel({ lat: 1, lng: 2 }, resolver)).rejects.toThrow("422");
  });

  it("rejects a click with neither a pixel nor a coordinate", async () => {
    await expect(resolveToSourcePixel({}, vi.fn<PixelResolver>())).rejects.toThrow();
  });
});

describe("overlayCorners", () => {
  const size = { width: 128, height: 96 };

  it("places a georeferenced layer at its own map_overlay_bounds", () => {
    const layer = {
      map_overlay_bounds: { min_x: 17.869, min_y: 59.998, max_x: 17.965, max_y: 60.046 },
    };
    expect(overlayCorners(layer, true, size)).toEqual([
      [59.998, 17.869],
      [60.046, 17.965],
    ]);
  });

  it("uses each layer's own bounds, not a shared box", () => {
    const a = { map_overlay_bounds: { min_x: 1, min_y: 2, max_x: 3, max_y: 4 } };
    const b = { map_overlay_bounds: { min_x: 1.1, min_y: 2.1, max_x: 3.1, max_y: 4.1 } };
    expect(overlayCorners(a, true, size)).not.toEqual(overlayCorners(b, true, size));
  });

  it("refuses to place a georeferenced layer without bounds (no fallback)", () => {
    expect(overlayCorners({ map_overlay_bounds: null }, true, size)).toBeNull();
  });

  it("keeps local pixel space for a non-georeferenced dataset", () => {
    expect(overlayCorners({ map_overlay_bounds: null }, false, size)).toEqual([
      [0, 0],
      [96, 128],
    ]);
  });
});
