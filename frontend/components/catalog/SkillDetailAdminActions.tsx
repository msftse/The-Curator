"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { Confirm } from "@/components/Confirm";
import { api } from "@/lib/api/client";
import { useAdminProbe } from "@/lib/hooks/useAdminProbe";
import type { SkillDetail } from "@/lib/api/types";

/**
 * Admin-only "Archive this skill" action for the catalog detail page.
 *
 * Mirrors the curator skills-admin row: confirm modal with typed-name +
 * required reason. Soft delete — restorable via the curator skills page.
 * Hidden unless the caller has admin role (probed via `useAdminProbe`).
 */
export function SkillDetailAdminActions({ skill }: { skill: SkillDetail }) {
  const { isAdmin, isLoading } = useAdminProbe();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [confirmName, setConfirmName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (isLoading || !isAdmin) return null;

  // Only meaningful for approved skills. Pinned skills would be rejected
  // server-side too (SKILL_PINNED).
  const canArchive = skill.status === "approved" && !skill.pinned;
  if (!canArchive && skill.status !== "archived") return null;

  const confirmReady = reason.trim().length > 0 && confirmName === skill.skill_id;

  async function doArchive() {
    setBusy(true);
    setError(null);
    try {
      await api.admin.archive(skill.skill_id, reason.trim());
      setOpen(false);
      // After archive the public catalog drops this skill; bounce back to
      // the catalog list so the operator sees the new state of the world.
      router.push("/catalog");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col items-end gap-1">
      {skill.status === "archived" ? (
        <span className="rounded bg-bg-2 px-2 py-1 text-[11px] text-muted">
          Archived · restore from{" "}
          <a
            href="/admin/curator/skills"
            className="text-ms-blue hover:underline"
          >
            curator
          </a>
        </span>
      ) : (
        <button
          onClick={() => setOpen(true)}
          className="rounded bg-ms-red px-3 py-1.5 text-xs text-white hover:brightness-95"
          title="Archive this skill (admin only, recoverable)"
        >
          Archive (admin)
        </button>
      )}
      {error ? (
        <div className="text-[11px] text-danger-fg">{error}</div>
      ) : null}

      <Confirm
        open={open}
        title={`Archive "${skill.name}"?`}
        destructive
        confirmText="Archive skill"
        confirmDisabled={!confirmReady || busy}
        onConfirm={doArchive}
        onClose={() => {
          if (busy) return;
          setOpen(false);
          setReason("");
          setConfirmName("");
          setError(null);
        }}
        body={
          <div className="space-y-3">
            <p>
              This will flip status to <strong>archived</strong> and copy the
              bundle to <code>archive/</code>. Published bytes are kept for
              defense-in-depth. The skill will disappear from the public
              catalog. Restore from the curator skills page.
            </p>
            <label className="block">
              <span className="text-xs font-medium text-gray-700">
                Reason (required, audit-logged)
              </span>
              <input
                type="text"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
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
    </div>
  );
}
