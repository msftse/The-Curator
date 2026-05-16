"use client";

import { useCallback, useEffect, useRef, useState } from "react";

type Entry<T> = {
  data?: T;
  error?: unknown;
  promise?: Promise<T>;
  subscribers: Set<() => void>;
};

// Module-scoped cache. Key is a stable JSON-serialized version of the input.
const CACHE: Map<string, Entry<unknown>> = new Map();

function normalize(key: unknown): string {
  return typeof key === "string" ? key : JSON.stringify(key);
}

function getEntry<T>(key: string): Entry<T> {
  let entry = CACHE.get(key) as Entry<T> | undefined;
  if (!entry) {
    entry = { subscribers: new Set() };
    CACHE.set(key, entry as Entry<unknown>);
  }
  return entry;
}

function notify(entry: Entry<unknown>) {
  for (const cb of entry.subscribers) cb();
}

async function runFetch<T>(
  key: string,
  fetcher: () => Promise<T>,
): Promise<T> {
  const entry = getEntry<T>(key);
  if (entry.promise) return entry.promise;
  const p = (async () => {
    try {
      const v = await fetcher();
      entry.data = v;
      entry.error = undefined;
      return v;
    } catch (err) {
      entry.error = err;
      throw err;
    } finally {
      entry.promise = undefined;
      notify(entry as Entry<unknown>);
    }
  })();
  entry.promise = p;
  return p;
}

export interface UseResourceResult<T> {
  data: T | undefined;
  error: unknown;
  isLoading: boolean;
  mutate: () => Promise<void>;
}

/**
 * Minimal SWR-shaped hook. Deduplicates in-flight fetches per key, caches
 * results module-wide, and supports manual revalidation via `mutate()`.
 *
 * SSR-safe: on the server, returns a loading stub and never calls `fetcher`.
 */
export function useResource<T>(
  key: unknown,
  fetcher: () => Promise<T>,
  opts: { revalidateOnMount?: boolean } = {},
): UseResourceResult<T> {
  const { revalidateOnMount = true } = opts;
  const k = normalize(key);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const entry = getEntry<T>(k);
  const [, force] = useState(0);

  useEffect(() => {
    // SSR-guard: only run in the browser.
    if (typeof window === "undefined") return;
    const cb = () => force((n) => n + 1);
    entry.subscribers.add(cb);
    if (revalidateOnMount || entry.data === undefined) {
      void runFetch(k, () => fetcherRef.current()).catch(() => {
        /* error stored on entry */
      });
    }
    return () => {
      entry.subscribers.delete(cb);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [k]);

  const mutate = useCallback(async () => {
    const e = getEntry<T>(k);
    e.data = undefined;
    e.error = undefined;
    e.promise = undefined;
    try {
      await runFetch(k, () => fetcherRef.current());
    } catch {
      /* error stored on entry */
    }
  }, [k]);

  // SSR-safe stub.
  if (typeof window === "undefined") {
    return {
      data: undefined,
      error: undefined,
      isLoading: true,
      mutate,
    };
  }

  return {
    data: entry.data,
    error: entry.error,
    isLoading:
      entry.promise !== undefined ||
      (entry.data === undefined && entry.error === undefined),
    mutate,
  };
}

/** Test-only: clear the module cache. */
export function __clearResourceCache() {
  CACHE.clear();
}
