import { useEffect, useState } from "react";

/** Real WebGL feature-detection (not a browser/UA sniff) — creates a
 * throwaway canvas and asks for a webgl2/webgl context. Used to decide
 * whether the 3D terrain viewer can render at all before ever constructing
 * a Three.js renderer (which would otherwise throw deep inside a library
 * call and crash the whole workspace). */
export function useWebGLSupport(): boolean {
  const [supported, setSupported] = useState(true);

  useEffect(() => {
    try {
      const canvas = document.createElement("canvas");
      const gl = canvas.getContext("webgl2") ?? canvas.getContext("webgl");
      setSupported(gl !== null);
    } catch {
      setSupported(false);
    }
  }, []);

  return supported;
}
