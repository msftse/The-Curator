"use client";

import { useState } from "react";

import { Confirm } from "@/components/Confirm";
import { StatusBadge } from "@/components/StatusBadge";
import { api } from "@/lib/api/client";
import type { SkillListItem } from "@/lib/api/types";

export function SkillAdminRow({
  skill,
  onMutated,
}: {
  skill: SkillListItem;
  onMutated: () => Promise<void> | void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [archiveReason, setArchiveReason] = useState("");
  const [confirmName, setConfirmName] = useState("");

  async function toggle() {
    setBusy("pin");
    setError(null);
    try {
      if (skill.pinned) await api.curator.unpin(skill.skill_id);
      else await api.curator.pin(skill.skill_id);
      await onMutated();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  async function restore() {
    setBusy("restore");
    setError(null);
    try {
      await api.curator.restore(skill.skill_id);
      await onMutated();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  async function archive() {
    setBusy("archive");
    setError(null);
    try {
      await api.admin.archive(skill.skill_id, archiveReason.trim());
      setArchiveOpen(false);
      setArchiveReason("");
      setConfirmName("");
      await onMutated();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  // Archive button is only meaningful for `approved` skills. Pinned skills
  // are rejected server-side too (SKILL_PINNED), but we disable the button
  // to make the contract obvious.
  const canArchive = skill.status === "approved" && !skill.pinned;

  // Make the confirm intentionally friction-y: typed-name confirmation is
  // the same pattern GitHub uses for repo archive. Reason must be non-empty.
  const confirmReady =
    archiveReason.trim().length > 0 && confirmName === skill.skill_id;

  return (
    <tr className="border-b">
      <td className="py-2">
        <div className="font-medium">{skill.name}</div>
        <div className="font-mono text-xs text-gray-500">{skill.skill_id}</div>
      </td>
      <td>
        <StatusBadge status={skill.status} />
      </td>
      <td>
        <button
          disabled={busy !== null}
          onClick={() => void toggle()}
          className={
            skill.pinned
              ? "rounded bg-violet px-3 py-1 text-xs text-white hover:bg-violet-dark disabled:opacity-50"
              : "rounded border border-line-2 px-3 py-1 text-xs text-ink-2 hover:bg-bg-2 disabled:opacity-50"
          }
        >
          {skill.pinned ? "Pinned · click to unpin" : "Pin"}
        </button>
      </td>
      <td>
        <div className="flex items-center gap-2">
          {skill.status === "archived" ? (
            <button
              disabled={busy !== null}
              onClick={() => void restore()}
              className="rounded bg-ms-green px-3 py-1 text-xs text-white hover:brightness-95 disabled:opacity-50"
            >
              Restore
            </button>
          ) : null}
          {canArchive ? (
            <button
              disabled={busy !== null}
              onClick={() => setArchiveOpen(true)}
              className="rounded bg-ms-red px-3 py-1 text-xs text-white hover:brightness-95 disabled:opacity-50"
              title="Archive this skill (recoverable via Restore)"
            >
              Archive
            </button>
          ) : null}
          {skill.status !== "archived" && !canArchive ? (
            <span className="text-xs text-muted">—</span>
          ) : null}
        </div>
        {error ? (
          <div className="mt-1 text-xs text-danger-fg">{error}</div>
        ) : null}
      </td>

      <Confirm
        open={archiveOpen}
        title={`Archive "${skill.name}"?`}
        destructive
        confirmText="Archive skill"
        confirmDisabled={!confirmReady || busy !== null}
        onConfirm={archive}
        onClose={() => {
          if (busy !== null) return;
          setArchiveOpen(false);
          setArchiveReason("");
          setConfirmName("");
          setError(null);
        }}
        body={
          <div className="space-y-3">
            <p>
              This will flip status to <strong>archived</strong> and copy the
              bundle to <code>archive/</code>. Published bytes are kept for
              defense-in-depth. The skill will disappear from the public
              catalog. You can restore it later from this page.
            </p>
            <label className="block">
              <span className="text-xs font-medium text-gray-700">
                Reason (required, audit-logged)
              </span>
              <input
                type="text"
                value={archiveReason}
                onChange={(e) => setArchiveReason(e.target.value)}
                placeholder="e.g. duplicate of foo-bar"
                className="mt-1 w-full rounded border border-gray-300 px-2 py-1 text-sm"
              />
            </label>
            <label className="block">
              <span className="text-xs font-medium text-gray-700">
                Type <code className="text-xs">{skill.skill_id}</code> to
                confirm
              </span>
              <input
                type="text"
                value={confirmName}
                onChange={(e) => setConfirmName(e.target.value)}
                className="mt-1 w-full rounded border border-gray-300 px-2 py-1 font-mono text-sm"
              />
            </label>
          </div>
        }
      />
    </tr>
  );
}
