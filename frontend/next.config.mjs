/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,

  // Standalone output enables minimal runtime image: Next.js copies only the
  // production code + its node_modules subset into .next/standalone, plus a
  // `server.js` entrypoint. The frontend Dockerfile's runtime stage ships
  // that directory only — no full node_modules tree.
  output: "standalone",

  // Runtime env injection (M4): the frontend image is environment-agnostic.
  // Client code reads `window.__ENV__` (populated by /env.js at runtime)
  // via `frontend/lib/env.ts`. The Node server reads plain env vars at
  // request time.
  //
  // We deliberately DO NOT inline NEXT_PUBLIC_* here — those would bake at
  // build time and defeat the single-image-per-release goal. See plan
  // .agents/plans/m4-aks-deployment.md Task 4 / 4a.
};

export default nextConfig;
