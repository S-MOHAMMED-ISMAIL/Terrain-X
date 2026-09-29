import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { PointerLockControls } from "three/examples/jsm/controls/PointerLockControls.js";
import { ApiError, api } from "@/api/client";
import type { TerrainMetadata } from "@/api/types";
import {
  changeSpeed,
  clearanceDefaults,
  entryPose,
  type FlightParams,
  type FlightReadout,
  type FlightTerrain,
  flightReadout,
  flightTerrainFromMesh,
  groundHeightAt,
  type Heading,
  headingFromDirection,
  stepFlight,
} from "./flythrough";
import {
  buildFlightPath,
  type FlightPath,
  IDLE_PLAYBACK,
  lookAheadDistance,
  type PathWaypoint,
  pathStateAt,
  type PlaybackCommand,
  type PlaybackState,
  type PlaybackStatus,
  waypointWorld,
} from "./flythroughPath";
import {
  browserRecordingEnvironment,
  downloadBlob,
  RECORDING_FPS,
  recordingFileName,
  recordingSupport,
} from "./flythroughRecorder";
import { pixelIndexAt } from "./terrainCoords";
import { calculateRelativeTerrainExaggeration, robustDepthRange } from "./terrainExaggeration";
import { buildTerrainGeometry } from "./terrainMesh";
import { configureTopDownCamera } from "./terrainTopDownCamera";

interface Props {
  projectId: string;
  jobId: string;
  artifactId: string;
  /** Only passed when the RGB source has already been verified (by the
   * caller) to share the DSM's real source pixel dimensions — see
   * TerrainWorkspace.tsx. Never applied as a texture otherwise. */
  compatibleTextureBlob: (() => Promise<Blob>) | null;
  exaggeration: number;
  showGrid: boolean;
  showTexture: boolean;
  firstPerson: boolean;
  onExitFirstPerson: () => void;
  /** "top" is a CAMERA-ONLY change — same mesh/geometry/texture/gamma/sky
   * mask as "perspective" (see terrainTopDownCamera.ts). Controlled by
   * TerrainWorkspace.tsx, which also forces this back to "perspective"
   * whenever firstPerson is switched on; this component defensively does
   * the same at render time (see the animate()/onClick camera selection
   * below) so first-person flythrough can never render/raycast from the
   * top-down camera even transiently. */
  viewMode: "perspective" | "top";
  resetToken: number;
  onSample: (row: number, col: number) => void;
  /** Fired at most once per real artifact load, only for an UNCALIBRATED
   * relative-depth terrain (never for calibrated DSM/metric elevation —
   * see calculateRelativeTerrainExaggeration's docstring), with a
   * data-driven initial exaggeration so relative-depth terrain doesn't
   * default to looking flat. TerrainWorkspace.tsx decides whether/how to
   * apply it (and never re-applies it for the same artifact, so a later
   * user slider adjustment or a 2D<->3D remount is never clobbered). */
  onAutoRelativeExaggeration: (value: number) => void;
  /** P1-8: while true, a 3D click adds a waypoint (grid-local x, z) instead
   * of sampling. */
  waypointMode?: boolean;
  onWaypoint?: (localX: number, localZ: number) => void;
  /** P1-8: waypoints of the flythrough path; the flown path is built here
   * from the rendered flight surface (flythroughPath.ts). */
  waypoints?: PathWaypoint[];
  playbackCommand?: PlaybackCommand | null;
  /** Playback speed multiplier (x the P1-7 default flight speed). */
  playbackSpeed?: number;
  onPlaybackStatus?: (status: PlaybackStatus) => void;
}

/** A real Three.js terrain viewer. The mesh is built exclusively from the
 * actual DSM height grid (see terrainMesh.ts / api.getTerrainGrid) — never
 * generated mathematically. See docs/ARCHITECTURE.md §3.5. */
