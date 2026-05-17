"use client";

import { useEffect, useMemo, useState } from "react";

import { api } from "@/lib/api/client";

/**
 * Download dialog.
 *
 * Surfaces three things the user actually wants:
 *
 *   1. **A prompt to paste into their agent.** Most consumers won't run the
 *      bundle themselves — they hand a short instruction to ClawPilot /
 *      Claude Code / Cursor / Copilot Chat and the agent goes and fetches
 *      the SKILL.md. So the primary affordance is "Copy prompt", not
 *      "Copy URL".
 *   2. **How to use it.** Three-step recipe directly above the prompt, so
 *      the dialog is self-explanatory the first time someone opens it.
 *   3. **The bundle itself**, as a fallback, via a short-lived signed URL
 *      (~15 min — see backend `signed_download_url`). The endpoint is
 *      auth-gated, so we fetch the SAS via the typed API client and then
 *      navigate the browser to it.
 *
 * The SAS is not auto-refreshed. Reopening the dialog mints a new one;
 * the URL is the capability and we want an explicit re-issue gesture.
 */
export function DownloadDialog({
  open,
  skillId,
  skillName,
  uploader,
  version,
  category,
  description,
  tags,
  onClose,
}: {
  open: boolean;
  skillId: string;
  skillName: string;
  uploader: string;
  version: string;
  /** Effective (user-or-classifier) category. May be null if neither set. */
  category: string | null;
  /** One-line description shown under the header. Falls back to "". */
  description: string;
  /** Effective tags list — already merged user + classifier upstream. */
  tags: string[];
  onClose: () => void;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [expiresAt, setExpiresAt] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!open) {
      // Reset on close so the next open starts clean.
      setUrl(null);
      setExpiresAt(null);
      setError(null);
      setCopied(false);
      return;
    }

    let cancelled = false;
    // Fire usage event in parallel — fire-and-forget.
    const email =
      typeof window !== "undefined"
        ? (window.localStorage.getItem("x-user-email") ?? "anon@org")
        : "anon@org";
    void api.catalog
      .reportUsage(skillId, { loader_id: `web-ui:${email}` })
      .catch((err) => {
        // eslint-disable-next-line no-console
        console.warn("usage event failed (ignored)", err);
      });

    (async () => {
      try {
        const res = await api.catalog.getDownloadUrl(skillId);
        if (cancelled) return;
        setUrl(res.url);
        setExpiresAt(res.expires_at);
      } catch (err) {
        if (cancelled) return;
        // Surface the actual error so CORS / 401 / network failures
        // are diagnosable without DevTools.
        setError(
          err instanceof Error
            ? err.message
            : "Failed to prepare download. Try again in a moment.",
        );
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [open, skillId]);

  // Build the agent-ready prompt. Stable regardless of SAS state — users
  // can copy it even while the bundle URL is still being minted.
  const prompt = useMemo(
    () => buildAgentPrompt({ skillName, description, category, tags }),
    [skillName, description, category, tags],
  );

  if (!open) return null;

  const onDownload = () => {
    if (!url) return;
    window.location.assign(url);
  };

  const onCopyPrompt = async () => {
    try {
      await navigator.clipboard.writeText(prompt);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Clipboard write blocked.",
      );
    }
  };

  const expiresIn = expiresAt ? formatRelativeExpiry(expiresAt) : null;
  const isReady = url !== null && error === null;

  // Static sub-line: "by alice@contoso.com · v1.0.0 · Sales & Demos"
  const subParts = [
    `by ${uploader}`,
    `v${version.replace(/^v/, "")}`,
    category,
  ].filter((p): p is string => !!p);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="download-dialog-title"
    >
      <div className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-lg border border-cream-border/40 bg-cream p-7 shadow-2xl">
        {/* Header */}
        <div className="flex items-start justify-between gap-4">
          <h2
            id="download-dialog-title"
            className="font-display text-[24px] font-bold tracking-ms-display text-ink"
          >
            {skillName}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded p-1 text-muted hover:bg-black/5 hover:text-ink"
          >
            ✕
          </button>
        </div>

        <hr className="mt-4 border-cream-border/60" />

        {/* Sub-line */}
        <p className="mt-4 text-[13px] text-muted">{subParts.join(" · ")}</p>

        {/* Description */}
        {description && (
          <p className="mt-4 text-[15px] leading-[1.55] text-ink-2">
            {description}
          </p>
        )}

        {/* Compatible agents */}
        <div className="mt-5 flex flex-wrap gap-2">
          {COMPATIBLE_AGENTS.map((agent) => (
            <span
              key={agent}
              className="ms-chip bg-ms-blue/10 text-ms-blue"
            >
              {agent}
            </span>
          ))}
        </div>

        {/* How to use */}
        <h3 className="mt-6 text-[11px] font-semibold uppercase tracking-[0.12em] text-muted">
          How to use it
        </h3>
        <ol className="mt-3 list-decimal space-y-1 pl-5 text-[14px] leading-[1.55] text-ink-2">
          <li>Copy the prompt below.</li>
          <li>Paste it into your AI agent (ClawPilot, Claude Code, Cursor, or Copilot Chat).</li>
          <li>
            The agent reads the skill and walks you through the task. Optionally{" "}
            <em>Download</em> the bundle if your agent fetches local files.
          </li>
        </ol>

        {/* Prompt block */}
        <p className="mt-6 text-[13px] font-semibold text-ink">
          Copy &amp; paste this into your agent:
        </p>
        <textarea
          readOnly
          value={prompt}
          className="mt-2 h-36 w-full resize-y rounded-md border border-cream-border/60 bg-bg-2 px-3 py-2.5 font-mono text-[12.5px] leading-[1.55] text-ink-2 focus:outline-none focus:ring-2 focus:ring-ms-blue/40"
        />

        {/* SAS status (only when there's something to say) */}
        {(expiresIn || error) && (
          <p className="mt-3 text-[11.5px] text-muted">
            {error ? (
              <span className="text-ms-red">{error}</span>
            ) : (
              <>Download link {expiresIn} — anyone holding it can fetch the bytes.</>
            )}
          </p>
        )}

        {/* Footer buttons */}
        <div className="mt-6 flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => void onCopyPrompt()}
            className="ms-btn-primary"
          >
            {copied ? "Copied ✓" : "Copy prompt"}
          </button>
          <button
            type="button"
            onClick={onDownload}
            disabled={!isReady}
            className="rounded border border-ink/20 bg-transparent px-4 py-2 text-[14px] font-medium text-ink hover:bg-black/5 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Download
          </button>
        </div>
      </div>
    </div>
  );
}

