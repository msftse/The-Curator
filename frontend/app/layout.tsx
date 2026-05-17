import "./globals.css";
import type { Metadata } from "next";
import Link from "next/link";

import { AdminNavLink } from "@/components/AdminNavLink";
import { UserPicker } from "@/components/UserPicker";
import { NavLink } from "@/components/NavLink";
import { AuthProvider } from "@/lib/auth/AuthProvider";

export const metadata: Metadata = {
  title: "Agentic Skill Hub",
  description:
    "Open-source hub for sanctioned agent skills. Curated, security-vetted, audit-trailed.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <head>
        {/*
         * Runtime env injection — see `frontend/lib/env.ts`.
         *
         * `/env.js` is served by `app/env.js/route.ts` and writes
         * `window.__ENV__` with the container's public env vars. It MUST
         * load before any React client bundle so MSAL + the API client
         * see the right config on first render.
         *
         * Plain `<script>` (not next/script) because we need synchronous,
         * pre-hydration execution. `no-store` on the response means a
         * config rollout takes effect on the next page load.
         */}
        <script src="/env.js" />
      </head>
      <body className="flex min-h-screen flex-col">
        <AuthProvider>
          {/* Top "NEW" announcement banner */}
          <div
            role="banner"
            className="border-b border-line bg-gradient-to-r from-bg-2 to-white px-4 py-2 text-center text-[13px] text-ink-2"
          >
            <span className="mr-2 inline-block rounded-full bg-ms-blue px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-white">
              New
            </span>
            Agentic Skill Hub v0.2 — submit, classify, review, publish. All in
            one place.
          </div>

          {/* Sticky nav */}
          <header className="sticky top-0 z-30 border-b border-line bg-white/90 backdrop-blur supports-[backdrop-filter]:bg-white/85">
            <nav
              aria-label="Main navigation"
              className="mx-auto flex max-w-[1280px] items-center gap-6 px-6 py-3"
            >
              <Link
                href="/"
                className="flex items-center gap-3 font-display text-[17px] font-bold text-ink hover:no-underline"
              >
                <BrandMark />
                <span className="leading-tight">
                  The Curator
                  <span className="block text-[10px] font-medium uppercase tracking-[0.18em] text-muted">
                    Agentic skills, reviewed
                  </span>
                </span>
              </Link>

              <div className="ml-3 hidden flex-1 items-center gap-1 md:flex">
                <NavLink href="/upload">Upload</NavLink>
                <NavLink href="/catalog">Catalog</NavLink>
                <NavLink href="/my-submissions">My submissions</NavLink>
                <NavLink href="/admin/queue">Review queue</NavLink>
                <AdminNavLink href="/admin/curator">Curator</AdminNavLink>
              </div>

              <div className="ml-auto flex items-center gap-2.5">
                <Link href="/upload" className="ms-btn-ghost hidden sm:inline-flex">
                  Submit a skill
                </Link>
                <UserPicker />
              </div>
            </nav>
          </header>

          <main className="flex-1 w-full">{children}</main>

          <Footer />
        </AuthProvider>
      </body>
    </html>
  );
}

function BrandMark() {
  // The Curator mark — same image as the favicon (app/icon.png), surfaced
  // here via /public so we can use a plain <img> without next/image config.
  return (
    <img
      src="/brand-icon.png"
      alt=""
      aria-hidden
      className="h-11 w-11 object-contain"
    />
  );
}

function Footer() {
  return (
    <footer className="relative mt-0 bg-ink px-6 pt-12 text-cream-dim">
      <span
        aria-hidden
        className="ms-gradient-stripe absolute inset-x-0 top-0 h-[2px]"
      />
      <div className="mx-auto grid max-w-[1280px] grid-cols-1 gap-8 sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <div className="mb-3 flex items-center gap-3">
            <img
              src="/brand-icon.png"
              alt=""
              aria-hidden
              className="h-12 w-12 object-contain"
            />
            <div>
              <strong className="font-display text-cream">The Curator</strong>
              <div className="text-xs text-cream-dim">
                Sanctioned skills. Reviewed &amp; audit-trailed.
              </div>
            </div>
          </div>
          <small className="text-cream-dim">
            Open source · Apache-2.0
          </small>
        </div>
        <div>
          <h4 className="mb-3 text-[12px] font-semibold uppercase tracking-[0.08em] text-gold">
            Product
          </h4>
          <ul className="flex flex-col gap-2 text-sm">
            <li>
              <Link
                href="/catalog"
                className="text-cream-dim hover:text-cream hover:no-underline"
              >
                Browse catalog
              </Link>
            </li>
            <li>
              <Link
                href="/upload"
                className="text-cream-dim hover:text-cream hover:no-underline"
              >
                Upload a skill
              </Link>
            </li>
            <li>
              <Link
                href="/my-submissions"
                className="text-cream-dim hover:text-cream hover:no-underline"
              >
                My submissions
              </Link>
            </li>
            <li>
              <Link
                href="/admin/queue"
                className="text-cream-dim hover:text-cream hover:no-underline"
              >
                Review queue
              </Link>
            </li>
          </ul>
        </div>
        <div>
          <h4 className="mb-3 text-[12px] font-semibold uppercase tracking-[0.08em] text-gold">
            Developers
          </h4>
          <ul className="flex flex-col gap-2 text-sm">
            <li>
              <Link
                href="/upload"
                className="text-cream-dim hover:text-cream hover:no-underline"
              >
                Create a skill
              </Link>
            </li>
            <li>
              <Link
                href="/admin/curator"
                className="text-cream-dim hover:text-cream hover:no-underline"
              >
                Curator
              </Link>
            </li>
          </ul>
        </div>
        <div>
          <h4 className="mb-3 text-[12px] font-semibold uppercase tracking-[0.08em] text-gold">
            Community
          </h4>
          <ul className="flex flex-col gap-2 text-sm">
            <li>
              <a
                href="https://github.com/anomalyco/opencode"
                className="text-cream-dim hover:text-cream hover:no-underline"
              >
                GitHub
              </a>
            </li>
            <li>
              <Link
                href="/"
                className="text-cream-dim hover:text-cream hover:no-underline"
              >
                About
              </Link>
            </li>
          </ul>
        </div>
      </div>
      <div className="mx-auto mt-8 flex max-w-[1280px] flex-wrap items-center justify-between gap-3 border-t border-gold/35 py-4 text-xs text-cream-dim opacity-70">
        <span>&copy; {new Date().getFullYear()} Agentic Skill Hub · v0.2</span>
        <span>Apache-2.0</span>
      </div>
    </footer>
  );
}
