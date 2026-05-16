"use client";

import { CuratorNav } from "@/components/curator/CuratorNav";
import { RequireAdmin } from "@/components/curator/RequireAdmin";

export default function CuratorLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Curator</h1>
      <RequireAdmin>
        <CuratorNav />
        <div>{children}</div>
      </RequireAdmin>
    </div>
  );
}
