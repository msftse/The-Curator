"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

export function NavLink({
  href,
  children,
}: {
  href: string;
  children: React.ReactNode;
}) {
  const pathname = usePathname() ?? "";
  const active = href === "/" ? pathname === "/" : pathname.startsWith(href);

  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      className={
        "rounded-md px-3 py-2 text-sm font-medium transition-colors duration-150 hover:no-underline " +
        (active
          ? "bg-ms-blue/[0.08] text-ms-blue"
          : "text-ink-2 hover:bg-bg-2")
      }
    >
      {children}
    </Link>
  );
}
