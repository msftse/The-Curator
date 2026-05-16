"use client";

import type { SkillDetail } from "@/lib/api/types";

function formatBytes(n: number | undefined | null): string {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    });
  } catch {
    return iso;
  }
}

function Row({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1 border-b border-line/60 pb-3 last:border-b-0 last:pb-0">
      <dt className="text-[10px] font-semibold uppercase tracking-[0.18em] text-muted">
        {label}
      </dt>
      <dd className="text-sm text-ink-2">{children}</dd>
    </div>
  );
}

export function SkillDetailMeta({ skill }: { skill: SkillDetail }) {
  const cls = skill.classification;
  const bundle = skill.bundle;
  const userCategoryDiverges =
    skill.user_category != null &&
    cls?.category != null &&
    skill.user_category !== cls.category;

  return (
    <aside className="ms-card flex flex-col gap-5 p-5">
      <section className="flex flex-col gap-3">
        <h2 className="font-display text-[11px] font-bold uppercase tracking-[0.18em] text-ink-2">
          Classifier
        </h2>
        <dl className="flex flex-col gap-3">
          <Row label="Category">
            {cls?.category ? (
              <span className="ms-chip bg-violet/[0.18] text-violet-dark">
                {cls.category}
              </span>
            ) : (
              "—"
            )}
          </Row>
          {(skill.user_category || (skill.user_tags ?? []).length > 0) && (
            <Row label="Uploader hint">
              <div className="flex flex-col gap-1.5">
                {skill.user_category && (
                  <div>
                    <span className="ms-chip bg-ms-blue/10 text-ms-blue">
                      {skill.user_category}
                    </span>
                    {userCategoryDiverges && (
                      <span className="ml-2 text-[11px] text-muted">
                        overrode classifier
                      </span>
                    )}
                  </div>
                )}
                {(skill.user_tags ?? []).length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {skill.user_tags.map((t) => (
                      <span
                        key={t}
                        className="ms-chip bg-ms-blue/10 text-ms-blue"
                      >
                        {t}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </Row>
          )}
          <Row label="Quality score">
            {cls?.quality_score != null ? (
              <span className="font-mono text-sm font-semibold text-ink">
                {cls.quality_score}
                <span className="text-xs font-normal text-muted">/100</span>
              </span>
            ) : (
              "—"
            )}
          </Row>
          {cls?.classifier_version && (
            <Row label="Classifier">
              <code className="font-mono text-[11px] text-muted">
                {cls.classifier_version}
              </code>
            </Row>
          )}
        </dl>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="font-display text-[11px] font-bold uppercase tracking-[0.18em] text-ink-2">
          Bundle
        </h2>
        <dl className="flex flex-col gap-3">
          <Row label="Checksum (sha256)">
            {bundle?.checksum_sha256 ? (
              <code
                className="font-mono text-xs"
                title={bundle.checksum_sha256}
              >
                {bundle.checksum_sha256.slice(0, 16)}…
              </code>
            ) : (
              "—"
            )}
          </Row>
          <Row label="Size">{formatBytes(bundle?.size_bytes)}</Row>
          <Row label="File count">{bundle?.file_count ?? "—"}</Row>
        </dl>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="font-display text-[11px] font-bold uppercase tracking-[0.18em] text-ink-2">
          Lifecycle
        </h2>
        <dl className="flex flex-col gap-3">
          <Row label="Uploaded">{formatTimestamp(skill.uploaded_at)}</Row>
          {skill.approved_at && (
            <Row label="Approved">{formatTimestamp(skill.approved_at)}</Row>
          )}
        </dl>
      </section>
    </aside>
  );
}
