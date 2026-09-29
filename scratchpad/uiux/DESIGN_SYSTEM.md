# TERRAIN-X Proposed Design System

Status: Phase 2 shared foundations and the Phase 3 terrain workspace shell are
implemented. Unimplemented domain-specific contracts below remain proposals
until their assigned UI/UX phases authorize implementation.

## Implemented foundation (Phase 2)

The implemented token source is `frontend/src/index.css`, exposed to components
through `frontend/tailwind.config.js`:

- semantic `surface`, `ui-border`, `content`, `accent`, and `status` colors
- metadata/supporting/control/panel/workspace/page typography roles
- 36px desktop controls, 44px touch targets, and responsive gutter spacing
- 4px control, 6px panel, and 8px dialog radii
- panel/elevated/popover shadows and 120/150/220ms motion durations
- standard/entry/exit easing and a global reduced-motion override
- wrapping control rows, stacked fields, compact headers, and motion panels

Implemented shared components in `frontend/src/components/ui` are `Button`,
`IconButton`, form controls and `Field`, `Tabs`, `Tooltip`, `Notice`,
`LiveStatus`, `Badge`/`StatusBadge`, `Dialog`, `ConfirmDialog`, loading/error/
success/processing/empty states, and technical information primitives. Login and
Register use the new form, notice, and button contracts without changing auth
behavior.

The primary button intentionally pairs the active teal token with dark text; the
measured contrast is 5.06:1. Existing `danger` button behavior remains as a
compatibility variant while `destructive` is the explicit destructive-action
contract. These foundations do not authorize bulk migration of workspace UI.

## Implemented workspace patterns (Phase 3)

The following workspace contracts are now implemented:

- `TerrainWorkspaceShell` owns project/dataset context, quality presentation,
  responsive rail/drawer geometry, and contextual mode selection. It does not
  own scientific state or recreate the selected Leaflet/Three.js viewport when
  panels change.
- Desktop at 1280px and wider uses 256px Layers and 320px Inspector rails around
  a fluid central viewport. Tablet at 768-1279px uses edge overlays above
  Leaflet panes. Phone below 768px uses the deliberate vertical order viewport,
  Inspector, then Layers, with bounded internal scrolling and no horizontal
  overflow.
- `DataQualityBar` values are conservative projections of real visualization
  context: metric/relative, reported vertical units, CRS/local coordinates, and
  calibration availability. Analysis completion alone never implies accuracy.
- `LayerRow` keeps active inspection selection distinct from visibility and
  expands details/legend only for the active row.
- Explore, Measure, Screen, and Flythrough are contextual modes over one retained
  workspace. The existing project Terrain, Measurements, and Disaster tabs set
  the relevant mode without remounting that workspace.
- The Results drawer holds inspection/results/history and changes geometry
  without discarding viewport state. Closed rails and drawer content are inert;
  toggles expose their controlled region and expanded state.

Phase 3 does not yet implement user-resizable rails/drawer, modal focus trapping
for tablet overlays, or a separate read-only phone capability model. Those
remain later design decisions rather than implied behavior.

## Implemented layer and quality patterns (Phase 4)

- Operational layer rows pair a named active-layer button with a separate
  visibility checkbox. Availability and processing/failure state are textual;
  backend reasons remain visible for disabled rows.
- The rail owns selection, visibility, and opacity. The Inspector owns one
  selected layer's legend and metadata. The Results drawer owns long
  calibration diagnostics. Residual visibility remains in the rail because it
  controls a point overlay rather than a diagnostic document.
- Scientific legends reuse backend ranges/classes and established ramps. UI
  accent colors do not recolor raster data, and legend colors are explicitly
  described as display encodings rather than measurements.
- Quality presentation is a pure mapping over `VisualizationContext` plus its
  associated `AnalysisJob`. Metric/relative mode comes only from terrain
  `height_kind`; calibration lifecycle comes only from `calibration_status` and
  `quality_gate`; vertical units come only from structured unit metadata.
- Unknown values stay Unknown. Missing calibration, rejected calibration, and
  processing are separate states. A relative fallback remains visible after a
  calibration failure.
- NoData is never equated with zero. Selected raster metadata is cached by
  artifact ID, and both explicit and unreported NoData states have text.
- Quality details use a bounded overlay disclosure so opening them cannot resize
  the central viewport. Long calibration diagnostics use a collapsed native
  disclosure in the internally scrolling Results drawer.

