// Runtime environment resolution.
//
// Why this exists: the frontend ships as a single Docker image promoted
// across dev → staging → prod. Build-time `NEXT_PUBLIC_*` baking would
// require a separate image per env, defeating "build once, deploy many".
//
// Strategy (Option B — runtime /env.js injection):
//
// 1. The pod's container env (`AUTH_MODE`, `ENTRA_TENANT_ID`, …) is read
//    server-side by `/env.js` (an App Router route handler, see
//    `app/env.js/route.ts`).
// 2. `/env.js` returns a tiny JS payload: `window.__ENV__ = { … }`.
// 3. `<script src="/env.js">` in `app/layout.tsx` `<head>` runs before
//    any React hydration, so `window.__ENV__` is populated by the time
//    client components mount.
// 4. This module's `getEnv()` reads `window.__ENV__` on the client and
//    falls back to `process.env` on the server (SSR + tests + Node
//    smoke). The two paths never disagree at runtime because Next.js
//    only renders auth-coupled UI inside client components.
//
// Note we intentionally do NOT define `NEXT_PUBLIC_*` constants here.
// The whole point is to break the build-time coupling. Callers should
// use `getEnv()` (or the typed helpers) every time they need a value.

export interface PublicEnv {
  AUTH_MODE: "oidc" | "stub";
  ENTRA_TENANT_ID: string;
  ENTRA_CLIENT_ID: string;
  ENTRA_API_SCOPE: string;
  API_BASE: string;
}

declare global {
  interface Window {
    __ENV__?: Partial<PublicEnv>;
  }
}

/**
 * Read a single public env var.
 *
 * - Client: prefers `window.__ENV__[key]`, falls back to `process.env.NEXT_PUBLIC_<KEY>`
 *   (which is undefined in production builds — present only in `next dev`).
 * - Server: reads `process.env.<key>` directly (no `NEXT_PUBLIC_` prefix
 *   required, since this code runs inside the container).
 *
 * `defaultValue` is returned when neither source has a value.
 */
export function getEnv<K extends keyof PublicEnv>(
  key: K,
  defaultValue: PublicEnv[K],
): PublicEnv[K] {
  if (typeof window !== "undefined") {
    const fromWindow = window.__ENV__?.[key];
    if (fromWindow !== undefined && fromWindow !== "") {
      return fromWindow as PublicEnv[K];
    }
    // Dev fallback — `next dev` still inlines NEXT_PUBLIC_* into the bundle.
    const devKey = `NEXT_PUBLIC_${key}` as const;
    const fromProcess = (process.env as Record<string, string | undefined>)[devKey];
    if (fromProcess !== undefined && fromProcess !== "") {
      return fromProcess as PublicEnv[K];
    }
    return defaultValue;
  }
  // Server-side: read the container env directly (no prefix). Falls back to
  // the NEXT_PUBLIC_-prefixed form so local `next dev` still works without
  // having to set both halves.
  const fromServer = process.env[key];
  if (fromServer !== undefined && fromServer !== "") {
    return fromServer as PublicEnv[K];
  }
  const fromPrefixed = process.env[`NEXT_PUBLIC_${key}`];
  if (fromPrefixed !== undefined && fromPrefixed !== "") {
    return fromPrefixed as PublicEnv[K];
  }
  return defaultValue;
}

/** Typed helpers — call these from app code rather than `getEnv` directly. */
export function authMode(): "oidc" | "stub" {
  const v = getEnv("AUTH_MODE", "stub");
  return v === "oidc" ? "oidc" : "stub";
}

export function isOidc(): boolean {
  return authMode() === "oidc";
}

export function entraTenantId(): string {
  return getEnv("ENTRA_TENANT_ID", "");
}

export function entraClientId(): string {
  return getEnv("ENTRA_CLIENT_ID", "");
}

export function entraApiScope(): string {
  return getEnv("ENTRA_API_SCOPE", "");
}

export function apiBase(): string {
  return getEnv("API_BASE", "http://localhost:8000");
}

/**
 * Server-side snapshot used by `/env.js` to bake the window payload.
 * Reads every key once and returns the values that should be exposed to
 * the client. Empty strings are dropped to keep the payload minimal.
 */
export function snapshotForClient(): Partial<PublicEnv> {
  const out: Partial<PublicEnv> = {};
  const mode = authMode();
  if (mode) out.AUTH_MODE = mode;
  const tenant = entraTenantId();
  if (tenant) out.ENTRA_TENANT_ID = tenant;
  const client = entraClientId();
  if (client) out.ENTRA_CLIENT_ID = client;
  const scope = entraApiScope();
  if (scope) out.ENTRA_API_SCOPE = scope;
  const base = apiBase();
  if (base) out.API_BASE = base;
  return out;
}
