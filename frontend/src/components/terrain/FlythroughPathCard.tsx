import { Button, Card, LiveStatus, ProgressBar, Select } from "@/components/ui";
import type { PathWaypoint, PlaybackCommand, PlaybackStatus } from "./flythroughPath";
import type { RecordingSupport } from "./flythroughRecorder";
import { WorkspaceToolSection } from "./WorkspaceToolSection";

interface Props {
  mode: "2d" | "3d";
  waypointMode: boolean;
  onToggleWaypointMode: () => void;
  waypoints: PathWaypoint[];
  message: string | null;
  loadingTerrain: boolean;
  onUndo: () => void;
  onClear: () => void;
  playback: PlaybackStatus;
  speed: number;
  onSpeedChange: (value: number) => void;
  onCommand: (type: PlaybackCommand["type"]) => void;
  recording: RecordingSupport;
  firstPerson: boolean;
  onDownloadPath: () => void;
}

const SPEEDS = [0.5, 1, 2, 4];

function waypointLabel(w: PathWaypoint): string {
  if (w.mapX !== null && w.mapY !== null) return `${w.mapX.toFixed(2)}, ${w.mapY.toFixed(2)}`;
  return `pixel ${w.pixelCol?.toFixed(1)}, ${w.pixelRow?.toFixed(1)}`;
}

function playbackLabel(playback: PlaybackStatus, firstPerson: boolean): string {
  if (playback.recording) return "Recording";
  if (firstPerson) return "Active";
  if (playback.state === "idle" && playback.pathOk) return "Ready";
  return playback.state.charAt(0).toUpperCase() + playback.state.slice(1);
}

function recordingLabel(playback: PlaybackStatus): string {
  if (playback.recording) return "Recording";
  if (playback.message?.includes("saved")) return "Complete";
  if (playback.message?.toLowerCase().includes("failed")) return "Failed";
  return "Idle";
}

/** P1-8: waypoint flythrough — waypoints (2D map or 3D terrain clicks),
 * path playback and WebM recording. Built from existing card styling. */