## Principles

1. **Truth before decoration.** Every status, unit, stage, and quality claim comes
   from real application state. Relative depth is never presented as elevation.
2. **Viewport first.** Spatial data remains visible while the user changes tools,
   layers, or diagnostics.
3. **Dense, not cramped.** Prefer compact rows and panels, but keep readable type,
   predictable grouping, and 44px touch targets where touch is expected.
4. **Progressive disclosure.** Show operational state first; put methodology,
   provenance, and long limitations one action away.
5. **Stable interaction.** Panel changes do not shift measurement targets or
   recreate map/3D engines.
6. **Accessible by default.** Keyboard, focus, contrast, semantics, and reduced
   motion are component contracts, not cleanup work.

## Visual direction

TERRAIN-X should read as a professional geospatial instrument: charcoal chrome,
clear neutral data surfaces, restrained teal selection, precise typography,
topographic color where data requires it, and semantic status colors. Avoid neon,
ambient glass cards, decorative gradients, oversized marketing headings, and
card-per-control composition.

The animated scientific background may remain on sparse dashboard/project pages
if measured cost is acceptable. The terrain workspace uses an opaque, quiet base
so map imagery and scientific layers control the visual field.

## Color tokens

Final values require contrast verification in implementation. Proposed roles:

| Token | Proposed value | Use |
| --- | --- | --- |
| `surface.app` | `#080B0E` | global/workspace base |
| `surface.chrome` | `#0E1318` | app bar, rails, toolbars |
| `surface.panel` | `#151B21` | dark panels and drawers |
| `surface.raised` | `#1C242C` | menus, popovers, selected rows |
| `surface.canvas` | `#05070A` | 3D viewport outside terrain |
| `surface.data` | `#F7F9FA` | light reports/forms where sustained reading benefits |
| `border.subtle` | `#29333D` | dark dividers |
| `border.strong` | `#44515D` | selected/focus-adjacent boundaries |
| `text.primary` | `#F2F5F7` | dark-surface primary text |
| `text.secondary` | `#A8B3BD` | dark-surface supporting text |
| `text.muted` | `#7F8B96` | metadata; must still pass contrast |
| `accent.default` | `#18B9A7` | primary action and selection |
| `accent.hover` | `#28C9B7` | hover |
| `accent.active` | `#0E8F82` | pressed/active |
| `status.info` | `#38A3E8` | informational state |
| `status.success` | `#39B873` | completed/available, not accuracy certification |
| `status.warning` | `#E4A83B` | limitations, screening, rejected calibration |
| `status.danger` | `#E35D6A` | failed/destructive |

Scientific ramps are data assets, not brand colors. Keep existing authoritative
layer ramps unless separately validated. Always pair ramps with labels and
numeric endpoints; do not rely on hue alone.

## Typography

Use the existing system sans stack for UI and the existing mono stack for
coordinates, pixel indices, CRS codes, IDs, and diagnostic values.

| Role | Size / line height | Weight |
| --- | --- | --- |
| Page title | 24 / 32px | 600 |
| Workspace title | 18 / 24px | 600 |
| Panel title | 14 / 20px | 600 |
| Body/control | 14 / 20px | 400/500 |
| Supporting | 13 / 18px | 400 |
| Metadata minimum | 12 / 16px | 400/500 |
| Numeric HUD | 12 / 16px mono | 500 |

Do not use viewport-scaled type or negative letter spacing. Uppercase is limited
to short category labels; long labels use sentence case.

## Spacing and geometry

- Base spacing unit: 4px.
- Common gaps: 4, 8, 12, 16, 24, 32px.
- Control height: 36px desktop, 44px touch contexts.
- Icon buttons: stable 36x36px desktop and 44x44px touch.
- Radius: 4px controls, 6px panels/cards, 8px dialogs only.
- Border: 1px; selected rows use color/background, not layout-changing width.
- Shadow: use only for menus, dialogs, and floating overlays. Rails and sections
  use borders/dividers.

No card should contain another decorative card. Repeated objects may be cards;
page sections and tool groups should be unframed panels or rows.

## Layout tokens

| Token | Desktop target |
| --- | ---: |
| Global app bar | 52px |
| Project context bar | 44px |
| Workspace command bar | 44px |
| Layers rail | 256px, collapsible to 44px |
| Tool/inspector rail | 320px, resizable 280-400px |
| Results drawer | 220px default, resizable 160-45vh |
| Viewport minimum | 560x420px |

