"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Confirm } from "@/components/Confirm";
import { api } from "@/lib/api/client";
import type { SnapshotListItem } from "@/lib/api/types";

export function RollbackModal({
  open,
  snap,
  onClose,
  onSuccess,
}: {
  open: boolean;
  snap: SnapshotListItem;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const router = useRouter();
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setTyped("");
      setError(null);
    }
  }, [open]);

  const matches = typed.trim() === snap.name;

  async function doRollback() {
    setError(null);
    try {
      await api.curator.rollback({ id: snap.name });
      onSuccess();
      router.push(
        `/admin/curator?rolled_back=${encodeURIComponent(snap.name)}`,
      );
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <Confirm
      open={open}
      title="Rollback to snapshot"
      destructive
      body={
        <div className="space-y-3">
          <div>
            This rolls the catalog back to{" "}
            <code className="font-mono text-xs">{snap.name}</code>. A
            pre-rollback snapshot is automatically captured first.
          </div>
          <div className="text-xs text-gray-600">
            Captured: {snap.captured_at} · Skills: {snap.skills_count}
          </div>
          <label className="block text-xs font-medium text-gray-700">
            Type the snapshot name to confirm:
            <input
              autoFocus
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              className="mt-1 block w-full rounded border border-gray-300 px-2 py-1 font-mono text-xs"
              placeholder={snap.name}
            />
          </label>
          {error ? (
            <div className="rounded border border-rose-300 bg-rose-50 p-2 text-xs text-rose-800">
              {error}
            </div>
          ) : null}
        </div>
      }
      confirmText="Rollback"
      confirmDisabled={!matches}
      onConfirm={doRollback}
      onClose={onClose}
    />
  );
}
