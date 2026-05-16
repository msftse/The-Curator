"use client";

import type { SkillDetail } from "@/lib/api/types";

function formatBytes(n: number | undefined | null): string {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

function Label({ children }: { children: React.ReactNode }) {
  return (
    <dt className="text-[11px] font-semibold uppercase tracking-[0.18em] text-muted">
      {children}
    </dt>
  );
}

function Value({ children }: { children: React.ReactNode }) {
  return <dd className="text-sm text-ink-2">{children}</dd>;
}

export function SkillDetailMeta({ skill }: { skill: SkillDetail }) {
  const cls = skill.classification;
  const bundle = skill.bundle;

  return (
    <div className="ms-card grid grid-cols-1 gap-6 p-5 md:grid-cols-2">
      <section className="flex flex-col gap-3">
        <h2 className="font-display text-sm font-bold uppercase tracking-[0.15em] text-ink-2">
          Classifier
        </h2>
        <dl className="flex flex-col gap-3">
          <div>
            <Label>Category</Label>
            <Value>
              {cls?.category ? (
                <span className="ms-chip bg-violet/[0.18] text-violet-dark">
                  {cls.category}
                </span>
              ) : (
                "—"
              )}
            </Value>
          </div>
          <div>
            <Label>Tags</Label>
            <Value>
              {(cls?.tags ?? []).length === 0 ? (
                "—"
              ) : (
                <span className="flex flex-wrap gap-1.5">
                  {cls?.tags.map((t) => (
                    <span key={t} className="ms-chip">
                      {t}
                    </span>
                  ))}
                </span>
              )}
            </Value>
          </div>
          <div>
            <Label>Quality score</Label>
            <Value>{cls?.quality_score ?? "—"}</Value>
          </div>
          <div>
            <Label>Summary</Label>
            <Value>{cls?.summary || skill.description || "—"}</Value>
          </div>
        </dl>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="font-display text-sm font-bold uppercase tracking-[0.15em] text-ink-2">
          Bundle
        </h2>
        <dl className="flex flex-col gap-3">
          <div>
            <Label>Checksum (sha256)</Label>
            <Value>
              {bundle?.checksum_sha256 ? (
                <code className="font-mono text-xs">
                  {bundle.checksum_sha256.slice(0, 12)}…
                </code>
              ) : (
                "—"
              )}
            </Value>
          </div>
          <div>
            <Label>Size</Label>
            <Value>{formatBytes(bundle?.size_bytes)}</Value>
          </div>
          <div>
            <Label>File count</Label>
            <Value>{bundle?.file_count ?? "—"}</Value>
          </div>
          <div>
            <Label>Uploaded</Label>
            <Value>{skill.uploaded_at}</Value>
          </div>
        </dl>
      </section>
    </div>
  );
}