The workspace uses available viewport height below app/project chrome. Rails and
drawer scroll independently. The map/3D region has stable minimum dimensions and
receives explicit resize notifications when surrounding panels change.

## Responsive model

### Desktop: 1280px and wider

Persistent left layers rail, center viewport, optional right contextual rail,
and bottom results drawer. Panels can collapse without unmounting the viewport.

### Tablet: 768-1279px

Viewport remains primary. Layers and Tools open as edge drawers; only one drawer
is open at a time. Results use a bottom sheet. All drawer controls are keyboard
reachable and return focus to their trigger.

### Phone: below 768px

Provide project status, dataset list/upload where practical, analysis monitoring,
report downloads, data quality, layer metadata, and a read-only 2D preview.
Precise measurements, full 3D interaction, flythrough path editing, and recording
show "Use a larger screen for this precision workspace" rather than rendering a
broken miniature tool. Zero horizontal page overflow is mandatory.

## Component contracts

### AppBar

Single-line brand, primary navigation, user menu, and mobile overflow menu.
Current route has both visible and semantic state. Email moves into the user menu
on constrained widths.

### ProjectContextBar

Project/dataset breadcrumb, analysis outcome, units/quality summary, and primary
workspace/report actions. Truncates safely with full text in tooltip.

### WorkspaceShell

Owns panel geometry only. It must not own scientific data or recreate Leaflet or
Three.js on panel toggles.

### Panel and Drawer

`Panel` is an unframed workspace region with header, optional toolbar, and scroll
body. `Drawer` adds focus management and overlay behavior for smaller screens.

### Button and IconButton

Variants: primary, secondary, ghost, danger. Primary uses accent color, one per
local action group. Icon-only buttons require accessible name and Tooltip.
Loading preserves width and exposes busy state.

### ToolButton and Toolbar

Stable icon button with label in expanded contexts, `aria-pressed`, disabled
reason, active cursor/tool state, and tooltip. Toolbar uses roving tabindex where
appropriate and arrow-key navigation.

### SegmentedControl

For 2D/3D and mutually exclusive compact modes. Uses radio semantics, not tabs,
unless it controls distinct tab panels.

### Tabs

Only for real peer panels. Implements tablist/tab/tabpanel semantics, arrow-key
navigation, and a visible active indicator. Do not use tabs as action buttons.

### FormField

Creates label/ID/description/error association. Error text is specific and
announced once. Units are visible as suffixes where applicable. Numeric fields
declare constraints without implying unsupported precision.

### StatusBadge

Status text is always present. Color is supplementary. Shapes/tones are stable:
neutral, info, success, warning, danger, active. Animated dots only indicate a
genuinely active backend operation and stop under reduced motion.

### DataQualityBadge and DataQualityBar

First-order trust signal. Required states include:

- `Relative | Unitless`
- `Calibrated | Metric`
- `Calibration rejected`
- `Georeferenced` / `Local pixel coordinates`
- verified/unknown vertical unit

It never maps "completed analysis" to "scientifically accurate".

### LayerRow

Contains drag/order affordance only if ordering is supported, scientific swatch,
visibility toggle, active-layer selection, concise name, unit/status, opacity
popover, and details menu. Visibility and active inspection state are separate.

### LegendPanel

Displays the active layer's real categorical or continuous legend, endpoints,
units, nodata, and semantic caveat. It is not repeated in every layer row.

### ProgressStepper

Consumes actual backend stage/status and selected workflow parameters. Supports
done/current/pending/skipped/failed. It never extrapolates time or fabricates
percent completion. Terminal state is announced through a polite live region.

### LoadingState

Variants: inline spinner, bounded skeleton, viewport loading overlay. Has visible
text or accessible name and `aria-busy` on the affected region. Skeleton geometry
matches final layout and does not flash for trivial operations when avoidable.

### EmptyState and PrerequisiteState

EmptyState means genuine absence and includes the next valid action.
PrerequisiteState explains which upstream dataset/analysis/calibration is missing
and links to it. Do not represent prerequisites as generic disabled gray cards.

### ErrorState and Notice

