"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { get } from "./api";

/**
 * Fetch `path` and re-fetch every `ms` (0 = once). Pauses while the tab is hidden.
 * When `path` changes, the previous data stays in place (loading=true) until the new response lands.
 */
export function usePoll<T = any>(path: string | null, ms = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadedPath, setLoadedPath] = useState<string | null>(null);
  const alive = useRef(true);
  const current = useRef(path);
  current.current = path;

  const reload = useCallback(async () => {
    if (!path) return;
    try {
      const d = await get<T>(path);
      if (alive.current && current.current === path) {
        setData(d);
        setError(null);
        setLoadedPath(path);
      }
    } catch (e) {
      if (alive.current && current.current === path) {
        setError((e as Error).message);
        setLoadedPath(path);
      }
    }
  }, [path]);

  useEffect(() => {
    alive.current = true;
    reload();
    if (!ms) return () => void (alive.current = false);
    const id = setInterval(() => {
      if (!document.hidden) reload();
    }, ms);
    return () => {
      alive.current = false;
      clearInterval(id);
    };
  }, [reload, ms]);

  return { data, error, reload, loading: path !== loadedPath };
}
