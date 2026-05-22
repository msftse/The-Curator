"use client";

import { useState } from "react";

import { Confirm } from "@/components/Confirm";
import { api } from "@/lib/api/client";
import type { JanitorResult } from "@/lib/api/types";

export interface JanitorPanelProps {
  onMutated?: () => Promise<void> | void;
}

export function JanitorPanel({ onMutated }: JanitorPanelProps) {
  const [busy, setBusy] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<JanitorResult | null>(null);
  const [ranAt, setRanAt] = useState<string | null>(null);

  async function run() {
    setBusy(true);
    try {
      const res = await api.curator.janitor();
      setResult(res);
      setRanAt(new Date().toLocaleTimeString());
      setError(null);
      if (onMutated) await onMutated();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
      setConfirmOpen(false);
    }
  }

  return (
    <div className="space-y-2 rounded border border-gray-200 bg-white p-3">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-gray-800">
            Queue janitor
          </h3>
          <p className="text-xs text-gray-500">
            Sweeps stale entries from classifier and defender queues and
            re-enqueues them. Safe to run on demand.
          </p>
        </div>
        <button
          disabled={busy}
          onClick={() => setConfirmOpen(true)}
          className="rounded bg-ink-2 px-3 py-1 text-sm text-cream hover:brightness-110 disabled:opacity-50"
        >
          {busy ? "Sweeping…" : "Run janitor"}
        </button>
      </div>

      {error ? (
        <div className="ms-msgbar-danger text-xs">{error}</div>
      ) : null}

      {result ? (
        <div className="mt-2 text-xs">
          <div className="text-gray-500">Last run: {ranAt}</div>
          <table className="mt-1 w-full border-collapse">
            <thead>
              <tr className="text-left text-gray-500">
                <th className="py-1 pr-4 font-medium">Queue</th>
                <th className="py-1 pr-4 font-medium">Scanned</th>
                <th className="py-1 font-medium">Re-queued</th>
              </tr>
            </thead>
            <tbody className="font-mono text-gray-800">
              <tr className="border-t border-gray-100">
                <td className="py-1 pr-4">classifier</td>
                <td className="py-1 pr-4">{result.classifier.scanned}</td>
                <td className="py-1">{result.classifier.requeued}</td>
              </tr>
              <tr className="border-t border-gray-100">
                <td className="py-1 pr-4">defender</td>
                <td className="py-1 pr-4">{result.defender.scanned}</td>
                <td className="py-1">{result.defender.requeued}</td>
              </tr>
            </tbody>
          </table>
        </div>
      ) : null}

      <Confirm
        open={confirmOpen}
        title="Run queue janitor?"
        body={
          <div>
            This scans the classifier and defender queues for stale in-flight
            items and re-enqueues them. Non-destructive.
          </div>
        }
        confirmText="Run janitor"
        onConfirm={run}
        onClose={() => setConfirmOpen(false)}
      />
    </div>
  );
}
