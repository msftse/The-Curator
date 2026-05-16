// Typed fetch wrapper.
//
// Two auth modes, switched at build time via `NEXT_PUBLIC_AUTH_MODE`:
//
// - `oidc`: acquires a token from MSAL via `acquireTokenSilent` against the
//   configured API scope and attaches it as `Authorization: Bearer <jwt>`.
//   Silent acquisition handles refresh automatically; if it fails (consent
//   revoked, refresh-token expired) we fall back to `acquireTokenRedirect`
//   so the user re-signs-in cleanly.
//
// - `stub`: keeps the legacy `X-User-Email` header from localStorage. This
//   is the local-dev path and is the only mode that should be live without
//   `LOCAL_DEV=1` on the backend.
//
// Anything that previously called `call(...)` keeps working unchanged.

import { InteractionRequiredAuthError } from "@azure/msal-browser";

import { curator } from "./curator";
import type {
  ClassificationPatch,
  SkillDetail,
  SkillListItem,
  UploadResponse,
  UsageEventBody,
} from "./types";
import {
  apiScope,
  isOidc,
  buildLoginRequest,
  buildSilentRequest,
  getMsal,
} from "@/lib/auth/msal";
import { apiBase } from "@/lib/env";

/**
 * Backend base URL. Evaluated at call time so a single image can ship across
 * envs — see `frontend/lib/env.ts` for the runtime injection mechanism.
 *
 * Exported as a function (not a constant) deliberately: importing modules
 * that call `BASE()` will pick up the runtime value rather than freezing
 * the build-time fallback.
 */
export function BASE(): string {
  return apiBase();
}

function getStubUser(): string {
  if (typeof window === "undefined") return "anon@org";
  return window.localStorage.getItem("x-user-email") ?? "anon@org";
}

async function acquireBearerToken(): Promise<string | null> {
  if (typeof window === "undefined") return null;
  const instance = getMsal();
  const account =
    instance.getActiveAccount() ?? instance.getAllAccounts()[0] ?? null;
  if (!account) {
    // No signed-in account yet. The redirect guard should have prevented this
    // path, but if a probe fires during the brief window between mount and
    // `handleRedirectPromise` resolving, kick off interactive login rather
    // than emit an unauthenticated request that the caller will misread as
    // "not admin".
    // eslint-disable-next-line no-console
    console.warn("acquireBearerToken: no MSAL account; triggering loginRedirect");
    await instance.loginRedirect(buildLoginRequest());
    return null;
  }
  try {
    const result = await instance.acquireTokenSilent(
      buildSilentRequest(account),
    );
    return result.accessToken;
  } catch (err) {
    // Silent token acquisition can fail when the refresh token has expired,
    // the user revoked consent, conditional-access policy demands a fresh
    // interactive sign-in, OR the cached tokens are stale after a manifest
    // change on the API app (e.g. requestedAccessTokenVersion flip). In all
    // these cases the cleanest recovery is interactive redirect.
    // eslint-disable-next-line no-console
    console.warn("acquireTokenSilent failed; falling back to redirect", err);
    if (err instanceof InteractionRequiredAuthError) {
      await instance.acquireTokenRedirect(buildLoginRequest());
      return null;
    }
    // Non-interaction errors (network blip, cache corruption) — still try
    // interactive login rather than silently emit an unauthed request.
    await instance.acquireTokenRedirect(buildLoginRequest());
    return null;
  }
}

async function attachAuthHeaders(headers: Headers): Promise<void> {
  if (isOidc()) {
    const token = await acquireBearerToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    // No fallback header — backend will return 401 if the token is missing,
    // which is the correct signal.
    return;
  }
  headers.set("X-User-Email", getStubUser());
}

export async function call<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  await attachAuthHeaders(headers);
  const res = await fetch(`${BASE()}${path}`, { ...init, headers });
  if (!res.ok) {
    let body: unknown;
    try {
      body = await res.json();
    } catch {
      body = await res.text();
    }
    throw new Error(`API ${res.status}: ${JSON.stringify(body)}`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** Same as `call` but returns the raw text body (used for text/markdown endpoints). */
export async function callText(
  path: string,
  init: RequestInit = {},
): Promise<string> {
  const headers = new Headers(init.headers);
  await attachAuthHeaders(headers);
  const res = await fetch(`${BASE()}${path}`, { ...init, headers });
  if (!res.ok) {
    let body: unknown;
    try {
      body = await res.json();
    } catch {
      body = await res.text();
    }
    throw new Error(`API ${res.status}: ${JSON.stringify(body)}`);
  }
  if (res.status === 204) return "";
  return await res.text();
}

// Re-exported for tests / debugging. Reads runtime env at access time.
export const __authConfig = {
  get mode(): "oidc" | "stub" {
    return isOidc() ? "oidc" : "stub";
  },
  get apiScope(): string {
    return apiScope();
  },
};

export const api = {
  uploads: {
    create(form: FormData): Promise<UploadResponse> {
      return call<UploadResponse>("/v1/uploads", {
        method: "POST",
        body: form,
      });
    },
  },
  meta: {
    /** Canonical category taxonomy. Public endpoint, safe to cache. */
    categories(): Promise<string[]> {
      return call<string[]>("/v1/categories");
    },
  },
  me: {
    submissions(): Promise<SkillListItem[]> {
      return call<SkillListItem[]>("/v1/me/submissions");
    },
  },
  admin: {
    queue(): Promise<SkillListItem[]> {
      return call<SkillListItem[]>("/v1/admin/queue");
    },
    approve(skillId: string): Promise<SkillListItem> {
      return call<SkillListItem>(`/v1/admin/skills/${skillId}/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
    },
    reject(skillId: string, reason: string): Promise<SkillListItem> {
      return call<SkillListItem>(`/v1/admin/skills/${skillId}/reject`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason }),
      });
    },
    patchClassification(
      skillId: string,
      patch: ClassificationPatch,
    ): Promise<SkillListItem> {
      return call<SkillListItem>(
        `/v1/admin/skills/${skillId}/classification`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(patch),
        },
      );
    },
    archive(skillId: string, reason: string): Promise<SkillListItem> {
      // Admin-issued manual archive. Soft delete: bytes go to archive/,
      // status flips to "archived", restorable via curator.restore().
      // Backend rejects pinned skills (SKILL_PINNED) and non-approved
      // statuses (INVALID_STATUS_TRANSITION).
      return call<SkillListItem>(`/v1/admin/skills/${skillId}/archive`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason }),
      });
    },
  },
  catalog: {
    list(): Promise<SkillListItem[]> {
      return call<SkillListItem[]>("/v1/skills");
    },
    get(skillId: string): Promise<SkillDetail> {
      return call<SkillDetail>(`/v1/skills/${skillId}`);
    },
    downloadUrl(skillId: string): string {
      return `${BASE()}/v1/skills/${skillId}/download`;
    },
    reportUsage(skillId: string, body: UsageEventBody): Promise<void> {
      return call<void>(`/v1/skills/${skillId}/usage`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    },
  },
  curator,
};
