"use client";

import { useEffect, useState } from "react";

const PRESET_USERS = ["alice@org", "bob@org", "manager@org", "admin@org"];

export function UserPicker() {
  const [user, setUser] = useState<string>("alice@org");

  useEffect(() => {
    const stored = window.localStorage.getItem("x-user-email");
    if (stored) setUser(stored);
  }, []);

  function pick(email: string) {
    window.localStorage.setItem("x-user-email", email);
    setUser(email);
    // Force pages to re-fetch with the new identity.
    window.location.reload();
  }

  return (
    <div className="flex items-center gap-2 text-sm">
      <span className="text-gray-500">acting as:</span>
      <select
        value={user}
        onChange={(e) => pick(e.target.value)}
        className="rounded border border-gray-300 bg-white px-2 py-1"
      >
        {PRESET_USERS.map((u) => (
          <option key={u} value={u}>
            {u}
          </option>
        ))}
      </select>
    </div>
  );
}
