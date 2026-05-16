"use client";

import Link from "next/link";

import { useAdminProbe } from "@/lib/hooks/useAdminProbe";

export function AdminNavLink({
  href,
  children,
}: {
  href: string;
  children: React.ReactNode;
}) {
  const { isAdmin } = useAdminProbe();
  if (!isAdmin) return null;
  return (
    <Link href={href} className="text-gray-600 hover:text-gray-900">
      {children}
    </Link>
  );
}