export function TerrainView3D({
  projectId,
  jobId,
  artifactId,
  compatibleTextureBlob,
  exaggeration,
  showGrid,
  showTexture,
  firstPerson,
  onExitFirstPerson,
  viewMode,
  resetToken,
  onSample,
  onAutoRelativeExaggeration,
  waypointMode = false,
  onWaypoint,
  waypoints = NO_WAYPOINTS,
  playbackCommand = null,
  playbackSpeed = 1,
  onPlaybackStatus,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [pointerLockHint, setPointerLockHint] = useState(false);
  // D2: whether an RGB texture is actually on the material right now.
  const [textureApplied, setTextureApplied] = useState(false);
  // Dev-only diagnostic (see the overlay in the JSX below, gated on
  // import.meta.env.DEV) — makes the real coordinate convention, sky-mask
  // exclusion rate, and elevation range visible for debugging without any
  // production-facing UI. Never itself alters what gets rendered.
  const [diagnostics, setDiagnostics] = useState<{
    heightKind: string;
    width: number;
    height: number;
    validPercent: number;
    excludedPercent: number;
    minElevation: number;
    maxElevation: number;
  } | null>(null);
  // Real, reactive mirror of PointerLockControls' own internal `isLocked`
  // flag (Three.js mutates that object directly — it never triggers a
  // React re-render on its own). Found by real browser acceptance testing:
  // the hint overlay below previously read `sceneRef.current?.pointerLock
  // .isLocked` straight in JSX, so React had no reason to ever re-evaluate
  // it once lock state changed — the "Click to enter flythrough mode" hint
  // (with its 50%-opacity black overlay) stayed on screen, dimming and
  // obscuring the whole view, for the entire real flythrough session.
  const [isPointerLocked, setIsPointerLocked] = useState(false);
  const onExitFirstPersonRef = useRef(onExitFirstPerson);
  onExitFirstPersonRef.current = onExitFirstPerson;
  // Same stale-closure protection as onExitFirstPersonRef above, for the
  // same reason: the click handler that calls this is registered once (see
  // the main scene-setup effect's dependency array below, intentionally
  // excluding `onSample` to avoid tearing down and rebuilding the whole
  // Three.js scene on every unrelated parent re-render), so it must read
  // the current callback through a ref rather than closing over the
  // `onSample` prop directly — otherwise a measurement-mode switch after
  // the scene was first built would silently never be observed by a real
  // click here, exactly like the analogous MapView2D.tsx bug this mirrors.
  // P1-8: always-current mirrors for the once-registered handlers.
  const waypointModeRef = useRef(waypointMode);
  waypointModeRef.current = waypointMode;
  const onWaypointRef = useRef(onWaypoint);
  onWaypointRef.current = onWaypoint;
  const waypointsRef = useRef(waypoints);
  waypointsRef.current = waypoints;
  const playbackSpeedRef = useRef(playbackSpeed);
  playbackSpeedRef.current = playbackSpeed;
  const onPlaybackStatusRef = useRef(onPlaybackStatus);
  onPlaybackStatusRef.current = onPlaybackStatus;
  const [playbackView, setPlaybackView] = useState<PlaybackStatus>(IDLE_PLAYBACK);
  const onSampleRef = useRef(onSample);
  onSampleRef.current = onSample;
  // Same stale-closure protection, for the raw raycast click handler's own
  // `if (!s || firstPerson) return;` guard below — that handler is
  // registered once in the same mount/artifact-dependent effect, so
  // without this ref it would keep checking whatever `firstPerson` was at
  // scene setup (normally `false`) forever, regardless of later real
  // first-person entry/exit.
  const firstPersonRef = useRef(firstPerson);
  firstPersonRef.current = firstPerson;
  // Same stale-closure protection, for the render loop's/onClick's own
  // camera selection below — both are set up once in the mount effect, so
  // without this ref they would keep rendering/raycasting from whichever
  // camera was active at scene setup (always "perspective") regardless of
  // later real view-mode toggles.
  const viewModeRef = useRef(viewMode);
  viewModeRef.current = viewMode;

  const sceneRef = useRef<{
    scene: THREE.Scene;
    camera: THREE.PerspectiveCamera;
    /** A second, real camera used ONLY to look at the exact same mesh from
     * directly above — never a different/rotated/mirrored geometry (see
     * terrainTopDownCamera.ts). Orthographic so the top-down view reads as
     * a true, undistorted map rather than a perspective-foreshortened
     * aerial photo. */
    topCamera: THREE.OrthographicCamera;
    renderer: THREE.WebGLRenderer;
    orbit: OrbitControls;
    pointerLock: PointerLockControls;
    mesh: THREE.Mesh;
    grid: THREE.GridHelper;
    metadata: TerrainMetadata;
    elevations: Float32Array;
    halfWidth: number;
    halfHeight: number;
    minElevation: number;
    maxElevation: number;
    keysDown: Set<string>;
    raycaster: THREE.Raycaster;
    animationFrame: number;
    // P1-7 flythrough: the flight surface is the rendered mesh itself (see
    // flythrough.ts), rebuilt whenever the geometry is.
    flight: FlightTerrain;
    flightExaggeration: number;
    flightParams: FlightParams | null;
    defaultSpeed: number;
    heading: Heading;
    preFlight: { position: THREE.Vector3; target: THREE.Vector3 } | null;
    hudUpdatedAt: number;
    // P1-8 waypoint flythrough.
    path: FlightPath | null;
    pathReason: string | null;
    pathVisual: THREE.Group;
    playback: {
      state: PlaybackState;
      time: number; // path time at 1x, seconds
      baseSpeed: number; // world units per second at 1x
      pre: { position: THREE.Vector3; target: THREE.Vector3 } | null;
      recorder: MediaRecorder | null;
      discard: boolean;
      message: string | null;
      reportedAt: number;
    };
  } | null>(null);
  // P1-7: the flythrough HUD (updated ~10x/s while pointer-locked).
  const [hud, setHud] = useState<FlightReadout | null>(null);

  const fitCamera = () => {
    const s = sceneRef.current;
    if (!s) return;
    const distance = Math.max(s.halfWidth, s.halfHeight, 10) * 1.8;
    const height = Math.max(s.maxElevation - s.minElevation, distance * 0.4);
    if (s.metadata.height_kind === "relative_depth") {
      // A relative-depth grid from a normal landscape/drone photo is
      // usually much WIDER than it is deep (row/Z only spans image
      // height, col/X spans image width) — the calibrated formula below,
      // which looks from a fixed 45-degree azimuth regardless of aspect
      // ratio, makes a wide-but-shallow mesh render as a thin diagonal
      // ribbon rather than a readable "foreground -> valley -> distant
      // terrain" view (found via real browser verification against an
      // actual aerial photo). `distance`/`height` above are unchanged
      // (same safe sizing from the mesh's largest extent, so the whole
      // mesh still fits in view) — only the X/Z ratio changes, favoring
      // the depth axis (+Z, the mesh's own near/foreground side per
      // terrainMesh.ts's row->Z convention) so foreground, valley, and
      // distant terrain appear in sequence instead of edge-on. Never
      // applied to calibrated DSM/metric elevation — see the `else`
      // branch, byte-for-byte the original formula.
      s.camera.position.set(distance * 0.25, height + distance * 0.5, distance * 0.95);
      s.orbit.target.set(0, 0, 0);
      s.camera.near = Math.max(distance / 1000, 0.1);
      s.camera.far = distance * 20;
    } else {
      s.camera.position.set(distance * 0.7, height + distance * 0.5, distance * 0.7);
      s.orbit.target.set(0, 0, 0);
      s.camera.near = Math.max(distance / 1000, 0.1);
      s.camera.far = distance * 20;
    }
    s.camera.updateProjectionMatrix();
    s.orbit.update();
  };

  // Frames the SAME real mesh's full footprint from directly above — never
  // a different geometry, never rotated/mirrored (see
  // terrainTopDownCamera.ts's docstring for the coordinate reasoning and
  // terrainTopDownCamera.test.ts for a deterministic projection check).
  // Uses the mesh's REAL, measured world-space bounding box (not a formula
  // assuming it's centered at the origin) — found by real browser
  // verification that a georeferenced DSM with a negative cell_size_y (a
  // real, common raster convention) leaves the mesh's actual world-Z span
  // asymmetric, which this tight orthographic frustum must account for
  // exactly (unlike the existing perspective camera's much more forgiving
  // distance/FOV, which is why that path never surfaced this).
  const fitTopCamera = () => {
    const s = sceneRef.current;
    const container = containerRef.current;
    if (!s || !container) return;
    const aspect = container.clientWidth / Math.max(container.clientHeight, 1);
    const worldBounds = new THREE.Box3().setFromObject(s.mesh);
    configureTopDownCamera(s.topCamera, worldBounds, aspect);
  };

  // One-time scene setup, re-created whenever the artifact changes.
  useEffect(() => {
    if (!containerRef.current) return;
    let disposed = false;
    setLoading(true);
    setError(null);

    const container = containerRef.current;
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x05070a);

    const camera = new THREE.PerspectiveCamera(
      60,
      container.clientWidth / Math.max(container.clientHeight, 1),
      0.1,
      10000,
    );
    // Real frustum values are set per-artifact by fitTopCamera() below —
    // this placeholder is never rendered from before that runs (viewMode
    // starts as "perspective" and TerrainWorkspace.tsx never lets the user
    // switch to "top" while loading).
    const topCamera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 10000);

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(container.clientWidth, container.clientHeight);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    container.appendChild(renderer.domElement);

    scene.add(new THREE.AmbientLight(0xffffff, 0.6));
    const sun = new THREE.DirectionalLight(0xffffff, 0.8);
    sun.position.set(1, 1.5, 1);
    scene.add(sun);

    const orbit = new OrbitControls(camera, renderer.domElement);
    orbit.enableDamping = true;
    const pointerLock = new PointerLockControls(camera, renderer.domElement);
    // Real 'lock'/'unlock' events PointerLockControls actually fires (tied
    // to the browser's own `pointerlockchange`) — used to keep
    // `isPointerLocked` (real React state) in sync, rather than reading
    // Three's internal `.isLocked` directly in JSX (see that state's own
    // comment for why that silently never worked).
    // P1-7: entering flythrough places the camera at the deterministic
    // entry pose (flythrough.ts::entryPose); leaving restores the exact
    // orbit view the user had before, so orbit behaviour is unchanged.
    const handlePointerLock = () => {
      setIsPointerLocked(true);
      const s = sceneRef.current;
      if (!s) return;
      s.preFlight = { position: camera.position.clone(), target: orbit.target.clone() };
      const defaults = clearanceDefaults(s.flight, s.metadata.height_kind, s.flightExaggeration);
      s.defaultSpeed = defaults.speed;
      s.flightParams = {
        speed: defaults.speed,
        minClearance: defaults.minClearance,
        follow: false,
        followClearance: defaults.entryClearance,
      };
      const pose = entryPose(s.flight, defaults.entryClearance);
      camera.position.set(pose.position.x, pose.position.y, pose.position.z);
      camera.lookAt(pose.lookAt.x, pose.lookAt.y, pose.lookAt.z);
      s.heading = headingFromDirection(
        pose.lookAt.x - pose.position.x,
        pose.lookAt.z - pose.position.z,
        s.heading,
      );
      s.hudUpdatedAt = 0;
    };
    const handlePointerUnlock = () => {
      setIsPointerLocked(false);
      const s = sceneRef.current;
      if (s?.preFlight) {
        camera.position.copy(s.preFlight.position);
        orbit.target.copy(s.preFlight.target);
        camera.lookAt(s.preFlight.target);
        orbit.update();
        s.preFlight = null;
      }
      if (s) s.flightParams = null;
      setHud(null);
      onExitFirstPersonRef.current();
    };
    pointerLock.addEventListener("lock", handlePointerLock);
    pointerLock.addEventListener("unlock", handlePointerUnlock);

    // P1-8: flythrough path line + waypoint markers.
    const pathVisual = new THREE.Group();
    scene.add(pathVisual);

    const grid = new THREE.GridHelper(1, 1);
    grid.visible = false;
    scene.add(grid);

    const raycaster = new THREE.Raycaster();

    async function load() {
      try {
        const metadata = await api.getTerrainMetadata(projectId, jobId, artifactId);
        const buffer = await api.getTerrainGrid(projectId, jobId, artifactId);
        if (disposed) return;
        const elevations = new Float32Array(buffer);

        // Uncalibrated relative-depth terrain: auto-scale a data-driven
        // initial visual exaggeration once per real artifact load (never
        // for calibrated DSM/metric elevation). This only decides WHICH
        // multiplier to use for presentation — buildTerrainGeometry below
        // still receives every real elevation value unmodified/unclipped;
        // robustDepthRange (P5/P95) is used only to pick a sensible
        // baseline, not to alter what's actually rendered.
        let effectiveExaggeration = exaggeration;
        if (metadata.height_kind === "relative_depth") {
          const cellX = Math.abs(metadata.cell_size_x ?? 1);
          const cellY = Math.abs(metadata.cell_size_y ?? 1);
          const terrainWidth = metadata.width * cellX;
          const terrainHeight = metadata.height * cellY;
          const { min, max } = robustDepthRange(elevations);
          effectiveExaggeration = calculateRelativeTerrainExaggeration(
            min,
            max,
            terrainWidth,
            terrainHeight,
          );
          onAutoRelativeExaggeration(effectiveExaggeration);
        }

        const built = buildTerrainGeometry(metadata, elevations, effectiveExaggeration);
        if (import.meta.env.DEV) {
          const total = metadata.width * metadata.height;
          setDiagnostics({
            heightKind: metadata.height_kind,
            width: metadata.width,
            height: metadata.height,
            validPercent: total > 0 ? (built.validCount / total) * 100 : 0,
            excludedPercent: total > 0 ? (built.nodataCount / total) * 100 : 0,
            minElevation: built.minElevation,
            maxElevation: built.maxElevation,
          });
        }
        const material = new THREE.MeshStandardMaterial({
          vertexColors: true,
          side: THREE.DoubleSide,
          flatShading: false,
        });
        const mesh = new THREE.Mesh(built.geometry, material);
        // Center the mesh under the origin so orbit/reset framing is simple.
        mesh.position.set(-built.halfWidth, 0, -built.halfHeight);
        scene.add(mesh);

        grid.scale.set(Math.max(built.halfWidth, built.halfHeight) * 2, 1, 1);

        sceneRef.current = {
          scene,
          camera,
          topCamera,
          renderer,
          orbit,
          pointerLock,
          mesh,
          grid,
          metadata,
          elevations,
          halfWidth: built.halfWidth,
          halfHeight: built.halfHeight,
          minElevation: built.minElevation,
          maxElevation: built.maxElevation,
          keysDown: new Set(),
          raycaster,
          animationFrame: 0,
          flight: flightTerrainFromMesh(
            built.geometry.getAttribute("position").array,
            elevations,
            {
              width: metadata.width,
              height: metadata.height,
              cellX: metadata.cell_size_x ?? 1,
              cellY: metadata.cell_size_y ?? 1,
            },
            built.halfWidth,
            built.halfHeight,
          ),
          flightExaggeration: effectiveExaggeration,
          flightParams: null,
          defaultSpeed: 0,
          heading: { fx: 0, fz: -1 },
          preFlight: null,
          hudUpdatedAt: 0,
          path: null,
          pathReason: null,
          pathVisual,
          playback: {
            state: "idle",
            time: 0,
            baseSpeed: 0,
            pre: null,
            recorder: null,
            discard: false,
            message: null,
            reportedAt: 0,
          },
        };
        rebuildPath();

        fitCamera();
        fitTopCamera();
        setLoading(false);
      } catch (err) {
        if (disposed) return;
        setError(err instanceof ApiError ? err.message : "Failed to load terrain height grid");
        setLoading(false);
      }
    }
    load();

    function onResize() {
      if (!container) return;
      camera.aspect = container.clientWidth / Math.max(container.clientHeight, 1);
      camera.updateProjectionMatrix();
      renderer.setSize(container.clientWidth, container.clientHeight);
      fitTopCamera();
    }
    const resizeObserver = new ResizeObserver(onResize);
    resizeObserver.observe(container);

    // P1-7 flight keys. While pointer-locked their browser defaults (e.g.
    // Space scrolling the page) are suppressed.
    const FLIGHT_KEYS = new Set([
      "KeyW",
      "KeyA",
      "KeyS",
      "KeyD",
      "Space",
      "ShiftLeft",
      "ShiftRight",
      "KeyF",
      "Equal",
      "NumpadAdd",
      "Minus",
      "NumpadSubtract",
    ]);
    function onKeyDown(e: KeyboardEvent) {
      const s = sceneRef.current;
      s?.keysDown.add(e.code);
      if (e.code === "Escape" && s && s.playback.state !== "idle") {
        stopPlayback();
        return;
      }
      if (!s || !pointerLock.isLocked || !s.flightParams) return;
      if (FLIGHT_KEYS.has(e.code)) e.preventDefault();
      const flightParams = s.flightParams;
      if (e.code === "KeyF" && !e.repeat) {
        if (!flightParams.follow) {
          // Terrain-follow starts at the current height above the surface
          // (never below the minimum), so turning it on causes no jump.
          const ground = groundHeightAt(s.flight, camera.position.x, camera.position.z);
          flightParams.followClearance =
            ground === null
              ? flightParams.followClearance
              : Math.max(flightParams.minClearance, camera.position.y - ground);
        }
        flightParams.follow = !flightParams.follow;
        s.hudUpdatedAt = 0;
      } else if (e.code === "Equal" || e.code === "NumpadAdd") {
        flightParams.speed = changeSpeed(flightParams.speed, s.defaultSpeed, 1);
        s.hudUpdatedAt = 0;
      } else if (e.code === "Minus" || e.code === "NumpadSubtract") {
        flightParams.speed = changeSpeed(flightParams.speed, s.defaultSpeed, -1);
        s.hudUpdatedAt = 0;
      }
    }
    function onKeyUp(e: KeyboardEvent) {
      sceneRef.current?.keysDown.delete(e.code);
    }
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);

    // Resolves a 3D click to the real, full-resolution source pixel it
    // corresponds to — see docs/ARCHITECTURE.md §3.7 for the bug this
    // fixes. The raycast hit point lives in the DOWNSAMPLED display grid's
    // own coordinate space (`geospatial/terrain_grid.py`,
    // MAX_TERRAIN_DIMENSION-capped); that grid's own row/col index is NOT
    // interpreted directly as a full-resolution row/col (that was the bug —
    // for any source larger than the display cap, it silently sampled the
    // wrong pixel). Instead:
    //   - Georeferenced: the hit point's grid-local (x, z) plus the grid's
    //     own real origin is a genuine real-world map coordinate, valid
    //     regardless of whether the display grid was reprojected from a
    //     geographic source — resolved against the artifact's own real
    //     full-resolution transform server-side (never a client-side
    //     rescale).
    //   - Not georeferenced: the display grid has cell_size=1/origin=0, so
    //     its own local coordinate IS the grid's pixel index; decimation
    //     uses the same uniform scale factor in both directions
    //     (geospatial/terrain_grid.py), so a proportional rescale by the
    //     real source/grid dimension ratio is exact, not approximate.
    async function onClick(event: MouseEvent) {
      const s = sceneRef.current;
      if (!s || firstPersonRef.current) return;
      // P1-8: the camera belongs to the path while a playback is active.
      if (s.playback.state !== "idle") return;
      const rect = renderer.domElement.getBoundingClientRect();
      const ndc = new THREE.Vector2(
        ((event.clientX - rect.left) / rect.width) * 2 - 1,
        -((event.clientY - rect.top) / rect.height) * 2 + 1,
      );
      // firstPersonRef.current is already false at this point (guarded
      // above), so this only ever picks between the two non-first-person
      // cameras — same real mesh either way, raycasting is unaffected by
      // which one is active (see terrainMesh.ts's coordinate convention;
      // camera choice here never changes hit.point.x/z, only which pixels
      // on screen a given world point happens to fall under).
      const activeCamera: THREE.Camera = viewModeRef.current === "top" ? s.topCamera : camera;
      s.raycaster.setFromCamera(ndc, activeCamera);
      const hits = s.raycaster.intersectObject(s.mesh);
      if (hits.length === 0) return;

      const point = hits[0].point;
      // Undo the mesh's own centering offset (`mesh.position.set(-halfWidth,
      // 0, -halfHeight)`, set above) to recover the real grid-local
      // coordinate `terrainMesh.ts` placed this vertex at.
      const gridLocalX = point.x + s.halfWidth;
      const gridLocalZ = point.z + s.halfHeight;
      if (waypointModeRef.current) {
        onWaypointRef.current?.(gridLocalX, gridLocalZ);
        return;
      }

      if (s.metadata.is_georeferenced) {
        const mapX = (s.metadata.origin_x ?? 0) + gridLocalX;
        const mapY = (s.metadata.origin_y ?? 0) + gridLocalZ;
        try {
          const pixel = await api.getPixelForCoordinate(
            projectId,
            jobId,
            artifactId,
            mapX,
            mapY,
            s.metadata.local_crs,
          );
          if (disposed) return;
          if (pixel.in_bounds) onSampleRef.current(pixel.row, pixel.col);
        } catch {
          // A transient/ownership error here just means no sample for this
          // click — never fabricate a row/col.
        }
      } else {
        // Continuous source-pixel coordinate (pixel i spans [i, i+1), its
        // centre at i + 0.5 — the GLB export's convention) -> the pixel
        // containing it.
        const row = pixelIndexAt(gridLocalZ * (s.metadata.source_height / s.metadata.height), s.metadata.source_height);
        const col = pixelIndexAt(gridLocalX * (s.metadata.source_width / s.metadata.width), s.metadata.source_width);
        onSampleRef.current(row, col);
      }
    }
    renderer.domElement.addEventListener("click", onClick);

    let raf: number;
    const clock = new THREE.Clock();
    function animate() {
      raf = requestAnimationFrame(animate);
      const s = sceneRef.current;
      const delta = clock.getDelta();

      if (s) {
        // pointerLock.isLocked (real first-person flythrough) always wins,
        // regardless of viewModeRef — this is the render-loop half of the
        // "first person must automatically exit top-down mode" requirement
        // (the other half is TerrainWorkspace.tsx forcing viewMode back to
        // "perspective" the moment flythrough is turned on); belt-and-
        // suspenders so first-person is never rendered/moved from the
        // top-down camera even for one transient frame.
        const underPlayback = s.playback.state !== "idle";
        const useTopCamera =
          !pointerLock.isLocked && !underPlayback && viewModeRef.current === "top";
        if (pointerLock.isLocked && s.flightParams) {
          // P1-7: terrain-aware flight (flythrough.ts::stepFlight) — never
          // below ground + minimum clearance anywhere along the step, no
          // tunnelling, nothing invented over no-surface cells.
          const direction = camera.getWorldDirection(new THREE.Vector3());
          s.heading = headingFromDirection(direction.x, direction.z, s.heading);
          const key = (code: string) => (s.keysDown.has(code) ? 1 : 0);
          const input = {
            forward: key("KeyW") - key("KeyS"),
            right: key("KeyD") - key("KeyA"),
            up: key("Space") - Math.max(key("ShiftLeft"), key("ShiftRight")),
          };
          // A long frame (e.g. after a background tab) moves at most 0.25 s
          // of flight: a presentation choice only — stepFlight is exact for
          // any dt.
          const next = stepFlight(
            s.flight,
            camera.position,
            s.heading,
            input,
            Math.min(delta, 0.25),
            s.flightParams,
          );
          camera.position.set(next.position.x, next.position.y, next.position.z);
          s.flightParams.followClearance = next.followClearance;
          const now = performance.now();
          if (now - s.hudUpdatedAt >= 100) {
            s.hudUpdatedAt = now;
            setHud(
              flightReadout(s.flight, camera.position, s.heading, s.flightParams, {
                kind: s.metadata.height_kind,
                exaggeration: s.flightExaggeration,
                isGeoreferenced: s.metadata.is_georeferenced,
                localCrs: s.metadata.local_crs,
                originX: s.metadata.origin_x,
                originY: s.metadata.origin_y,
                sourceWidth: s.metadata.source_width,
                sourceHeight: s.metadata.source_height,
              }),
            );
          }
        } else if (underPlayback && s.path) {
          // P1-8: deterministic playback along the flown polyline.
          const pb = s.playback;
          const recording = pb.recorder !== null;
          const multiplier = recording ? 1 : playbackSpeedRef.current;
          if (pb.state === "playing") pb.time += Math.min(delta, 0.25) * multiplier;
          const st = pathStateAt(
            s.path,
            pb.baseSpeed * pb.time,
            lookAheadDistance(s.path, pb.baseSpeed * multiplier),
          );
          camera.position.set(st.position.x, st.position.y, st.position.z);
          camera.lookAt(st.lookAt.x, st.lookAt.y, st.lookAt.z);
          if (pb.state === "playing" && st.finished) {
            pb.state = "finished";
            pb.time = s.path.total / pb.baseSpeed;
            if (pb.recorder && pb.recorder.state !== "inactive") pb.recorder.stop();
            pb.reportedAt = 0;
          }
          const now = performance.now();
          if (now - pb.reportedAt >= 100) {
            pb.reportedAt = now;
            const defaults = clearanceDefaults(
              s.flight,
              s.metadata.height_kind,
              s.flightExaggeration,
            );
            setHud(
              flightReadout(
                s.flight,
                camera.position,
                st.heading,
                {
                  speed: pb.baseSpeed * multiplier,
                  minClearance: defaults.minClearance,
                  follow: false,
                  followClearance: s.path.clearance,
                },
                {
                  kind: s.metadata.height_kind,
                  exaggeration: s.flightExaggeration,
                  isGeoreferenced: s.metadata.is_georeferenced,
                  localCrs: s.metadata.local_crs,
                  originX: s.metadata.origin_x,
                  originY: s.metadata.origin_y,
                  sourceWidth: s.metadata.source_width,
                  sourceHeight: s.metadata.source_height,
                },
              ),
            );
            reportPlayback();
          }
        } else if (!useTopCamera) {
          orbit.update();
        }
        renderer.render(scene, useTopCamera ? s.topCamera : camera);
      }
    }
    animate();

    return () => {
      disposed = true;
      cancelAnimationFrame(raf);
      resizeObserver.disconnect();
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      renderer.domElement.removeEventListener("click", onClick);
      const current = sceneRef.current;
      if (current?.playback.recorder && current.playback.recorder.state !== "inactive") {
        current.playback.discard = true;
        current.playback.recorder.stop();
      }
      orbit.dispose();
      pointerLock.removeEventListener("lock", handlePointerLock);
      pointerLock.removeEventListener("unlock", handlePointerUnlock);
      pointerLock.disconnect();
      const s = sceneRef.current;
      if (s) {
        s.mesh.geometry.dispose();
        (s.mesh.material as THREE.Material).dispose();
      }
      renderer.dispose();
      if (renderer.domElement.parentElement === container) {
        container.removeChild(renderer.domElement);
      }
      sceneRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, jobId, artifactId]);

  // Rebuild geometry (Z only) when exaggeration changes — no refetch.
  useEffect(() => {
    const s = sceneRef.current;
    if (!s) return;
    const rebuilt = buildTerrainGeometry(s.metadata, s.elevations, exaggeration);
    s.mesh.geometry.dispose();
    s.mesh.geometry = rebuilt.geometry;
    // P1-7: flight follows the surface actually rendered.
    s.flight = flightTerrainFromMesh(
      rebuilt.geometry.getAttribute("position").array,
      s.elevations,
      {
        width: s.metadata.width,
        height: s.metadata.height,
        cellX: s.metadata.cell_size_x ?? 1,
        cellY: s.metadata.cell_size_y ?? 1,
      },
      rebuilt.halfWidth,
      rebuilt.halfHeight,
    );
    s.flightExaggeration = exaggeration;
    if (s.flightParams) {
      const defaults = clearanceDefaults(s.flight, s.metadata.height_kind, exaggeration);
      s.flightParams.minClearance = defaults.minClearance;
      s.flightParams.followClearance = Math.max(
        s.flightParams.followClearance,
        defaults.minClearance,
      );
    }
    // P1-8: the flown path follows the rebuilt surface.
    rebuildPath();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [exaggeration]);

  // ------------------------------------------------------------------------
  // P1-8: waypoint flythrough — path, playback, recording
  // ------------------------------------------------------------------------

  /** Reports playback/path status to the view and the workspace controls. */
  function reportPlayback() {
    const s = sceneRef.current;
    if (!s) return;
    const pb = s.playback;
    const isElevation = s.metadata.height_kind === "elevation";
    const scale = isElevation ? s.flightExaggeration : 1;
    const duration = s.path && pb.baseSpeed > 0 ? s.path.total / pb.baseSpeed : 0;
    const finished = pb.state === "finished";
    const status: PlaybackStatus = {
      state: pb.state,
      // Terminal status is exact. Recomputing total / speed through floating-
      // point multiplication can otherwise expose 0.9999999999999999 after
      // playback has already reached the final waypoint.
      progress: finished ? 1 : s.path && s.path.total > 0 ? Math.min(1, (pb.baseSpeed * pb.time) / s.path.total) : 0,
      elapsed: finished ? duration : Math.min(pb.time, duration),
      duration,
      recording: pb.recorder !== null,
      pathOk: s.path !== null,
      pathReason: s.pathReason,
      clearance: s.path ? s.path.clearance / scale : null,
      clearanceUnits: isElevation ? "reference units" : "visual units (relative, not a distance)",
      message: pb.message,
    };
    setPlaybackView(status);
    onPlaybackStatusRef.current?.(status);
  }

  /** Builds the flown path from the RENDERED flight surface and redraws it.
   * A path change during playback restarts it from the beginning. */
  function rebuildPath() {
    const s = sceneRef.current;
    if (!s) return;
    const defaults = clearanceDefaults(s.flight, s.metadata.height_kind, s.flightExaggeration);
    const result =
      waypointsRef.current.length === 0
        ? ({ ok: false, reason: null } as const)
        : buildFlightPath(
            s.flight,
            waypointsRef.current,
            defaults.entryClearance,
            s.flightExaggeration,
          );
    s.path = result.ok ? result.path : null;
    s.pathReason = result.ok ? null : result.reason;
    s.playback.baseSpeed = defaults.speed;

    for (const child of [...s.pathVisual.children]) {
      s.pathVisual.remove(child);
      const obj = child as THREE.Mesh | THREE.Line;
      obj.geometry.dispose();
      (obj.material as THREE.Material).dispose();
    }
    const cell = Math.min(Math.abs(s.flight.cellX), Math.abs(s.flight.cellY));
    waypointsRef.current.forEach((w, i) => {
      const p = waypointWorld(s.flight, w);
      const y = s.path
        ? pathStateAt(s.path, s.path.waypointS[i], 0).position.y
        : (groundHeightAt(s.flight, p.x, p.z) ?? 0) + defaults.entryClearance;
      const marker = new THREE.Mesh(
        new THREE.SphereGeometry(Math.max(cell * 0.6, 0.5), 12, 8),
        new THREE.MeshBasicMaterial({ color: 0xf59e0b }),
      );
      marker.position.set(p.x, y, p.z);
      s.pathVisual.add(marker);
    });
    if (s.path) {
      const positions = new Float32Array(s.path.x.length * 3);
      for (let k = 0; k < s.path.x.length; k++) {
        positions[k * 3] = s.path.x[k];
        positions[k * 3 + 1] = s.path.y[k];
        positions[k * 3 + 2] = s.path.z[k];
      }
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
      s.pathVisual.add(new THREE.Line(geometry, new THREE.LineBasicMaterial({ color: 0xfbbf24 })));
    }

    if (s.playback.state !== "idle") {
      if (!s.path) stopPlayback();
      else {
        s.playback.time = 0;
        s.playback.state = "playing";
      }
    }
    reportPlayback();
  }

  /** Stops playback (discarding any recording in progress) and restores the
   * orbit view the user had before it started. */
  function stopPlayback() {
    const s = sceneRef.current;
    if (!s) return;
    const pb = s.playback;
    if (pb.recorder && pb.recorder.state !== "inactive") {
      pb.discard = true;
      pb.recorder.stop();
    }
    if (pb.pre) {
      s.camera.position.copy(pb.pre.position);
      s.orbit.target.copy(pb.pre.target);
      s.camera.lookAt(pb.pre.target);
      s.orbit.enabled = viewModeRef.current === "perspective" && !firstPersonRef.current;
      s.orbit.update();
      pb.pre = null;
    }
    pb.state = "idle";
    pb.time = 0;
    setHud(null);
    reportPlayback();
  }

  /** Starts a MediaRecorder on the 3D canvas. Returns an error, or null. */
  function startRecording(): string | null {
    const s = sceneRef.current;
    if (!s) return "The 3D view is not ready.";
    const support = recordingSupport(browserRecordingEnvironment());
    if (!support.supported) return support.reason;
    let stream: MediaStream;
    let recorder: MediaRecorder;
    try {
      stream = s.renderer.domElement.captureStream(RECORDING_FPS);
      recorder = new MediaRecorder(stream, { mimeType: support.mimeType });
    } catch (err) {
      return `Recording could not start: ${err instanceof Error ? err.message : String(err)}`;
    }
    const chunks: Blob[] = [];
    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunks.push(event.data);
    };
    recorder.onerror = () => {
      s.playback.discard = true;
      s.playback.message = "Recording failed; no file was produced.";
    };
    recorder.onstop = () => {
      stream.getTracks().forEach((track) => track.stop());
      const discard = s.playback.discard;
      s.playback.recorder = null;
      s.playback.discard = false;
      if (!discard && chunks.length > 0) {
        downloadBlob(new Blob(chunks, { type: support.mimeType }), recordingFileName(artifactId));
        s.playback.message = "Recording saved.";
      } else if (discard && !s.playback.message) {
        s.playback.message = "Recording cancelled; no file was produced.";
      }
      reportPlayback();
    };
    recorder.start(250);
    s.playback.recorder = recorder;
    s.playback.discard = false;
    return null;
  }

  // Waypoints changed -> rebuild the flown path.
  useEffect(() => {
    rebuildPath();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [waypoints]);

  // Entering first-person flythrough stops any playback.
  useEffect(() => {
    if (firstPerson && sceneRef.current?.playback.state !== "idle") stopPlayback();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [firstPerson]);

  // Playback commands from the workspace controls.
  useEffect(() => {
    const s = sceneRef.current;
    if (!s || !playbackCommand) return;
    const pb = s.playback;
    pb.message = null;
    const begin = () => {
      if (pb.state === "idle") {
        pb.pre = { position: s.camera.position.clone(), target: s.orbit.target.clone() };
        s.orbit.enabled = false;
      }
      pb.time = 0;
      pb.state = "playing";
    };
    switch (playbackCommand.type) {
      case "play":
      case "record": {
        if (!s.path) {
          pb.message = s.pathReason ?? "No valid path.";
          break;
        }
        if (firstPersonRef.current) {
          pb.message = "Exit flythrough mode before playing a path.";
          break;
        }
        if (pb.recorder) break;
        if (playbackCommand.type === "record") {
          const error = startRecording();
          if (error) {
            pb.message = error;
            break;
          }
        }
        begin();
        break;
      }
      case "pause":
        if (pb.state === "playing") pb.state = "paused";
        break;
      case "resume":
        if (pb.state === "paused") pb.state = "playing";
        break;
      case "restart":
        if (pb.state !== "idle" && !pb.recorder) begin();
        break;
      case "stop":
        stopPlayback();
        return;
    }
    pb.reportedAt = 0;
    reportPlayback();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playbackCommand?.id]);

  useEffect(() => {
    if (sceneRef.current) sceneRef.current.grid.visible = showGrid;
  }, [showGrid]);

  useEffect(() => {
    const s = sceneRef.current;
    if (!s) return;
    const material = s.mesh.material as THREE.MeshStandardMaterial;
    if (!showTexture || !compatibleTextureBlob) {
      material.map = null;
      material.vertexColors = true;
      material.needsUpdate = true;
      setTextureApplied(false);
      return;
    }
    let cancelled = false;
    compatibleTextureBlob().then((blob) => {
      if (cancelled) return;
      const url = URL.createObjectURL(blob);
      new THREE.TextureLoader().load(url, (texture) => {
        texture.colorSpace = THREE.SRGBColorSpace;
        if (cancelled) {
          texture.dispose();
          URL.revokeObjectURL(url);
          return;
        }
        material.map = texture;
        material.vertexColors = false;
        material.needsUpdate = true;
        setTextureApplied(true);
        URL.revokeObjectURL(url);
      });
    });
    return () => {
      cancelled = true;
    };
    // `loading` is a real, deliberate dependency: the
    // scene (`sceneRef.current`) is built asynchronously in a separate
    // effect's `load()` call, so THIS effect's very first run (synchronous,
    // on mount) always sees `sceneRef.current === null` and no-ops via the
    // early return above. Found by real browser acceptance testing — the
    // RGB texture never actually appeared on initial load (silently
    // staying on the vertex-color fallback) for either the calibrated or
    // relative-depth path, because nothing was re-triggering this effect
    // once the scene actually finished loading. Re-running it when
    // `loading` flips to `false` is what makes the already-correct
    // fetch/apply logic above actually execute against a real scene.
  }, [showTexture, compatibleTextureBlob, loading]);

  useEffect(() => {
    if (resetToken > 0) {
      fitCamera();
      fitTopCamera();
    }

  }, [resetToken]);

  // Re-fit the top-down camera whenever it becomes the active view — picks
  // up any container resize/footprint that happened while "perspective"
  // was active, rather than only ever fitting once at initial load.
  useEffect(() => {
    if (viewMode === "top") fitTopCamera();
  }, [viewMode]);

  useEffect(() => {
    const s = sceneRef.current;
    if (!s) return;
    if (firstPerson) {
      s.orbit.enabled = false;
      setPointerLockHint(true);
    } else {
      s.pointerLock.unlock();
      // Orbit only drives the perspective camera — leave it disabled while
      // the fixed, non-orbiting top-down camera is the one being rendered
      // (see animate() above), so a mouse drag over the top view can never
      // silently reposition the perspective camera the user isn't looking
      // at, only to surprise them on switching back.
      s.orbit.enabled = viewMode === "perspective";
      setPointerLockHint(false);
    }
  }, [firstPerson, viewMode]);

  return (
    <div className="relative h-full w-full">
      <div
        ref={containerRef}
        data-testid="terrain-3d-canvas"
        data-texture-applied={textureApplied ? "true" : "false"}
        className="h-full w-full rounded-lg bg-terrain-950"
        onClick={() => {
          if (firstPerson) sceneRef.current?.pointerLock.lock();
        }}
      />
      {loading && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-terrain-950/80 text-sm text-slate-300 animate-fade-in">
          <svg
            className="h-5 w-5 animate-spin text-brand-400"
            viewBox="0 0 24 24"
            fill="none"
            aria-hidden="true"
          >
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          Loading terrain height grid…
        </div>
      )}
      {import.meta.env.DEV && diagnostics && !loading && (
        <div className="pointer-events-none absolute bottom-2 right-2 z-10 rounded-md bg-black/70 px-2 py-1.5 font-mono text-metadata leading-snug text-slate-300">
          <div>height_kind: {diagnostics.heightKind}</div>
          <div>
            grid: {diagnostics.width}×{diagnostics.height} (row0=top→Z=0, col0=left→X=0)
          </div>
          <div>valid: {diagnostics.validPercent.toFixed(1)}%</div>
          <div>excluded (nodata/sky): {diagnostics.excludedPercent.toFixed(1)}%</div>
          <div>
            elevation range: {diagnostics.minElevation.toFixed(3)}..{diagnostics.maxElevation.toFixed(3)}
          </div>
        </div>
      )}
      {error && (
        <div className="absolute inset-0 flex items-center justify-center bg-terrain-950/95 p-4 text-center text-sm text-red-300 animate-fade-in">
          {error}
        </div>
      )}
      {firstPerson && pointerLockHint && !isPointerLocked && (
        // pointer-events-none: this is a purely informational overlay. It
        // fully covers the real interactive container div beneath (which
        // owns the actual onClick -> pointerLock.lock() call) via
        // `inset-0`, and — found by real browser acceptance testing, not
        // by inspection — was previously swallowing every click aimed at
        // it, silently preventing a real user from ever entering
        // flythrough mode by clicking this exact hint as instructed.
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-1 bg-black/50 text-center text-sm text-white animate-fade-in">
          <span className="rounded-md bg-white/10 px-3 py-1 font-medium backdrop-blur-sm">
            Click to enter flythrough mode
          </span>
          <span className="text-xs text-slate-300">
            WASD move · mouse look · Space/Shift up/down · F follow terrain · +/- speed · Esc exit
          </span>
        </div>
      )}
      {firstPerson && isPointerLocked && hud && <FlightHud hud={hud} />}
      {playbackView.state !== "idle" && (
        <>
          {hud && <FlightHud hud={hud} />}
          <PlaybackHud status={playbackView} />
        </>
      )}
    </div>
  );
}

