import { useEffect, useRef, useState } from "react";
import { ApiError } from "@/api/client";

export interface ObjectUrlState {
  url: string | null;
  loading: boolean;
  error: string | null;
}

/**
 * Fetches an authenticated binary resource (via `fetchBlob`, which already
 * carries the Authorization header — see api/client.ts) and exposes it as a
 * browser object URL for use in <img src>. A plain <img src="..."> cannot
 * attach a Bearer token, so every raster preview goes through this instead.
 *
 * Re-fetches only when `key` changes (not on every render, even if the
 * caller passes a fresh inline `fetchBlob` closure each time — the latest
 * closure is tracked in a ref instead of the effect's dependency array), and
 * always revokes the previous object URL before creating a new one, and on
 * unmount, so switching datasets/layers never leaks blob URLs.
 */
export function useObjectUrlBlob(
  key: string | null,
  fetchBlob: () => Promise<Blob>,
): ObjectUrlState {
  const [state, setState] = useState<ObjectUrlState>({ url: null, loading: false, error: null });
  const urlRef = useRef<string | null>(null);
  const fetchRef = useRef(fetchBlob);
  fetchRef.current = fetchBlob;

  useEffect(() => {
    let cancelled = false;

    if (urlRef.current) {
      URL.revokeObjectURL(urlRef.current);
      urlRef.current = null;
    }

    if (!key) {
      setState({ url: null, loading: false, error: null });
      return;
    }

    setState({ url: null, loading: true, error: null });
    fetchRef
      .current()
      .then((blob) => {
        if (cancelled) return;
        const url = URL.createObjectURL(blob);
        urlRef.current = url;
        setState({ url, loading: false, error: null });
      })
      .catch((err) => {
        if (cancelled) return;
        setState({
          url: null,
          loading: false,
          error: err instanceof ApiError ? err.message : "Failed to load image",
        });
      });

    return () => {
      cancelled = true;
    };
    // Intentionally keyed only on `key` — see docstring above.
     
  }, [key]);

  // Final cleanup on unmount.
  useEffect(() => {
    return () => {
      if (urlRef.current) {
        URL.revokeObjectURL(urlRef.current);
        urlRef.current = null;
      }
    };
  }, []);

  return state;
}