// Static list — matches Hub-at-a-glance. Compatibility is a SKILL.md
// format contract, not a per-skill setting, so this is global.
const COMPATIBLE_AGENTS = [
  "ClawPilot",
  "Claude Code",
  "Cursor",
  "GitHub Copilot",
] as const;

function buildAgentPrompt(args: {
  skillName: string;
  description: string;
  category: string | null;
  tags: string[];
}): string {
  const lines = [
    `I want to use the "${args.skillName}" skill from The Curator.`,
    "",
  ];
  if (args.description) {
    lines.push(`What it does: ${args.description}`);
  }
  if (args.category) {
    lines.push(`Category: ${args.category}`);
  }
  if (args.tags.length > 0) {
    lines.push(`Tags: ${args.tags.join(", ")}`);
  }
  return lines.join("\n");
}

/** "in 14m" / "in 38s" / "expired". Compact, human-readable. */
function formatRelativeExpiry(iso: string): string {
  const expiry = new Date(iso).getTime();
  const now = Date.now();
  const diffMs = expiry - now;
  if (diffMs <= 0) return "expired";
  const mins = Math.floor(diffMs / 60_000);
  if (mins >= 1) return `expires in ${mins}m`;
  const secs = Math.floor(diffMs / 1000);
  return `expires in ${secs}s`;
}
