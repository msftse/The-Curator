import "./globals.css";
import type { Metadata } from "next";
import Link from "next/link";

import { UserPicker } from "@/components/UserPicker";

export const metadata: Metadata = {
  title: "Agentic Skill Hub",
  description: "Internal hub for sanctioned agent skills",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <header className="border-b border-gray-200 bg-white">
          <div className="mx-auto flex max-w-5xl items-center justify-between px-4 py-3">
            <nav className="flex items-center gap-4 text-sm font-medium">
              <Link href="/" className="text-gray-900">
                Skill Hub
              </Link>
              <Link href="/upload" className="text-gray-600 hover:text-gray-900">
                Upload
              </Link>
              <Link href="/my-submissions" className="text-gray-600 hover:text-gray-900">
                My submissions
              </Link>
              <Link href="/admin/queue" className="text-gray-600 hover:text-gray-900">
                Review queue
              </Link>
            </nav>
            <UserPicker />
          </div>
        </header>
        <main className="mx-auto max-w-5xl px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
