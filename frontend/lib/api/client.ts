// Typed fetch wrapper that injects X-User-Email from localStorage (stub auth).
import type {
  ClassificationPatch,
  SkillListItem,
  UploadResponse,
} from "./types";

const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

function getStubUser(): string {
  if (typeof window === "undefined") return "anon@org";
  return window.localStorage.getItem("x-user-email") ?? "anon@org";
}

async function call<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("X-User-Email", getStubUser());
  const res = await fetch(`${BASE}${path}`, { ...init, headers });
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

export const api = {
  uploads: {
    create(form: FormData): Promise<UploadResponse> {
      return call<UploadResponse>("/v1/uploads", {
        method: "POST",
        body: form,
      });
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
  },
  catalog: {
    list(): Promise<SkillListItem[]> {
      return call<SkillListItem[]>("/v1/skills");
    },
    get(skillId: string): Promise<SkillListItem> {
      return call<SkillListItem>(`/v1/skills/${skillId}`);
    },
    downloadUrl(skillId: string): string {
      return `${BASE}/v1/skills/${skillId}/download`;
    },
  },
};
