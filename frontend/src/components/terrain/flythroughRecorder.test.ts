import { describe, expect, it } from "vitest";
import {
  pickWebmMimeType,
  recordingFileName,
  recordingSupport,
  WEBM_MIME_CANDIDATES,
} from "./flythroughRecorder";

describe("recording support", () => {
  it("prefers VP9, then VP8, then plain WebM", () => {
    expect(pickWebmMimeType(() => true)).toBe(WEBM_MIME_CANDIDATES[0]);
    expect(pickWebmMimeType((t) => t !== "video/webm;codecs=vp9")).toBe("video/webm;codecs=vp8");
    expect(pickWebmMimeType((t) => t === "video/webm")).toBe("video/webm");
    expect(pickWebmMimeType(() => false)).toBeNull();
  });

  it("reports the real reason when a browser cannot record", () => {
    expect(
      recordingSupport({ hasMediaRecorder: false, isTypeSupported: null, canCaptureStream: true }),
    ).toEqual({
      supported: false,
      reason: "Recording is not supported in this browser (no MediaRecorder).",
    });
    const noCapture = recordingSupport({
      hasMediaRecorder: true,
      isTypeSupported: () => true,
      canCaptureStream: false,
    });
    expect(!noCapture.supported && noCapture.reason).toMatch(/canvas capture unavailable/);
    const noWebm = recordingSupport({
      hasMediaRecorder: true,
      isTypeSupported: () => false,
      canCaptureStream: true,
    });
    expect(!noWebm.supported && noWebm.reason).toMatch(/no WebM encoder/);
    expect(
      recordingSupport({ hasMediaRecorder: true, isTypeSupported: () => true, canCaptureStream: true }),
    ).toEqual({ supported: true, mimeType: "video/webm;codecs=vp9" });
  });

  it("names the file after the terrain artifact", () => {
    expect(recordingFileName("abc-123")).toBe("terrainx-flythrough-abc-123.webm");
  });
});
