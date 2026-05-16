"use client";

import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api/client";
import type { UploadResponse } from "@/lib/api/types";

const MAX_USER_TAGS = 8;
const MAX_TAG_LEN = 40;

export default function UploadPage() {
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<UploadResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const [categories, setCategories] = useState<string[]>([]);
  const [category, setCategory] = useState<string>("");
  const [tags, setTags] = useState<string[]>([]);
  const [tagDraft, setTagDraft] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    api.meta
      .categories()
      .then((list) => {
        if (!cancelled) setCategories(list);
      })
      .catch(() => {
        // Non-fatal: dropdown stays empty, user can still submit without
        // a category. Backend's user_category=None path is supported.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function addTagFromDraft() {
    const raw = tagDraft.trim();
    if (!raw) return;
    if (raw.length > MAX_TAG_LEN) {
      setError(`Tag too long (max ${MAX_TAG_LEN} chars): ${raw}`);
      return;
    }
    const lower = raw.toLowerCase();
    if (tags.some((t) => t.toLowerCase() === lower)) {
      setTagDraft("");
      return;
    }
    if (tags.length >= MAX_USER_TAGS) {
      setError(`Maximum ${MAX_USER_TAGS} tags.`);
      return;
    }
    setTags([...tags, raw]);
    setTagDraft("");
    setError(null);
  }

  function onTagKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" || e.key === "," || e.key === "Tab") {
      if (tagDraft.trim()) {
        e.preventDefault();
        addTagFromDraft();
      }
    } else if (e.key === "Backspace" && !tagDraft && tags.length) {
      setTags(tags.slice(0, -1));
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      // Flush any pending tag draft so the user doesn't lose it on submit.
      const finalTags = tagDraft.trim()
        ? [
            ...tags,
            ...(tags.some((t) => t.toLowerCase() === tagDraft.trim().toLowerCase())
              ? []
              : [tagDraft.trim()]),
          ]
        : tags;
      const form = new FormData();
      form.append("file", file);
      if (category) form.append("category", category);
      if (finalTags.length) form.append("tags", finalTags.join(","));
      const r = await api.uploads.create(form);
      setResult(r);
      setFile(null);
      setTags([]);
      setTagDraft("");
      setCategory("");
      if (inputRef.current) inputRef.current.value = "";
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  function onDrop(e: React.DragEvent<HTMLLabelElement>) {
    e.preventDefault();
    setDragOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) setFile(f);
  }

  return (
    <div className="mx-auto max-w-3xl px-6 py-12">
      <header className="mb-8 space-y-2 text-center">
        <span className="ms-eyebrow-blue">Contributor</span>
        <h1 className="font-display text-[clamp(28px,4vw,40px)] font-bold tracking-ms-display text-ink">
          Submit a skill
        </h1>
        <p className="mx-auto max-w-md text-sm leading-relaxed text-muted">
          Drop in a single <code className="rounded bg-bg-2 px-1 py-0.5 font-mono text-[12px]">SKILL.md</code>{" "}
          or a <code className="rounded bg-bg-2 px-1 py-0.5 font-mono text-[12px]">tar / tar.gz</code> bundle.
          Maximum size 10&nbsp;MB.
        </p>
      </header>

      <div className="my-8 ms-divider">
        <div className="ms-divider-line" />
        <div className="ms-divider-icon">◆</div>
        <div className="ms-divider-line" />
      </div>

      <form onSubmit={submit} className="ms-card space-y-5 p-7">
        <label
          htmlFor="skill-file"
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
          className={
            "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-md border-2 border-dashed p-10 text-center transition-colors duration-150 " +
            (dragOver
              ? "border-ms-blue bg-ms-blue/[0.04] text-ms-blue"
              : "border-line-2 bg-bg text-muted hover:border-ms-blue hover:bg-ms-blue/[0.04] hover:text-ms-blue")
          }
        >
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-ms-blue/10 text-ms-blue">
            <svg
              aria-hidden
              width="22"
              height="22"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M12 16V4M7 9l5-5 5 5" />
              <path d="M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" />
            </svg>
          </div>
          <div className="font-display text-[15px] font-semibold text-ink">
            {file ? file.name : "Drop SKILL.md or click to choose"}
          </div>
          <small className="text-xs text-muted">
            For multi-file skills, package as <code className="font-mono">tar.gz</code>.
            Accepted: .md, .tar, .tar.gz, .tgz · up to 10&nbsp;MB
          </small>
          <input
            id="skill-file"
            ref={inputRef}
            type="file"
            accept=".md,.tar,.gz,.tgz"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="sr-only"
          />
        </label>

        {file && (
          <div className="flex items-center justify-between rounded-md border border-line bg-bg-2 px-3.5 py-2.5 text-sm">
            <div className="flex items-center gap-2 text-ink-2">
              <FileIcon />
              <span className="font-semibold">{file.name}</span>
              <span className="text-xs text-muted">
                {(file.size / 1024).toFixed(1)} KB
              </span>
            </div>
            <button
              type="button"
              onClick={() => {
                setFile(null);
                if (inputRef.current) inputRef.current.value = "";
              }}
              className="rounded px-2 py-0.5 text-xs text-muted transition-colors hover:bg-white hover:text-ink"
            >
              Remove
            </button>
          </div>
        )}

        <div className="grid gap-5 sm:grid-cols-2">
          <div className="space-y-1.5">
            <label
              htmlFor="skill-category"
              className="block font-display text-[13px] font-semibold text-ink"
            >
              Category
              <span className="ml-1 font-normal text-muted">(optional)</span>
            </label>
            <select
              id="skill-category"
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="w-full rounded-md border border-line bg-bg px-3 py-2 text-sm text-ink focus:border-ms-blue focus:outline-none focus:ring-1 focus:ring-ms-blue"
            >
              <option value="">— Let the classifier decide —</option>
              {categories.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
            <p className="text-xs text-muted">
              Your choice overrides the auto-classifier.
            </p>
          </div>

          <div className="space-y-1.5">
            <label
              htmlFor="skill-tags"
              className="block font-display text-[13px] font-semibold text-ink"
            >
              Tags
              <span className="ml-1 font-normal text-muted">
                (optional, up to {MAX_USER_TAGS})
              </span>
            </label>
            <div
              className="flex min-h-[42px] flex-wrap items-center gap-1.5 rounded-md border border-line bg-bg px-2 py-1.5 focus-within:border-ms-blue focus-within:ring-1 focus-within:ring-ms-blue"
              onClick={() => document.getElementById("skill-tags")?.focus()}
            >
              {tags.map((t) => (
                <span
                  key={t}
                  className="inline-flex items-center gap-1 rounded-full bg-ms-blue/10 px-2 py-0.5 text-xs font-medium text-ms-blue"
                >
                  {t}
                  <button
                    type="button"
                    aria-label={`Remove ${t}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      setTags(tags.filter((x) => x !== t));
                    }}
                    className="rounded text-ms-blue/70 hover:text-ms-blue"
                  >
                    ×
                  </button>
                </span>
              ))}
              <input
                id="skill-tags"
                type="text"
                value={tagDraft}
                onChange={(e) => setTagDraft(e.target.value)}
                onKeyDown={onTagKeyDown}
                onBlur={() => tagDraft.trim() && addTagFromDraft()}
                placeholder={tags.length ? "" : "kubernetes, helm, …"}
                className="min-w-[120px] flex-1 border-none bg-transparent px-1 py-0.5 text-sm text-ink focus:outline-none"
                disabled={tags.length >= MAX_USER_TAGS}
              />
            </div>
            <p className="text-xs text-muted">
              Press Enter or comma to add. Merged with the classifier's tags.
            </p>
          </div>
        </div>

        <div className="flex items-center justify-between gap-3 border-t border-line pt-4">
          <small className="text-xs text-muted">
            Stored on Cosmos &amp; Blob. Audit-trailed.
          </small>
          <button
            type="submit"
            disabled={!file || busy}
            className="ms-btn-primary"
          >
            {busy && <Spinner />}
            {busy ? "Uploading…" : "Submit to hub →"}
          </button>
        </div>
      </form>

      {error && (
        <div className="mt-5 ms-msgbar-danger">
          <DotIcon />
          <span>{error}</span>
        </div>
      )}

      {result && (
        <div className="mt-5 ms-msgbar-success">
          <CheckIcon />
          <div className="space-y-1">
            <div>
              Uploaded as <code className="font-mono">{result.skill_id}</code> (
              {result.version}) — status{" "}
              <strong className="font-semibold">{result.status}</strong>,
              classifier{" "}
              <strong className="font-semibold">
                {result.classifier_status}
              </strong>
              .
            </div>
            <div>
              Watch progress on{" "}
              <a
                className="font-semibold underline underline-offset-2"
                href="/my-submissions"
              >
                My submissions
              </a>
              .
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function FileIcon() {
  return (
    <svg
      aria-hidden
      width="16"
      height="16"
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M5 3h7l4 4v10a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z" />
      <path d="M12 3v4h4" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg
      aria-hidden
      width="16"
      height="16"
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="mt-0.5 shrink-0"
    >
      <path d="M4 10l4 4 8-8" />
    </svg>
  );
}

function DotIcon() {
  return (
    <svg
      aria-hidden
      width="16"
      height="16"
      viewBox="0 0 20 20"
      fill="currentColor"
      className="mt-0.5 shrink-0"
    >
      <circle cx="10" cy="10" r="8" opacity="0.15" />
      <path
        d="M10 5v6"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
      <circle cx="10" cy="14" r="1" />
    </svg>
  );
}

function Spinner() {
  return (
    <svg
      aria-hidden
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      className="animate-spin"
    >
      <circle
        cx="12"
        cy="12"
        r="9"
        stroke="currentColor"
        strokeWidth="3"
        opacity="0.25"
      />
      <path
        d="M21 12a9 9 0 0 0-9-9"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
      />
    </svg>
  );
}
