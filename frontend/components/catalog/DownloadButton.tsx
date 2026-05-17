"use client";

import { useState } from "react";

import { DownloadDialog } from "./DownloadDialog";
import { api } from "@/lib/api/client";

/**
 * Catalog "Download" entry point — opens a dialog that gives the user a
 * pasteable agent prompt plus a fallback signed-URL download.
 *
 * Why a dialog (and not a plain anchor):
 *   1. The `/v1/skills/{id}/download_url` endpoint is auth-gated; a plain
 *      `<a href>` to it can't attach the bearer token, so we have to fetch
 *      the SAS URL via the typed API client first.
 *   2. The primary affordance is "Copy prompt" — a one-shot instruction
 *      the user pastes into Hermes / Openclaw /Claude Code / Cursor / Copilot
 *      Chat. Surfacing that next to a Download fallback is the whole job
 *      of this dialog.
 *
 * The actual SAS fetch + clipboard interaction live in `DownloadDialog`.
 */
export function DownloadButton({
  skillId,
  skillName,
  uploader,
  version,
  category,
  description,
  tags,
}: {
  skillId: string;
  skillName: string;
  uploader: string;
  version: string;
  category: string | null;
  description: string;
  tags: string[];
}) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="ms-btn-primary"
      >
        Use this skill
      </button>
      <DownloadDialog
        open={open}
        skillId={skillId}
        skillName={skillName}
        uploader={uploader}
        version={version}
        category={category}
        description={description}
        tags={tags}
        onClose={() => setOpen(false)}
      />
    </>
  );
}

// Keep the prior URL-builder export around so any non-React caller (tests,
// imperative code) can still ask for the *raw* legacy 307 endpoint.
export const legacyDownloadHref = (skillId: string): string =>
  api.catalog.downloadUrl(skillId);
