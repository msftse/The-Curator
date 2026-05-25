"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS: { href: string; label: string }[] = [
  { href: "/admin/curator", label: "Overview" },
  { href: "/admin/curator/schedule", label: "Schedule" },
  { href: "/admin/curator/snapshots", label: "Snapshots" },
  { href: "/admin/curator/skills", label: "Skills" },
  { href: "/admin/curator/reviews", label: "Reviews" },
];

export function CuratorNav() {
  const pathname = usePathname();

  return (
    <nav className="flex gap-4 border-b border-gray-200 pb-2 text-sm">
      {TABS.map((t) => {
        const active =
          t.href === "/admin/curator"
            ? pathname === t.href
            : pathname?.startsWith(t.href);
        return (
          <Link
            key={t.href}
            href={t.href}
            className={
              active
                ? "font-semibold text-gray-900"
                : "text-gray-600 hover:text-gray-900"
            }
          >
            {t.label}
          </Link>
        );
      })}
    </nav>
  );
}
