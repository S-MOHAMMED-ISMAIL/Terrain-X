# UI/UX Phase 2 Shared Foundations

Phase 2 date: 2026-09-28

Status: **PASS**. This phase establishes reusable visual and accessibility
primitives. It does not begin the Terrain Workspace or navigation redesign.

## 1. Design token architecture

Semantic CSS custom properties in `src/index.css` define the charcoal surface
family, borders, dark-surface text, restrained teal accent, and info/success/
warning/danger roles. `tailwind.config.js` exposes those values through
`surface`, `ui-border`, `content`, `accent`, and `status` utilities.

Tailwind also defines the implemented type roles, 36px desktop and 44px touch
control dimensions, 4/6/8px radii, restrained panel/popover shadows, responsive
gutter spacing, and 120/150/220ms motion timings. Scientific ramps remain
separate from interface colors. Primary buttons use dark text on the active teal
token for a measured 5.06:1 contrast ratio.

## 2. Shared primitives created

- Buttons: `Button` and accessible-name-required `IconButton`; primary,
  secondary, ghost, destructive, legacy danger, disabled, and loading states.
- Forms: `Input`, `Select`, `Textarea`, `Checkbox`, `Radio`, `Switch`, and
  render-prop `Field` with generated IDs and description/error associations.
- Interaction: accessible `Tabs`, keyboard/pointer `Tooltip`, `Dialog`, and
  `ConfirmDialog` with focus entry, trapping, Escape, backdrop, and focus return.
- Status: `Notice`, `LiveStatus`, `Badge`/`StatusBadge`, plus compact loading,
  empty, error, success, and processing presentations.
- Engineering information: `MetricValue`, `CoordinateValue`, `QualityState`,
  `DataState`, and `TechnicalKeyValue`.

The primitives render caller-provided state. They do not infer calibration,
accuracy, availability, units, or any other scientific claim.

## 3. Accessibility improvements

Login and Register now have programmatically associated labels, required state,
password guidance, browser autocomplete metadata, error descriptions, live
error announcements, and announced/disabled submit progress. Authentication
behavior and API calls are unchanged.

New controls have visible focus treatment, native disabled semantics, keyboard
operation, and non-color status text. Tabs use tablist/tab/tabpanel semantics and
arrow/Home/End navigation. Tooltips work on hover and focus and dismiss with
Escape. Badge detail no longer depends on a native `title`. Dialogs manage focus
and restore it to the opener. No claim of full WCAG conformance is made.

## 4. Responsive foundation

Reusable gutter, wrapping control-row, stacked-field, and compact-header classes
provide stable `min-width: 0` behavior. Controls are 36px in desktop contexts and
receive a 44px minimum under coarse-pointer media queries. Auth primitives were
browser-tested at 390, 768, 1024, and 1440px with no horizontal overflow.

This does not resolve the known application-shell and Terrain Workspace phone
layout issues recorded in the Phase 1 baseline; those remain later-phase work.

## 5. Motion foundation

The token layer defines short hover/selection/panel durations and standard
entry/exit easings. Dialog and existing auth entry motion use transform/opacity
only. The global `prefers-reduced-motion: reduce` rule collapses custom animation
and transition durations to 0.001ms and disables smooth scrolling. No continuous
animation or Three.js loop was added or changed.

## 6. Tests added

`Foundations.test.tsx` adds 12 jsdom component tests for button focus, disabled
and loading behavior, icon names, field associations, checkbox/switch keyboard
operation, tab semantics/navigation, tooltip access, dialog focus/Escape/return,
visible semantic statuses, technical values, and badge tooltips.

`phase2-auth-accessibility.spec.ts` adds two live-stack browser tests. They cover
registration, logout, login, protected-route redirection, auth labels and error
associations, submit busy state, secret-safe error presentation, responsive
overflow at four widths, and reduced motion.

## 7. Existing components migrated

Only clearly compatible surfaces were migrated. Login and Register now consume
`Field`, `Input`, `Notice`, and `Button`. The existing shared Button gained its
state/size contracts, Badge converts optional detail to the shared Tooltip, and
EmptyState received compact responsive typography/spacing. Existing consumers
retain their commands, data, and API behavior. Complex workspace-specific
controls were deliberately left for later phases.

## 8. Validation results

| Validation | Exact result |
| --- | --- |
| Focused Phase 2 component tests | **1 file; 12 tests passed** in 7.02s |
| Full Vitest | **19 files; 216 tests passed** in 10.28s (Phase 1: 18/204) |
| TypeScript | **clean** (`tsc -b --pretty false`) |
| ESLint | **0 errors; 5 existing Fast Refresh warnings** |
| Vite production build | **passed**, 689 modules, 21.84s |
| Focused auth/accessibility Playwright | **2 tests passed** in 10.4s |
| Full Playwright | **20 passed; 1 transient P1-7 canvas-delta failure** in 10.2m |
| Exact unchanged P1-7 rerun | **1 passed** in 37.8s |
| Production dependency audit | **0 vulnerabilities** (`npm audit --omit=dev`) |

Production bundle output is 0.39 kB HTML (0.27 kB gzip), 49.58 kB CSS
(13.37 kB gzip), and 1,396.60 kB JavaScript (390.85 kB gzip). Vite retains the
known warning for a minified chunk over 500 kB.

The full browser run's only failure was the existing P1-7 assertion that an
orbit drag must change canvas screenshot bytes. It passed immediately when that
exact scenario was rerun unchanged. No test or production code was altered to
accommodate it; all 21 browser scenarios passed across the full run and isolated
rerun.

## 9. Known remaining UI issues

- The application-shell, project-tab, and Terrain Workspace responsive problems
  from Phase 1 remain intentionally unresolved.
- Legacy workspace controls still include native-title usage and locally styled
  form controls; migrate them only within their owning redesign phase.
- The JavaScript bundle increased by about 5 kB minified and remains a single
  large eagerly loaded chunk.
- Five pre-existing Fast Refresh warnings remain in `AuthContext.tsx` and
  `PageHeaderContext.tsx`.
- Automated contrast coverage is not comprehensive; the new primary pairing was
  measured directly, but later surfaces still require phase-specific auditing.
- The P1-7 canvas screenshot-delta assertion showed one transient failure in the
  long all-spec run, then passed unchanged in isolation.

## 10. Phase boundary

The Terrain Workspace redesign was **not started**. Project navigation,
Leaflet, Three.js, terrain calculations, coordinates, calibration,
measurements, DTM/nDSM, disaster analysis, flythrough behavior, GLB generation,
report lifecycle, API schemas, backend code, and database schemas were not
changed. The next authorized step is Phase 3: Terrain Workspace shell.
