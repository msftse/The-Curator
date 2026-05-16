"use client";

import { useState } from "react";

import { api } from "@/lib/api/client";
import type { UploadResponse } from "@/lib/api/types";

export default function UploadPage() {
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<UploadResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const r = await api.uploads.create(form);
      setResult(r);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Upload a skill</h1>
      <p className="text-sm text-gray-600">
        Drop in a single SKILL.md or a tar/tar.gz bundle (max 10 MB).
      </p>
      <form onSubmit={submit} className="space-y-3">
        <input
          type="file"
          accept=".md,.tar,.gz,.tgz"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          className="block w-full rounded border border-gray-300 bg-white p-2"
        />
        <button
          type="submit"
          disabled={!file || busy}
          className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          {busy ? "Uploading…" : "Upload"}
        </button>
      </form>
      {error && (
        <div className="rounded border border-rose-300 bg-rose-50 p-3 text-sm text-rose-800">
          {error}
        </div>
      )}
      {result && (
        <div className="rounded border border-emerald-300 bg-emerald-50 p-3 text-sm text-emerald-900">
          <p>
            Uploaded as <code>{result.skill_id}</code> ({result.version}) —
            status: <strong>{result.status}</strong>, classifier:{" "}
            <strong>{result.classifier_status}</strong>
          </p>
          <p className="mt-2">
            Watch progress on{" "}
            <a className="underline" href="/my-submissions">
              /my-submissions
            </a>
            .
          </p>
        </div>
      )}
    </div>
  );
}
