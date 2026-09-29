// P1-8: flythrough recording support — pure capability logic (unit-tested)
// plus the thin browser reader it is fed from. Recording itself is
// MediaRecorder over canvas.captureStream(); nothing is ever faked when a
// browser cannot record — the control is disabled with the real reason.

/** Preferred first. */
export const WEBM_MIME_CANDIDATES = [
  "video/webm;codecs=vp9",
  "video/webm;codecs=vp8",
  "video/webm",
];

export const RECORDING_FPS = 30;

export interface RecordingEnvironment {
  hasMediaRecorder: boolean;
  isTypeSupported: ((mimeType: string) => boolean) | null;
  canCaptureStream: boolean;
}

export type RecordingSupport =
  | { supported: true; mimeType: string }
  | { supported: false; reason: string };

export function pickWebmMimeType(isTypeSupported: (mimeType: string) => boolean): string | null {
  for (const candidate of WEBM_MIME_CANDIDATES) {
    if (isTypeSupported(candidate)) return candidate;
  }
  return null;
}

export function recordingSupport(env: RecordingEnvironment): RecordingSupport {
  if (!env.hasMediaRecorder || !env.isTypeSupported) {
    return { supported: false, reason: "Recording is not supported in this browser (no MediaRecorder)." };
  }
  if (!env.canCaptureStream) {
    return {
      supported: false,
      reason: "Recording is not supported in this browser (canvas capture unavailable).",
    };
  }
  const mimeType = pickWebmMimeType(env.isTypeSupported);
  if (!mimeType) {
    return { supported: false, reason: "Recording is not supported in this browser (no WebM encoder)." };
  }
  return { supported: true, mimeType };
}

/** Reads the real capabilities of the current browser. */
export function browserRecordingEnvironment(): RecordingEnvironment {
  const recorder = (globalThis as { MediaRecorder?: { isTypeSupported?: (t: string) => boolean } })
    .MediaRecorder;
  const canvasProto = (globalThis as { HTMLCanvasElement?: { prototype: object } }).HTMLCanvasElement
    ?.prototype as { captureStream?: unknown } | undefined;
  return {
    hasMediaRecorder: typeof recorder === "function",
    isTypeSupported:
      typeof recorder?.isTypeSupported === "function"
        ? (t: string) => recorder.isTypeSupported!(t)
        : null,
    canCaptureStream: typeof canvasProto?.captureStream === "function",
  };
}

export function recordingFileName(artifactId: string): string {
  return `terrainx-flythrough-${artifactId}.webm`;
}

/** Hands a blob to the browser as a file download (never uploaded). */
export function downloadBlob(blob: Blob, fileName: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  // Revoked once the browser has taken the download.
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