ErrorState structure: title, what happened, consequence, remaining capability,
recovery action, optional technical details. Notice variants communicate
information, scientific limitation, warning, or danger. Backend stack traces are
never displayed.

### Tooltip

Works on hover and keyboard focus, is dismissible, and does not contain required
information or interactive controls. Native `title` is insufficient for primary
help.

### ConfirmDialog

Used for deleting projects/datasets/measurements and destructive path clearing
when meaningful. Focus is trapped, initial focus is safe, Escape closes, and
focus returns to the trigger. Name the object being deleted.

### MeasurementResult

Shows measurement type, value, verified unit/relative semantics, layer source,
coordinates, uncertainty/limitations, save state, and profile chart where
applicable. Large results use tabular numerals. The result drawer keeps the
viewport visible.

### ExportMenu and TaskHistory

Groups terrain, flight, and report outputs. Each item states format, contents,
prerequisites, immediate/generated behavior, generation status, and completion.
Unavailable formats have a visible reason.

### WorkspaceToolSection

Operational tools use one compact section pattern: title, always-visible textual
status badge, one-sentence context, then controls or results. Valid state labels
include Ready, Active, Editing, Processing/Generating, Completed, Failed, and
Unavailable. Color is supplementary. Multiple sections may share one card when
they are stages of a single tool (for example waypoints plus playback), but a
section never nests a decorative card.

Async tool actions keep feedback local. Use the real backend percentage only
when supplied; otherwise use a spinner/indeterminate label. Announce terminal
changes once, not polling ticks or animation-frame updates. A mode change resets
the Inspector scroll position so the active tool heading and status are visible.

## Workspace interaction model

### Explore

Default mode. Click inspects the active visible scientific layer. Cursor/status
bar shows geographic coordinates or local pixels, source layer, and units.

### Measure

Tool palette: point, coordinate, distance, profile, slope. Active tool persists
visibly; Escape cancels; Clear removes pending geometry; completed result opens
the drawer. Required point count and next click are stated without overlaying the
measurement target.

### Screen

Requires calibrated metric elevation. Configuration appears in the right panel;
results appear in the layer rail and drawer. "Screening, not prediction" remains
persistent and is never reduced to a tooltip.

### Flythrough

Path editing works in 2D and 3D. Playback controls dock under the viewport with
standard previous/play-pause/restart/stop symbols and tooltips. Recording has an
unmissable active state, elapsed time, and stop action. First-person mode and path
playback are mutually explicit.

## 2D and 3D rules

- The same selected dataset, active layer, units, and quality state appear in
  both representations.
- Coordinate/pixel conversion remains backend-authoritative.
- 3D texture state respects existing spatial-compatibility rules.
- Camera presets use icons with tooltips and do not alter scientific data.
- 3D HUD uses 12px minimum type and avoids overlapping honesty notices.
- Decorative transitions never animate measured points or terrain geometry.
- WebGL fallback preserves access to 2D layers, metadata, measurements supported
  by 2D, and exports.

## Calibration presentation

### Passed

Use neutral/teal emphasis with a success status: "Calibrated; metric elevation
available." Show reference type, unit, sample count, held-out skill, and a
diagnostics link. State that passing is not independent accuracy certification.

### Rejected

Use warning, not generic system failure: "Calibration rejected; relative terrain
available; metric elevation unavailable." Show failed criteria and reference
review action. Do not expose metric-only tools as if merely loading.

### Not requested

Use neutral state: relative/unitless terrain is expected. Offer calibration setup
as a workflow action without implying the relative result is broken.

## State language

- Prefer `Ready`, `Running`, `Completed`, `Failed`, `Calibration rejected`, and
  `Unavailable` with a reason.
- Avoid vague `Processing...`, `Something went wrong`, and unexplained disabled
  controls.
- Use ellipsis only for an action in progress: `Generating...`.
- Preserve technically necessary terms, then explain them in plain language.

## Motion

| Interaction | Duration | Behavior |
| --- | ---: | --- |
| hover/press | 120ms | color/background only |
| selection/focus | 150ms | color, border, subtle opacity |
| panel/drawer | 180-240ms | transform + opacity |
| toast | 180ms | small translate + opacity |
| progress stage | 200ms | state color/connector |

Use standard ease-out for entry and ease-in for exit. No stagger on operational
lists after initial load. Reduced motion collapses all optional motion. Map pan,
3D camera, measurement anchors, and flythrough follow functional controls only.

