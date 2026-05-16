// `/env.js` — runtime environment payload for the SPA.
//
// Serves a tiny script that sets `window.__ENV__` before any React code
// mounts. Loaded via `<script src="/env.js">` in `app/layout.tsx`.
//
// Why a route handler instead of a static asset:
// - We promote one image across envs (build once, deploy many). The
//   container's env vars determine what gets injected at request time.
// - Caching: `Cache-Control: no-store` so a config rollout takes effect
//   on the next page load without a CDN purge. The payload is ~200 bytes.
// - Safety: we explicitly enumerate keys in `snapshotForClient()` rather
//   than dumping `process.env`. No secrets reach the wire.

import { NextResponse } from "next/server";

import { snapshotForClient } from "@/lib/env";

// Force dynamic — Next must not pre-render or cache this at build time.
export const dynamic = "force-dynamic";
export const revalidate = 0;

export function GET(): NextResponse {
  const env = snapshotForClient();
  // `JSON.stringify` with no replacer is safe here — values are strings we
  // sourced from container env, never user input. We still escape `</` to
  // avoid `</script>` injection if a future key ever holds user-derived text.
  const json = JSON.stringify(env).replace(/<\//g, "<\\/");
  const body = `window.__ENV__=${json};`;
  return new NextResponse(body, {
    status: 200,
    headers: {
      "Content-Type": "application/javascript; charset=utf-8",
      "Cache-Control": "no-store, max-age=0",
    },
  });
}
