"use client";

import { useEffect, useRef, useState } from "react";

import { isOidc } from "@/lib/auth/msal";
import { signOut, useAuthAccount } from "@/lib/auth/AuthProvider";

const PRESET_USERS = ["alice@org", "bob@org", "manager@org", "admin@org"];

const ACCENT_PALETTE = [
  "bg-ms-blue",
  "bg-ms-green",
  "bg-ms-red",
  "bg-ms-yellow",
];

function accentFor(email: string): string {
  let hash = 0;
  for (const ch of email) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return ACCENT_PALETTE[hash % ACCENT_PALETTE.length];
}

function initialsFor(email: string): string {
  const local = email.split("@")[0] ?? email;
  const parts = local.split(/[._-]/).filter(Boolean);
  if (parts.length >= 2) {
    return (parts[0]![0] + parts[1]![0]!).toUpperCase();
  }
  return local.slice(0, 2).toUpperCase();
}

export function UserPicker() {
  // Runtime branch — the two render paths share no state, so we route
  // before any hooks fire to keep rules-of-hooks happy. `isOidc()` is
  // stable within a page lifetime (runtime env doesn't change mid-session).
  if (isOidc()) {
    return <OidcAccountChip />;
  }
  return <StubPersonaPicker />;
}

/**
 * OIDC mode: shows the signed-in account with a dropdown offering sign-out.
 * No persona switching — identity is whatever Entra issued.
 */
function OidcAccountChip() {
  const { email, name } = useAuthAccount();
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onClick(e: MouseEvent) {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    window.addEventListener("mousedown", onClick);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onClick);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const display = email ?? "Signed in";
  const accent = accentFor(display);
  const isYellow = accent === "bg-ms-yellow";

  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        className="flex items-center gap-2 rounded-full border border-line-2 bg-white py-1 pl-1 pr-3 text-sm font-semibold text-ink-2 shadow-ms-sm transition-colors duration-150 hover:bg-bg-2"
      >
        <span
          className={
            "flex h-7 w-7 items-center justify-center rounded-full text-[11px] font-bold " +
            accent +
            " " +
            (isYellow ? "text-ink" : "text-white")
          }
          aria-hidden
        >
          {initialsFor(display)}
        </span>
        <span className="hidden sm:inline">{name ?? display}</span>
        <svg
          aria-hidden
          width="12"
          height="12"
          viewBox="0 0 12 12"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M2.5 4.5l3.5 3.5 3.5-3.5" />
        </svg>
      </button>

      {open && (
        <div
          role="menu"
          className="absolute right-0 z-40 mt-2 w-64 overflow-hidden rounded-lg border border-line bg-white shadow-ms-lg"
        >
          <div className="border-b border-line px-3.5 py-2.5">
            <div className="text-sm font-semibold text-ink">
              {name ?? display}
            </div>
            {name && email && (
              <div className="truncate text-xs text-muted">{email}</div>
            )}
          </div>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              signOut();
            }}
            className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left text-sm text-ink-2 transition-colors duration-150 hover:bg-bg-2"
          >
            <svg
              aria-hidden
              width="14"
              height="14"
              viewBox="0 0 20 20"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M13 4h3a1 1 0 011 1v10a1 1 0 01-1 1h-3" />
              <path d="M7 10h9M12 6l4 4-4 4" />
            </svg>
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}

/**
 * Stub mode: the original persona picker that writes `x-user-email` to
 * localStorage. Used for local-dev with `LOCAL_DEV=1` on the backend.
 */
function StubPersonaPicker() {
  const [user, setUser] = useState<string>("alice@org");
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const stored = window.localStorage.getItem("x-user-email");
    if (stored) setUser(stored);
  }, []);

  useEffect(() => {
    if (!open) return;
    function onClick(e: MouseEvent) {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    window.addEventListener("mousedown", onClick);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onClick);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  function pick(email: string) {
    window.localStorage.setItem("x-user-email", email);
    setUser(email);
    setOpen(false);
    window.location.reload();
  }

  const isYellow = accentFor(user) === "bg-ms-yellow";

  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="flex items-center gap-2 rounded-full border border-line-2 bg-white py-1 pl-1 pr-3 text-sm font-semibold text-ink-2 shadow-ms-sm transition-colors duration-150 hover:bg-bg-2"
      >
        <span
          className={
            "flex h-7 w-7 items-center justify-center rounded-full text-[11px] font-bold " +
            accentFor(user) +
            " " +
            (isYellow ? "text-ink" : "text-white")
          }
          aria-hidden
        >
          {initialsFor(user)}
        </span>
        <span className="hidden sm:inline">{user}</span>
        <svg
          aria-hidden
          width="12"
          height="12"
          viewBox="0 0 12 12"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M2.5 4.5l3.5 3.5 3.5-3.5" />
        </svg>
      </button>

      {open && (
        <div
          role="listbox"
          className="absolute right-0 z-40 mt-2 w-64 overflow-hidden rounded-lg border border-line bg-white shadow-ms-lg"
        >
          <div className="border-b border-line bg-bg px-3.5 py-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-muted">
            Switch persona
          </div>
          <ul className="py-1">
            {PRESET_USERS.map((u) => {
              const selected = u === user;
              const accent = accentFor(u);
              const yellow = accent === "bg-ms-yellow";
              return (
                <li key={u}>
                  <button
                    type="button"
                    role="option"
                    aria-selected={selected}
                    onClick={() => pick(u)}
                    className={
                      "flex w-full items-center gap-3 px-3.5 py-2 text-left text-sm transition-colors duration-150 " +
                      (selected
                        ? "bg-ms-blue/[0.08] text-ms-blue"
                        : "text-ink-2 hover:bg-bg-2")
                    }
                  >
                    <span
                      className={
                        "flex h-7 w-7 items-center justify-center rounded-full text-[11px] font-bold " +
                        accent +
                        " " +
                        (yellow ? "text-ink" : "text-white")
                      }
                      aria-hidden
                    >
                      {initialsFor(u)}
                    </span>
                    <span className="flex-1">{u}</span>
                    {selected && (
                      <svg
                        aria-hidden
                        width="14"
                        height="14"
                        viewBox="0 0 20 20"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      >
                        <path d="M4 10l4 4 8-8" />
                      </svg>
                    )}
                  </button>
                </li>
              );
            })}
          </ul>
          <div className="border-t border-line bg-bg px-3.5 py-2 text-[11px] text-muted">
            POC auth — sent as <code className="font-mono">X-User-Email</code>.
          </div>
        </div>
      )}
    </div>
  );
}
