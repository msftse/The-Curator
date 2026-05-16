"use client";

import { api } from "@/lib/api/client";

import { useResource } from "./useResource";

async function probeAdmin(): Promise<{ isAdmin: boolean }> {
  try {
    await api.curator.status();
    return { isAdmin: true };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    if (msg.includes("API 401") || msg.includes("API 403")) {
      return { isAdmin: false };
    }
    throw err;
  }
}

export function useAdminProbe(): {
  isAdmin: boolean;
  isLoading: boolean;
  error: unknown;
} {
  const { data, error, isLoading } = useResource(
    ["curator", "probe"],
    probeAdmin,
  );
  return {
    isAdmin: data?.isAdmin ?? false,
    isLoading,
    error,
  };
}
