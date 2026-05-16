"use client";

import { useState } from "react";

import { Confirm } from "@/components/Confirm";
import { api } from "@/lib/api/client";
import type { CuratorRunRecord, CuratorStatus } from "@/lib/api/types";

export interface RunControlsProps {
  status: CuratorStatus | undefined;
  onMutated: () => Promise<void> | void;
  onDryRun: (rec: CuratorRunRecord) => void;
  onRun: (rec: CuratorRunRecord) => void;
}

export function RunControls({
  status,
  onMutated,
  onDryRun,
  onRun,
}: RunControlsProps) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmRun, setConfirmRun] = useState(false);

  async function pause() {
    setBusy("pause");
    try {
      await api.curator.pause();
      await onMutated();
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }
  async function resume() {
    setBusy("resume");
    try {
      await api.curator.resume();
      await onMutated();
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }
  async function dryRun() {
    setBusy("dry");
    try {
      const rec = await api.curator.run({ dryRun: true });
      onDryRun(rec);
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }
  async function doRun() {
    setBusy("run");
    try {
      const rec = await api.curator.run({});
      onRun(rec);
      await onMutated();
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
      setConfirmRun(false);
    }
  }

  const paused = status?.paused ?? false;

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        <button
          disabled={busy !== null || paused}
          onClick={() => void pause()}
          className="rounded bg-gold px-3 py-1 text-sm font-semibold text-ink hover:brightness-95 disabled:opacity-50"
        >
          Pause
        </button>
        <button
          disabled={busy !== null || !paused}
          onClick={() => void resume()}
          className="rounded bg-ms-blue px-3 py-1 text-sm text-white hover:bg-ms-blue-dark disabled:opacity-50"
        >
          Resume
        </button>
        <button
          disabled={busy !== null}
          onClick={() => void dryRun()}
          className="rounded bg-ink-2 px-3 py-1 text-sm text-cream hover:brightness-110 disabled:opacity-50"
        >
          {busy === "dry" ? "Running dry-run…" : "Dry-run"}
        </button>
        <button
          disabled={busy !== null}
          onClick={() => setConfirmRun(true)}
          className="rounded bg-ms-green px-3 py-1 text-sm text-white hover:brightness-95 disabled:opacity-50"
        >
          Run
        </button>
      </div>
      {error ? (
        <div className="ms-msgbar-danger text-xs">
          {error}
        </div>
      ) : null}

      <Confirm
        open={confirmRun}
        title="Run curator?"
        body={
          <div>
            This writes a snapshot and may transition skills (approved → stale,
            stale → archived). Archived skills can be restored. No data is ever
            deleted.
          </div>
        }
        confirmText="Run curator"
        onConfirm={doRun}
        onClose={() => setConfirmRun(false)}
      />
    </div>
  );
}
