// D1: the terrain's one raster -> world convention, for the few places that
// convert between pixels and the 3D local frame outside terrainMesh.ts.
//
// Local frame: (0, 0) is the grid transform's origin corner (origin_x,
// origin_y); map = origin + local. Grid cell (row, col) — and its mesh
// vertex — is at its CENTRE, local ((col + 0.5) * cellX, (row + 0.5) * cellY),
// with signed cell sizes. Not georeferenced: origin 0, cell 1, and a
// continuous source-pixel coordinate u has pixel i spanning [i, i + 1) with
// its centre at i + 0.5 (the GLB export's pixel_col/pixel_row convention).

/** The pixel index containing continuous pixel coordinate `u`, clamped to
 * [0, count - 1] (the same floor rule as the backend's coordinate_to_pixel). */
export function pixelIndexAt(u: number, count: number): number {
  return Math.min(Math.max(Math.floor(u), 0), count - 1);
}

/** Not georeferenced: the centre of source pixel (row, col) in the 3D local
 * frame of a grid decimated from `sourceWidth x sourceHeight` to
 * `width x height` cells of size (cellX, cellY). */
export function sourcePixelCentreToLocal(
  meta: {
    width: number;
    height: number;
    source_width: number;
    source_height: number;
    cell_size_x: number | null;
    cell_size_y: number | null;
  },
  row: number,
  col: number,
): { localX: number; localZ: number; pixelCol: number; pixelRow: number } {
  const pixelCol = col + 0.5;
  const pixelRow = row + 0.5;
  return {
    localX: pixelCol * (meta.width / meta.source_width) * (meta.cell_size_x ?? 1),
    localZ: pixelRow * (meta.height / meta.source_height) * (meta.cell_size_y ?? 1),
    pixelCol,
    pixelRow,
  };
}