function fmt(value: number | null, digits: number): string {
  return value === null ? "—" : value.toFixed(digits);
}

/** P1-7 flythrough HUD. Each value is also carried in `data-value` at full
 * precision (the same number the text shows, unrounded). */
function FlightHud({ hud }: { hud: FlightReadout }) {
  const position =
    hud.mapX !== null && hud.mapY !== null
      ? `${hud.mapX.toFixed(2)}, ${hud.mapY.toFixed(2)}${hud.crs ? ` (${hud.crs})` : ""}`
      : `pixel ${fmt(hud.pixelCol, 1)}, ${fmt(hud.pixelRow, 1)}`;
  return (
    <div
      data-testid="flight-hud"
      className="pointer-events-none absolute right-2 top-2 z-10 max-w-[calc(100%-1rem)] rounded-control bg-slate-950/85 px-2 py-1.5 font-mono text-xs leading-snug text-slate-100 shadow"
    >
      <div className="mb-1 font-sans text-metadata font-semibold uppercase text-amber-300">Flythrough active</div>
      <div
        data-testid="flight-hud-position"
        data-map-x={hud.mapX ?? ""}
        data-map-y={hud.mapY ?? ""}
        data-crs={hud.crs ?? ""}
        data-pixel-col={hud.pixelCol ?? ""}
        data-pixel-row={hud.pixelRow ?? ""}
      >
        Position: {position}
      </div>
      <div data-testid="flight-hud-ground" data-value={hud.groundValue ?? ""}>
        {hud.groundLabel}: {hud.groundValue === null ? "no surface" : fmt(hud.groundValue, 3)}
      </div>
      <div
        data-testid="flight-hud-clearance"
        data-value={hud.heightAboveGround ?? ""}
        data-min={hud.minClearance}
      >
        Height above ground: {fmt(hud.heightAboveGround, 2)} {hud.clearanceUnits} (min{" "}
        {hud.minClearance.toFixed(2)})
      </div>
      <div data-testid="flight-hud-heading" data-value={hud.headingDeg}>
        Heading: {hud.headingDeg.toFixed(0)}°
      </div>
      <div data-testid="flight-hud-speed" data-value={hud.speed}>
        Speed: {hud.speed.toFixed(2)} {hud.speedUnits}
      </div>
      <div data-testid="flight-hud-follow" data-value={hud.follow ? "on" : "off"}>
        Terrain follow: {hud.follow ? "on" : "off"} (F)
      </div>
    </div>
  );
}

const NO_WAYPOINTS: PathWaypoint[] = [];

/** P1-8 playback status line (full-precision values in data-value). */
function PlaybackHud({ status }: { status: PlaybackStatus }) {
  return (
    <div
      data-testid="playback-hud"
      className="pointer-events-none absolute bottom-2 left-2 z-10 rounded-md bg-black/70 px-2 py-1.5 font-mono text-metadata leading-snug text-slate-200"
    >
      <div data-testid="path-status" data-value={status.state}>
        Path: {status.state}
        {status.recording ? " · recording" : ""}
      </div>
      <div data-testid="path-progress" data-value={status.progress}>
        Progress: {(status.progress * 100).toFixed(1)}%
      </div>
      <div data-testid="path-time" data-value={status.elapsed} data-total={status.duration}>
        Path time: {status.elapsed.toFixed(1)} / {status.duration.toFixed(1)} s (at 1×)
      </div>
      <div data-testid="path-clearance" data-value={status.clearance ?? ""}>
        Target clearance: {status.clearance === null ? "—" : status.clearance.toFixed(2)}{" "}
        {status.clearanceUnits}
      </div>
    </div>
  );
}
