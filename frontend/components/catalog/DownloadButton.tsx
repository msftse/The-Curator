"use client";

import { api } from "@/lib/api/client";

/**
 * Download button — anchor that lets the browser follow the 307 redirect
 * to a signed Azure Blob SAS URL.
 *
 * Side-effect: fires `POST /v1/skills/{id}/usage` with `loader_id =
 * "web-ui:<email>"` so the curator's 30/90-day staleness counters reflect
 * human exploration alongside agent loads. Fire-and-forget; errors are
 * logged and swallowed so a flaky usage call NEVER blocks the download.
 *
 * We deliberately do NOT call `event.preventDefault()` — the anchor must
 * navigate even while the usage POST is in flight.
 */
export function DownloadButton({ skillId }: { skillId: string }) {
  const onClick = () => {
    const email =
      typeof window !== "undefined"
        ? (window.localStorage.getItem("x-user-email") ?? "anon@org")
        : "anon@org";
    void api.catalog
      .reportUsage(skillId, { loader_id: `web-ui:${email}` })
      .catch((err) => {
        // eslint-disable-next-line no-console
        console.warn("usage event failed (ignored)", err);
      });
  };

  return (
    <a
      href={api.catalog.downloadUrl(skillId)}
      onClick={onClick}
      className="ms-btn-primary"
    >
      Download tar.gz
    </a>
  );
}
