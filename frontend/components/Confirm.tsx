"use client";

import { useEffect, useState } from "react";

export interface ConfirmProps {
  open: boolean;
  title: string;
  body: React.ReactNode;
  confirmText: string;
  confirmDisabled?: boolean;
  destructive?: boolean;
  onConfirm: () => void | Promise<void>;
  onClose: () => void;
}

export function Confirm({
  open,
  title,
  body,
  confirmText,
  confirmDisabled,
  destructive,
  onConfirm,
  onClose,
}: ConfirmProps) {
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) setBusy(false);
  }, [open]);

  if (!open) return null;

  async function handle() {
    setBusy(true);
    try {
      await onConfirm();
    } finally {
      setBusy(false);
    }
  }

  const confirmClass = destructive
    ? "bg-ms-red hover:brightness-95"
    : "bg-ms-green hover:brightness-95";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div className="w-full max-w-md rounded-lg bg-white p-5 shadow-lg">
        <h2 className="text-lg font-semibold text-gray-900">{title}</h2>
        <div className="mt-3 text-sm text-gray-700">{body}</div>
        <div className="mt-5 flex justify-end gap-2">
          <button
            onClick={onClose}
            disabled={busy}
            className="rounded border border-gray-300 bg-white px-3 py-1 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={() => void handle()}
            disabled={busy || confirmDisabled}
            className={`rounded px-3 py-1 text-sm text-white disabled:opacity-50 ${confirmClass}`}
          >
            {busy ? "Working…" : confirmText}
          </button>
        </div>
      </div>
    </div>
  );
}
