"use client";

import { useMemo } from "react";

import { api } from "@/lib/api/client";
import { useResource } from "@/lib/hooks/useResource";

/**
 * "Hub at a glance" — homepage live-metrics band.
 *
 * Three counters sourced live from the public API:
 *   1. SKILLS PUBLISHED   ← length of `GET /v1/skills` (status=approved).
 *   2. CATEGORIES         ← length of `GET /v1/categories` (canonical taxonomy).
 *   3. COMPATIBLE AGENTS  ← static — agent compatibility is a contract of the
 *      SKILL.md format itself, not something the backend tracks per-skill.
 *      Keep this list in sync with marketing copy and `Hero` Stats below.
 *
 * Both fetches are cached/deduped by `useResource`, so this section is cheap
 * even when re-rendered. The component renders a graceful skeleton-ish
 * fallback ("—") on first paint / SSR rather than blocking the homepage.
 */

const COMPATIBLE_AGENTS = [
  "Hermes",
  "Openclaw",
  "Claude Code",
  "Cursor",
  "GitHub Copilot",
] as const;

export function HubAtAGlance() {
  const skills = useResource(["home", "skills"], () => api.catalog.list());
  const categories = useResource(["home", "categories"], () =>
    api.meta.categories(),
  );

  const skillCount = skills.data?.length;
  const categoryCount = categories.data?.length;
  // Show a compact preview of the taxonomy under the count, matching the
  // screenshot. First 6 names, joined by middle-dot.
  const categoryPreview = useMemo(() => {
    if (!categories.data) return "";
    return categories.data.slice(0, 6).join(" · ");
  }, [categories.data]);

  return (
    <section
      id="hub-at-a-glance"
      aria-label="Hub at a glance"
      className="section-dark px-6 py-20"
    >
      <div className="section-dark-frame" />
      <div className="relative z-[1] mx-auto max-w-[1100px]">
        <span className="ms-eyebrow-gold mb-2 block text-center">Live</span>
        <h2 className="text-center font-display text-[clamp(28px,4vw,42px)] font-bold leading-tight tracking-ms-display text-cream">
          Hub at a glance
        </h2>

        <div className="my-10 ms-divider">
          <div className="ms-divider-line" />
          <div className="ms-divider-icon">◆</div>
          <div className="ms-divider-line" />
        </div>

        <div className="grid grid-cols-1 gap-5 sm:grid-cols-3">
          <GlanceCard
            eyebrow="Skills published"
            value={fmtCount(skillCount, skills.error)}
            caption="Curated & security-vetted for MCAPS Israel"
            statusTone="green"
            statusLabel="Accepting submissions"
          />
          <GlanceCard
            eyebrow="Categories"
            value={fmtCount(categoryCount, categories.error)}
            caption={categoryPreview || "Canonical taxonomy"}
            statusTone="blue"
            statusLabel="Growing"
          />
          <GlanceCard
            eyebrow="Compatible agents"
            value={String(COMPATIBLE_AGENTS.length)}
            caption={COMPATIBLE_AGENTS.join(" · ")}
            statusTone="green"
            statusLabel="All supported"
          />
        </div>
      </div>
    </section>
  );
}

function fmtCount(n: number | undefined, error: unknown): string {
  if (error) return "—";
  if (n === undefined) return "—";
  return String(n);
}

function GlanceCard({
  eyebrow,
  value,
  caption,
  statusTone,
  statusLabel,
}: {
  eyebrow: string;
  value: string;
  caption: string;
  statusTone: "green" | "blue" | "yellow" | "red";
  statusLabel: string;
}) {
  const dotClass: Record<typeof statusTone, string> = {
    green: "bg-ms-green",
    blue: "bg-ms-blue",
    yellow: "bg-ms-yellow",
    red: "bg-ms-red",
  };
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-gold/35 bg-ink-surface p-6">
      <span className="text-[11px] font-semibold uppercase tracking-[0.22em] text-gold">
        {eyebrow}
      </span>
      <strong className="font-display text-[44px] font-bold leading-none tracking-ms-display text-cream">
        {value}
      </strong>
      <p className="min-h-[2.5em] text-[13.5px] leading-[1.5] text-cream-dim">
        {caption}
      </p>
      <div className="mt-1 flex items-center gap-2">
        <span
          aria-hidden
          className={"inline-block h-2 w-2 rounded-full " + dotClass[statusTone]}
        />
        <span className="text-[13px] font-semibold text-cream">
          {statusLabel}
        </span>
      </div>
    </div>
  );
}
