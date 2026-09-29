// The same fixed 8-stop ramp used server-side to colorize single-band
// scientific rasters (see geospatial/raster_preview.py::_RAMP_COLORS) —
// duplicated here only as a CSS gradient for the legend swatch, never used
// to recompute or reinterpret any actual pixel value.
const RAMP_CSS =
  "linear-gradient(to right, rgb(68,1,84), rgb(72,40,120), rgb(62,74,137), " +
  "rgb(49,104,142), rgb(38,130,142), rgb(31,158,137), rgb(53,183,121), rgb(253,231,37))";

function formatValue(value: number): string {
  const abs = Math.abs(value);
  if (abs !== 0 && (abs < 0.01 || abs >= 100000)) return value.toExponential(2);
  return value.toFixed(abs < 10 ? 3 : 1);
}

interface Props {
  minValue: number | null;
  maxValue: number | null;
  unitLabel?: string;
}

/** A real legend: the gradient swatch always spans the actual min/max the
 * backend computed from the actual raster (see RasterMetadata) — never a
 * hardcoded numeric range. Rounded only for display; nothing here is fed
 * back into any computation. */
export function Legend({ minValue, maxValue, unitLabel }: Props) {
  if (minValue === null || maxValue === null) {
    return <p className="text-xs text-slate-500">No finite values to show a range for.</p>;
  }
  return (
    <div>
      <div className="h-2 w-full rounded-full" style={{ background: RAMP_CSS }} />
      <div className="mt-1 flex justify-between text-metadata text-slate-500">
        <span>
          {formatValue(minValue)}
          {unitLabel ? ` ${unitLabel}` : ""}
        </span>
        <span>
          {formatValue(maxValue)}
          {unitLabel ? ` ${unitLabel}` : ""}
        </span>
      </div>
    </div>
  );
}

// Same "golden-angle hue rotation" formula used server-side to colorize the
// categorical raster itself (see geospatial/raster_preview.py::_region_color)
// — duplicated here (not fetched) purely so the legend swatches match the
// real preview PNG's colors for the same region ID, never used to
// reinterpret or recompute an actual pixel value.
function regionColor(regionId: number): string {
  const hue = (regionId * 0.618033988749895) % 1.0;
  return hsvToCss(hue, 0.65, 0.95);
}

function hsvToCss(h: number, s: number, v: number): string {
  const i = Math.floor(h * 6);
  const f = h * 6 - i;
  const p = v * (1 - s);
  const q = v * (1 - f * s);
  const t = v * (1 - (1 - f) * s);
  let r = 0;
  let g = 0;
  let b = 0;
  switch (i % 6) {
    case 0:
      [r, g, b] = [v, t, p];
      break;
    case 1:
      [r, g, b] = [q, v, p];
      break;
    case 2:
      [r, g, b] = [p, v, t];
      break;
    case 3:
      [r, g, b] = [p, q, v];
      break;
    case 4:
      [r, g, b] = [t, p, v];
      break;
    default:
      [r, g, b] = [v, p, q];
  }
  return `rgb(${Math.round(r * 255)}, ${Math.round(g * 255)}, ${Math.round(b * 255)})`;
}

interface FixedLegendEntry {
  value: number;
  label: string;
}

interface FixedLegendProps {
  entries: FixedLegendEntry[];
}

/** A real, FIXED value->label legend for Phase 8's flood_screening/
 * landslide_screening layers — entries come verbatim from the artifact's
 * own persisted class_labels metadata (see LayerContext.legend), never
 * invented here. Uses the same golden-angle-hue color formula as the
 * region-ID swatches above so a class's swatch color matches its actual
 * color in the categorical preview PNG. */
export function FixedLegend({ entries }: FixedLegendProps) {
  if (entries.length === 0) {
    return <p className="text-xs text-slate-500">No legend classes available.</p>;
  }
  return (
    <div className="flex flex-col gap-1">
      {entries.map((entry) => (
        <div key={entry.value} className="flex items-center gap-1.5 text-metadata text-slate-600">
          <span
            className="inline-block h-3 w-3 flex-shrink-0 rounded"
            style={{ backgroundColor: regionColor(entry.value) }}
          />
          {entry.label}
        </div>
      ))}
    </div>
  );
}

// Bounded so a heavily-segmented image never renders thousands of DOM
// swatches — matches the categorical raster's own "N detected regions"
// summary approach (see geospatial/raster_preview.py).
const MAX_SWATCHES = 12;

interface CategoricalLegendProps {
  regionCount: number | null;
}

/** A real per-region-ID legend for a categorical (class-agnostic
 * segmentation) layer — deterministic colors matching the actual preview
 * PNG, never the continuous min/max ramp used for scalar rasters. Region
 * IDs are display swatches only; they carry no semantic-class meaning (see
 * docs/ARCHITECTURE.md §3.6). */
export function CategoricalLegend({ regionCount }: CategoricalLegendProps) {
  if (regionCount === null) {
    return <p className="text-xs text-slate-500">No region count available.</p>;
  }
  if (regionCount === 0) {
    return <p className="text-xs text-slate-500">No distinct regions were detected.</p>;
  }

  const shown = Math.min(regionCount, MAX_SWATCHES);
  const ids = Array.from({ length: shown }, (_, i) => i + 1);

  return (
    <div>
      <div className="flex flex-wrap gap-1.5">
        {ids.map((id) => (
          <span
            key={id}
            title={`Region ${id}`}
            className="inline-flex h-5 w-5 items-center justify-center rounded text-[9px] font-medium text-white/90"
            style={{ backgroundColor: regionColor(id) }}
          >
            {id}
          </span>
        ))}
        {regionCount > MAX_SWATCHES && (
          <span className="inline-flex h-5 items-center rounded bg-slate-100 px-1.5 text-metadata text-slate-500">
            +{regionCount - MAX_SWATCHES} more
          </span>
        )}
      </div>
      <p className="mt-1 text-metadata text-slate-500">
        {regionCount} detected region{regionCount === 1 ? "" : "s"} — arbitrary IDs, not semantic
        classes.
      </p>
    </div>
  );
}