export function FlythroughPathCard({
  mode,
  waypointMode,
  onToggleWaypointMode,
  waypoints,
  message,
  loadingTerrain,
  onUndo,
  onClear,
  playback,
  speed,
  onSpeedChange,
  onCommand,
  recording,
  firstPerson,
  onDownloadPath,
}: Props) {
  const idle = playback.state === "idle";
  const canStart = playback.pathOk && !firstPerson && (idle || playback.state === "finished");
  return (
    <div data-testid="flythrough-path-card">
      <Card padding="sm" className="text-xs text-slate-600">
        <WorkspaceToolSection
          title="Flythrough"
          status={playbackLabel(playback, firstPerson)}
          tone={playback.recording ? "danger" : playback.state === "playing" || firstPerson ? "brand" : "neutral"}
          description="Navigate in first person or build a terrain-following path."
        >
          <dl className="mb-3 grid grid-cols-2 gap-x-3 gap-y-1 rounded-control bg-slate-50 p-2 text-metadata">
            <div><dt className="text-slate-500">Navigation</dt><dd className="font-mono text-slate-800">W A S D / Space / Shift</dd></div>
            <div><dt className="text-slate-500">Path speed</dt><dd className="font-medium text-slate-800">{speed}x</dd></div>
            <div><dt className="text-slate-500">Terrain follow</dt><dd className="font-medium text-slate-800">On during path playback</dd></div>
            <div><dt className="text-slate-500">Clearance</dt><dd className="font-mono text-slate-800">{playback.clearance === null ? "Unavailable" : `${playback.clearance.toFixed(2)} ${playback.clearanceUnits}`}</dd></div>
          </dl>
        </WorkspaceToolSection>

        <WorkspaceToolSection
          title="Waypoints and path"
          status={waypointMode ? "Editing" : `${waypoints.length} waypoint${waypoints.length === 1 ? "" : "s"}`}
          tone={waypointMode ? "brand" : playback.pathOk ? "success" : "neutral"}
          description="Path validity and clearance come from the rendered terrain surface."
        >
        <div className="mb-2 flex flex-wrap gap-1.5">
          <Button size="sm" variant={waypointMode ? "primary" : "secondary"} aria-pressed={waypointMode} onClick={onToggleWaypointMode} disabled={!idle}>
            {waypointMode ? "Stop adding waypoints" : "Add waypoints"}
          </Button>
          <Button size="sm" variant="ghost" onClick={onUndo} disabled={!idle || waypoints.length === 0}>Undo waypoint</Button>
          <Button size="sm" variant="ghost" onClick={onClear} disabled={!idle || waypoints.length === 0}>Clear waypoints</Button>
        </div>
        {waypointMode && (
          <LiveStatus className="mb-1 text-metadata text-amber-800">
            Click the {mode === "2d" ? "map" : "terrain"} to add a waypoint
            {loadingTerrain ? " (loading terrain…)" : ""}.
          </LiveStatus>
        )}
        {message && (
          <p data-testid="waypoint-message" className="mb-1 text-metadata text-red-600">
            {message}
          </p>
        )}
        <ol data-testid="waypoint-list" className="mb-2 list-decimal pl-5 text-metadata">
          {waypoints.map((w, i) => (
            <li
              key={i}
              data-map-x={w.mapX ?? ""}
              data-map-y={w.mapY ?? ""}
              data-pixel-col={w.pixelCol ?? ""}
              data-pixel-row={w.pixelRow ?? ""}
              data-local-x={w.localX}
              data-local-z={w.localZ}
            >
              {waypointLabel(w)}
            </li>
          ))}
        </ol>
        <p data-testid="path-summary" className={`mb-2 rounded-control border px-2 py-1.5 text-metadata ${playback.pathOk ? "border-green-200 bg-green-50 text-green-800" : "border-slate-200 bg-slate-50 text-slate-600"}`}>
          {playback.pathOk
            ? `Path ready: ${playback.duration.toFixed(1)} s at 1×, ${
                playback.clearance?.toFixed(2) ?? "—"
              } ${playback.clearanceUnits} above the rendered (display-grid) surface.`
            : (playback.pathReason ?? "Add at least two waypoints.")}
        </p>

        </WorkspaceToolSection>

        <WorkspaceToolSection
          title="Playback and recording"
          status={recordingLabel(playback)}
          tone={playback.recording ? "danger" : playback.message?.includes("saved") ? "success" : "neutral"}
          description={mode === "3d" ? "Playback uses the current valid path." : "Switch to 3D Terrain to play or record."}
        >
        {mode === "3d" ? (
          <>
            <div className="mb-2">
              <div className="mb-1 flex justify-between text-metadata text-slate-500">
                <span>{playback.state}</span>
                <span data-testid="playback-progress-text">{Math.round(playback.progress * 100)}%</span>
              </div>
              <ProgressBar value={playback.progress * 100} />
            </div>
            <div className="mb-2 flex flex-wrap gap-1.5">
              {playback.state === "playing" ? (
                <Button size="sm" variant="secondary" onClick={() => onCommand("pause")}>Pause</Button>
              ) : playback.state === "paused" ? (
                <Button size="sm" variant="secondary" onClick={() => onCommand("resume")}>Resume</Button>
              ) : (
                <Button size="sm" variant="secondary" onClick={() => onCommand("play")} disabled={!canStart}>Play path</Button>
              )}
              <Button size="sm" variant="ghost" onClick={() => onCommand("restart")} disabled={idle || playback.recording}>Restart</Button>
              <Button size="sm" variant="ghost" onClick={() => onCommand("stop")} disabled={idle}>Stop</Button>
              <label className="flex items-center gap-1">
                Speed
                <Select
                  aria-label="Playback speed"
                  value={speed}
                  onChange={(e) => onSpeedChange(Number(e.target.value))}
                  disabled={playback.recording}
                  className="h-8 min-h-8 py-1"
                >
                  {SPEEDS.map((v) => (
                    <option key={v} value={v}>
                      {v}×
                    </option>
                  ))}
                </Select>
              </label>
            </div>
            <div className="flex flex-wrap items-center gap-1">
              <Button size="sm" variant={playback.recording ? "destructive" : "secondary"} onClick={() => onCommand(playback.recording ? "stop" : "record")} disabled={!playback.recording && (!recording.supported || !canStart)}>
                {playback.recording ? "Stop recording" : "Record flythrough (WebM)"}
              </Button>
            </div>
            {!recording.supported && (
              <p data-testid="recording-unsupported" className="mt-1 text-metadata text-slate-500">
                {recording.reason}
              </p>
            )}
            {playback.message && (
              <LiveStatus data-testid="playback-message" priority={playback.message.toLowerCase().includes("failed") ? "assertive" : "polite"} className="mt-2 text-metadata text-slate-700">
                {playback.message}
              </LiveStatus>
            )}
          </>
        ) : (
          <p className="text-metadata text-slate-500">Playback and recording run in the 3D Terrain view.</p>
        )}
        <Button size="sm" variant="ghost" className="mt-2" onClick={onDownloadPath} disabled={waypoints.length < 2}>Download path (JSON)</Button>
        </WorkspaceToolSection>
      </Card>
    </div>
  );
}
