"use client";

import { CuratorNav } from "@/components/curator/CuratorNav";
import { RequireAdmin } from "@/components/curator/RequireAdmin";

export default function CuratorLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="mx-auto max-w-[1280px] space-y-4 px-6 py-12">
      <h1 className="font-display text-[28px] font-bold tracking-ms-display text-ink">Curator</h1>
      <RequireAdmin>
        <CuratorNav />
        <div>{children}</div>
      </RequireAdmin>
    </div>
  );
}