## Accessibility acceptance

- Every input has a programmatic label and error/help association.
- All actions are reachable and operable by keyboard in logical order.
- Focus is never lost when panels close, jobs complete, or items are deleted.
- Icon-only controls have accessible names and focusable tooltips.
- Status is not color-only; continuous ramps include numeric labels.
- Normal text meets WCAG AA contrast; focus indicators meet non-text contrast.
- Touch targets are at least 44x44px where touch layout is offered.
- Polling announces only meaningful stage/terminal changes.
- Canvas workflows have adjacent textual state and controls; unsupported precise
  canvas interaction is explicitly bounded rather than falsely claimed.
- Reduced motion is tested, not inferred from CSS alone.

## Performance constraints

- Keep Leaflet and Three.js mounted across panel/tool changes.
- Never bind React state updates to every animation frame.
- Resize canvases only through observed container changes.
- Dispose Three.js geometry, materials, textures, and listeners deterministically.
- Revoke object URLs and avoid duplicate image decoding.
- Poll only active jobs and stop at terminal state.
- Virtualize only after measuring genuinely large lists.
- Profile the global decorative canvas; disable it in the workspace if it affects
  frame time or power usage.
- Treat T2 as the operational baseline; UI work must not increase request storms,
  artifact downloads, or memory retention.

## Validation matrix for implementation

Every phase should check desktop 1440x900 and 1280x800, tablet 1024x768, phone
390x844, keyboard-only navigation, reduced motion, loading/empty/error/success,
relative/no-calibration, calibration passed, calibration rejected, 2D, 3D,
WebGL unavailable, each measurement mode, disaster unavailable/completed/failed,
flythrough/edit/play/record unsupported, GLB export, report generation/failure,
and long project/dataset names.

Existing D1/D2/D3, terrain, measurement, flythrough, export, report, and golden
path tests remain behavioral guardrails. Visual tests supplement them; they do
not replace coordinate or artifact correctness assertions.

## Analysis and report operations

### AnalysisJobCard

Use one compact operational card per backend job. The header identifies the
input and exact job state; the body lists each real product with its state,
meaning, verified unit, unavailable reason, and supported download. Keep
calibration diagnostics in a native disclosure, with in-sample fit and held-out
validation in separate labeled sections. Never collapse them into a score.

DTM and nDSM are products of the calibrated terrain pipeline, not independent
jobs. Describe DTM as an estimated ground surface and nDSM as DSM minus the
estimated DTM. Unknown vertical units remain `Unknown`.

### LifecycleRow

Map backend lifecycle values to explicit text plus color. Active jobs may show a
real backend percentage; reports use an indeterminate state because the report
API supplies no percentage. Poll only while a backend item is active. Put the
state text, not the percentage, in a polite atomic live region so queued,
running/generating, completed, and failed transitions are announced once.

### ProductRow and DownloadGrid

Product rows pair a scientific definition with state, unit, prerequisite or
failure reason, and only the actions the API supports. Completed report files
use a responsive download grid showing format and filename; unavailable formats
stay disabled and expose the reason. Preserve short stable accessible names
such as `PDF`, `CSV`, and `ZIP bundle` independently of visible filenames.

### Analysis-to-workspace continuity

An analysis result opens the existing Terrain Workspace with that job's source
dataset selected. Once opened, the workspace remains mounted while the user
moves through Analysis and Reports, preserving the terrain engine and local
view state. Returning must not regenerate artifacts or create duplicate preview
requests.

## Final loading and responsive rules

- Route-load the Project Workspace so authentication, Dashboard and Projects do
  not download terrain dependencies.
- Load Three.js on first 3D use and charting on first profile result. Every lazy
  boundary must have a named status fallback and preserve the existing engine's
  lifecycle once mounted.
- Operational/diagnostic text uses 12px (`text-metadata`) as its minimum. Do not
  use explicit 10-11px utilities to force dense content into a panel.
- Validate document overflow at 390, 412, 768, 1024, 1280, 1440 and 1920px.
  Workspace rails, drawers and the viewport may scroll internally by design;
  the document must not acquire horizontal scrolling.
- Final visual baselines use reduced-motion rendering and wait for terminal
  backend state, never an entrance animation or intermediate polling state.
- Deduplicate only concurrent identical page loads. Do not add stale cross-route
  caches merely to reduce request counts.
