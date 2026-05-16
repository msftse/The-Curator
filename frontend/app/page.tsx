import Link from "next/link";

export default function HomePage() {
  return (
    <>
      <Hero />
      <WhatWeDo />
      <BrowseCatalog />
    </>
  );
}

/* ====================== HERO ====================== */
function Hero() {
  return (
    <section
      aria-label="Hero"
      className="relative flex min-h-[80vh] flex-col items-center justify-center overflow-hidden px-8 py-20 text-center text-cream"
    >
      {/* Cinematic dark backdrop — Obsidian Navy with Slate Ink center glow */}
      <div
        aria-hidden
        className="absolute inset-0 z-0"
        style={{
          background:
            "radial-gradient(ellipse at 30% 20%, #171B2E 0%, #0B1020 55%, #05070F 100%)",
        }}
      />
      {/* Curator accent glows — Signal Violet + Curator Indigo + Artifact Gold */}
      <div
        aria-hidden
        className="absolute inset-0 z-0 opacity-50"
        style={{
          background:
            "radial-gradient(circle at 18% 30%, rgba(139,92,246,0.28), transparent 38%)," +
            "radial-gradient(circle at 82% 25%, rgba(91,95,239,0.30), transparent 42%)," +
            "radial-gradient(circle at 75% 80%, rgba(91,95,239,0.18), transparent 35%)," +
            "radial-gradient(circle at 22% 85%, rgba(245,197,66,0.14), transparent 38%)",
        }}
      />
      {/* Vignette */}
      <div
        aria-hidden
        className="absolute inset-0 z-[1]"
        style={{
          background:
            "radial-gradient(ellipse at center, rgba(0,0,0,0) 0%, rgba(0,0,0,0.35) 75%, rgba(0,0,0,0.6) 100%)",
        }}
      />
      {/* Double-stroke frame */}
      <div
        aria-hidden
        className="pointer-events-none absolute z-[2] border-[1.5px]"
        style={{
          top: "2.5rem",
          left: "2.5rem",
          right: "2.5rem",
          bottom: "2.5rem",
          borderColor: "rgba(253,246,232,0.45)",
        }}
      />
      <div
        aria-hidden
        className="pointer-events-none absolute z-[2]"
        style={{
          top: "calc(2.5rem + 6px)",
          left: "calc(2.5rem + 6px)",
          right: "calc(2.5rem + 6px)",
          bottom: "calc(2.5rem + 6px)",
          border: "0.5px solid rgba(253,246,232,0.6)",
        }}
      />

      <div className="relative z-[3] flex w-full max-w-[900px] flex-col items-center gap-5">
        <span
          className="rounded-full border border-cream-border bg-black/15 px-3.5 py-1.5 text-[11px] font-semibold uppercase tracking-[0.25em] text-cream backdrop-blur"
          style={{ opacity: 0.85 }}
        >
          Open source · v0.2
        </span>

        <h1 className="m-0 font-display text-[clamp(36px,7vw,72px)] font-bold leading-[1.05] tracking-ms-tight text-cream drop-shadow-[0_4px_30px_rgba(0,0,0,0.55)]">
          A hub for{" "}
          <span className="ms-accent-text">agent skills</span>
          <br />
          your team can trust.
        </h1>

        <Ornament />

        <p className="mx-auto max-w-[640px] text-[clamp(15px,1.6vw,19px)] leading-[1.55] text-cream-dim drop-shadow-[0_2px_14px_rgba(0,0,0,0.55)]">
          Submit a SKILL.md bundle, watch the classifier run, have a manager
          publish it to the catalog, then let the curator keep it healthy —
          flagging stale skills, proposing fixes, and archiving (never deleting)
          what nobody uses. Reviewed, audit-trailed, and security-vetted —
          compatible with any agent runtime that consumes plain Markdown skills.
        </p>

        <div className="mt-2 flex flex-wrap items-center justify-center gap-3">
          <Link href="/upload" className="ms-btn-cream">
            Submit a skill &rarr;
          </Link>
          <Link href="/my-submissions" className="ms-btn-cream-ghost">
            View my submissions
          </Link>
        </div>

        <div className="mt-6 flex flex-wrap justify-center gap-9 text-[11px] uppercase tracking-[0.18em] text-cream-dim">
          <Stat value="4" label="Statuses" />
          <Stat value="3-step" label="Review flow" />
          <Stat value="4" label="Compatible agents" />
          <Stat value="Apache-2.0" label="Open source" raw />
        </div>
      </div>
    </section>
  );
}

function Stat({
  value,
  label,
  raw,
}: {
  value: string;
  label: string;
  raw?: boolean;
}) {
  return (
    <div>
      <strong
        className={
          "block font-display font-bold text-cream " +
          (raw
            ? "text-base normal-case tracking-normal"
            : "text-[22px] tracking-ms-display")
        }
      >
        {value}
      </strong>
      {label}
    </div>
  );
}

function Ornament() {
  return (
    <div aria-hidden className="my-1 flex items-center justify-center gap-4">
      <span
        className="h-px w-16"
        style={{ background: "rgba(253,246,232,0.5)" }}
      />
      <span
        className="text-[0.7rem]"
        style={{ color: "rgba(253,246,232,0.65)" }}
      >
        ◆
      </span>
      <span
        className="h-px w-16"
        style={{ background: "rgba(253,246,232,0.5)" }}
      />
    </div>
  );
}

