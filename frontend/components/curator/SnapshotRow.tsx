"use client";

import { useState } from "react";

import { RollbackModal } from "./RollbackModal";

import type { SnapshotListItem } from "@/lib/api/types";

function humanBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`;
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export function SnapshotRow({
  snap,
  onRolledBack,
}: {
  snap: SnapshotListItem;
  onRolledBack: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <tr className="border-b">
      <td className="py-2 font-mono text-xs">{snap.name}</td>
      <td className="text-xs">{formatDate(snap.captured_at)}</td>
      <td className="text-xs">{snap.skills_count}</td>
      <td className="text-xs">{humanBytes(snap.size_bytes)}</td>
      <td>
        <button
          onClick={() => setOpen(true)}
          className="rounded bg-gold px-3 py-1 text-xs font-semibold text-ink hover:brightness-95"
        >
          Rollback…
        </button>
        <RollbackModal
          open={open}
          snap={snap}
          onClose={() => setOpen(false)}
          onSuccess={() => {
            setOpen(false);
            onRolledBack();
          }}
        />
      </td>
    </tr>
  );
}
