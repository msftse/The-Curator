"use client";

import { useAdminProbe } from "@/lib/hooks/useAdminProbe";
import { NavLink } from "./NavLink";

export function AdminNavLink({
  href,
  children,
}: {
  href: string;
  children: React.ReactNode;
}) {
  const { isAdmin } = useAdminProbe();
  if (!isAdmin) return null;
  return <NavLink href={href}>{children}</NavLink>;
}