/* ================== WHAT WE DO (dark) ================== */
function WhatWeDo() {
  return (
    <section
      id="what"
      aria-label="What we do"
      className="section-dark px-6 py-20"
    >
      <div className="section-dark-frame" />
      <div className="relative z-[1] mx-auto max-w-[1100px]">
        <span className="ms-eyebrow-gold mb-2 block text-center">
          What you can do here
        </span>
        <h2 className="text-center font-display text-[clamp(28px,4vw,42px)] font-bold leading-tight tracking-ms-display text-cream">
          Skill management, end to end
        </h2>
        <p className="mx-auto mt-2 max-w-[600px] text-center text-[15px] leading-[1.55] text-cream-dim">
          Upload your SKILL.md, let the classifier label it, a manager
          approves, and it's published immutably to the catalog. Every
          transition is audit-trailed.
        </p>

        <div className="my-10 ms-divider">
          <div className="ms-divider-line" />
          <div className="ms-divider-icon">◆</div>
          <div className="ms-divider-line" />
        </div>

        <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-4">
          <VpCard
            tone="red"
            icon="⚙"
            title="Submit"
            description="Drop a SKILL.md or tar.gz bundle. The classifier auto-labels category and summary."
          />
          <VpCard
            tone="blue"
            icon="★"
            title="Track"
            description="Live status — pending, classified, approved, rejected. Auto-refreshed every few seconds."
          />
          <VpCard
            tone="green"
            icon="✓"
            title="Review"
            description="Managers approve or reject classified skills. Every decision is recorded in the audit log."
          />
          <VpCard
            tone="yellow"
            icon="◈"
            title="Curate"
            description="Pin, archive, snapshot, rollback. Never-delete invariant guarantees full recoverability."
          />
        </div>
      </div>
    </section>
  );
}

function VpCard({
  tone,
  icon,
  title,
  description,
}: {
  tone: "red" | "blue" | "green" | "yellow";
  icon: string;
  title: string;
  description: string;
}) {
  const toneClasses: Record<typeof tone, string> = {
    red: "bg-ms-red/15 text-ms-red",
    blue: "bg-ms-blue/15 text-ms-blue",
    green: "bg-ms-green/15 text-ms-green",
    yellow: "bg-ms-yellow/15 text-ms-yellow",
  };
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-gold/35 bg-ink-surface p-6 transition-all duration-200 hover:-translate-y-0.5 hover:border-gold">
      <div
        aria-hidden
        className={
          "flex h-10 w-10 items-center justify-center rounded-[10px] text-xl font-bold " +
          toneClasses[tone]
        }
      >
        {icon}
      </div>
      <h3 className="font-display text-[17px] font-bold text-cream">{title}</h3>
      <p className="text-[13.5px] leading-[1.5] text-cream-dim">{description}</p>
    </div>
  );
}

/* ================== BROWSE CATALOG (light) ================== */
function BrowseCatalog() {
  return (
    <section
      id="browse"
      aria-label="Get started"
      className="px-6 py-20"
      style={{
        background: "linear-gradient(180deg, #F3F1FB 0%, var(--bg) 100%)",
      }}
    >
      <div className="mx-auto max-w-[1100px]">
        <span className="ms-eyebrow-gold mb-2 block text-center">
          Get started
        </span>
        <h2 className="text-center font-display text-[clamp(28px,4vw,42px)] font-bold leading-tight tracking-ms-display text-ink">
          Pick your next step
        </h2>
        <p className="mx-auto mt-2 max-w-[580px] text-center text-[15px] leading-[1.55] text-muted">
          Browse what's published, contribute a new skill, track your queue,
          or review what's coming in.
        </p>

        <div className="my-10 ms-divider">
          <div className="ms-divider-line" />
          <div className="ms-divider-icon">◆</div>
          <div className="ms-divider-line" />
        </div>

        <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-4">
          <ActionCard
            href="/catalog"
            tone="yellow"
            eyebrow="Discover"
            title="Browse the catalog"
            description="See every approved skill, filter by category, and download the bundle."
          />
          <ActionCard
            href="/upload"
            tone="red"
            eyebrow="Contributor"
            title="Upload a skill"
            description="Drop in a SKILL.md or tar.gz bundle to start the review pipeline."
          />
          <ActionCard
            href="/my-submissions"
            tone="blue"
            eyebrow="Contributor"
            title="My submissions"
            description="Track classifier progress, manager decisions, and publish status."
          />
          <ActionCard
            href="/admin/queue"
            tone="green"
            eyebrow="Manager"
            title="Review queue"
            description="Approve or reject classified skills. Requires the manager@org persona."
          />
        </div>
      </div>
    </section>
  );
}

function ActionCard({
  href,
  tone,
  eyebrow,
  title,
  description,
}: {
  href: string;
  tone: "red" | "blue" | "green" | "yellow";
  eyebrow: string;
  title: string;
  description: string;
}) {
  const dot: Record<typeof tone, string> = {
    red: "bg-ms-red",
    blue: "bg-ms-blue",
    green: "bg-ms-green",
    yellow: "bg-ms-yellow",
  };
  return (
    <Link
      href={href}
      className="ms-card ms-card-hover ms-card-stripe group flex flex-col gap-3 p-6 hover:no-underline"
    >
      <div className="flex items-center gap-2">
        <span className={"inline-block h-2 w-2 rounded-full " + dot[tone]} />
        <span className="text-[11px] font-semibold uppercase tracking-[0.18em] text-muted">
          {eyebrow}
        </span>
      </div>
      <h3 className="font-display text-[18px] font-bold tracking-ms-display text-ink">
        {title}
      </h3>
      <p className="text-sm leading-[1.5] text-muted">{description}</p>
      <span className="mt-auto inline-flex items-center gap-1.5 text-sm font-semibold text-ms-blue group-hover:no-underline">
        Open
        <span aria-hidden className="transition-transform group-hover:translate-x-0.5">
          →
        </span>
      </span>
    </Link>
  );
}
