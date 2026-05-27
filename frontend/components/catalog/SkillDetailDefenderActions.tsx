"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { api } from "@/lib/api/client";
import { useAdminProbe } from "@/lib/hooks/useAdminProbe";
import type { SkillDetail } from "@/lib/api/types";

const MIN_JUSTIFICATION = 20;

/**
 * Admin-only defender controls for the catalog detail page (M5-4).
 *
 * Visible only when:
 *   - caller has `admin` role (probed via `useAdminProbe`), AND
 *   - `defender_status` is `flagged` or `failed`.
 *
 * Renders two buttons that each open a two-step modal:
 *
 *   1. **Override** — flips `defender_status` back to `clean` so the
 *      regular approve flow can run. Requires a justification of
 *      ≥20 chars. Backend: `POST /v1/admin/skills/{id}/defender-override`.
 *
 *   2. **Quarantine** — moves the bundle to the terminal `quarantine/`
 *      blob container. Only offered when `defender_status === 'flagged'`
 *      per the plan (a `failed` scan should be re-queued, not
 *      quarantined). Backend: `POST /v1/admin/skills/{id}/quarantine`.
 *
 * Two-step UX: both modals require typing the skill_id into a confirm
 * field before the destructive button enables. Mirrors the existing
 * archive flow (`SkillDetailAdminActions`).
 */
export function SkillDetailDefenderActions({ skill }: { skill: SkillDetail }) {
  const { isAdmin, isLoading } = useAdminProbe();
  const router = useRouter();
  const [mode, setMode] = useState<"override" | "quarantine" | null>(null);
  const [justification, setJustification] = useState("");
  const [confirmName, setConfirmName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (isLoading || !isAdmin) return null;

  const status = skill.defender_status;
  const showAny = status === "flagged" || status === "failed";
  if (!showAny) return null;

  // Quarantine is offered ONLY for `flagged` (backend rejects with
  // DEFENDER_NOT_FLAGGED otherwise; plan §3).
  const canQuarantine = status === "flagged";

  function close() {
    if (busy) return;
    setMode(null);
    setJustification("");
    setConfirmName("");
    setError(null);
  }

  const trimmed = justification.trim();
  const justOk = trimmed.length >= MIN_JUSTIFICATION;
  const nameOk = confirmName === skill.skill_id;
  const ready = justOk && nameOk && !busy;

  async function submit() {
    if (!mode) return;
    setBusy(true);
    setError(null);
    try {
      if (mode === "override") {
        await api.admin.defenderOverride(skill.skill_id, trimmed);
        // Refresh the page so the panel re-renders with the new
        // defender_status. router.refresh() reuses the existing route
        // tree, avoiding a full reload.
        router.refresh();
        close();
      } else {
        await api.admin.quarantine(skill.skill_id, trimmed);
        // After quarantine the public catalog drops the skill; bounce
        // back to the catalog list (mirrors the archive flow).
        router.push("/catalog");
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const isOverride = mode === "override";
  const title = isOverride
    ? `Override defender finding on "${skill.name}"?`
    : `Quarantine "${skill.name}"?`;
  const confirmText = isOverride ? "Override finding" : "Quarantine skill";

  return (
    <div
      className="flex flex-col items-end gap-2"
      data-testid="defender-admin-actions"
    >
      <div className="flex gap-2">
        <button
          onClick={() => setMode("override")}
          data-testid="defender-override-button"
          className="rounded bg-ms-blue px-3 py-1.5 text-xs text-white hover:brightness-95"
          title="Override defender finding with a justification (audit-logged)"
        >
          Override defender
        </button>
        {canQuarantine && (
          <button
            onClick={() => setMode("quarantine")}
            data-testid="defender-quarantine-button"
            className="rounded bg-ms-red px-3 py-1.5 text-xs text-white hover:brightness-95"
            title="Move bundle to quarantine (terminal, recoverable only by retention window)"
          >
            Quarantine
          </button>
        )}
      </div>

      {mode && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
          role="dialog"
          aria-modal="true"
          aria-label={title}
          data-testid={
            isOverride ? "defender-override-modal" : "defender-quarantine-modal"
          }
        >
          <div className="w-full max-w-md rounded-lg bg-white p-5 shadow-lg">
            <h2 className="text-lg font-semibold text-gray-900">{title}</h2>
            <div className="mt-3 space-y-3 text-sm text-gray-700">
              {isOverride ? (
                <p>
                  Flips <code>defender_status</code> back to{" "}
                  <strong>clean</strong> so the regular approve flow can
                  proceed. The original finding is preserved on the audit
                  trail.
                </p>
              ) : (
                <p>
                  Moves the bundle to the <code>quarantine/</code> container
                  and flips status to <strong>quarantined</strong> (terminal).
                  The bundle bytes are auto-deleted after the configured
                  retention window. This action is the right one when you
                  believe the finding is real.
                </p>
              )}
              <label className="block">
                <span className="text-xs font-medium text-gray-700">
                  Justification (required, audit-logged, min{" "}
                  {MIN_JUSTIFICATION} chars)
                </span>
                <textarea
                  value={justification}
                  onChange={(e) => setJustification(e.target.value)}
                  rows={3}
                  placeholder={
                    isOverride
                      ? "e.g. reviewed setup.sh manually; curl|sh is intended bootstrap"
                      : "e.g. bundle exfiltrates env vars via curl"
                  }
                  className="mt-1 w-full rounded border border-gray-300 px-2 py-1 text-sm"
                  data-testid="defender-justification-input"
                />
                <span className="mt-0.5 block text-[11px] text-muted">
                  {trimmed.length}/{MIN_JUSTIFICATION}
                </span>
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
                  data-testid="defender-confirm-name"
                />
              </label>
              {error && (
                <div className="text-[11px] text-danger-fg">{error}</div>
              )}
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button
                onClick={close}
                disabled={busy}
                className="rounded border border-gray-300 bg-white px-3 py-1 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={() => void submit()}
                disabled={!ready}
                data-testid="defender-confirm-button"
                className={`rounded px-3 py-1 text-sm text-white disabled:opacity-50 ${
                  isOverride
                    ? "bg-ms-blue hover:brightness-95"
                    : "bg-ms-red hover:brightness-95"
                }`}
              >
                {busy ? "Working…" : confirmText}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
