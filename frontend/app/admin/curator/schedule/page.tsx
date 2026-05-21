"use client";

import { useEffect, useState } from "react";

import { ScheduleEditor } from "@/components/curator/ScheduleEditor";
import { api } from "@/lib/api/client";
import type { CuratorSchedule } from "@/lib/api/types";

export default function CuratorSchedulePage() {
  const [schedule, setSchedule] = useState<CuratorSchedule | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    api.curator
      .getSchedule()
      .then((s) => {
        if (active) setSchedule(s);
      })
      .catch((e) => {
        if (active) setLoadError((e as Error).message);
      });
    return () => {
      active = false;
    };
  }, []);

  return (
    <div className="space-y-4">
      <h2 className="text-sm font-semibold uppercase text-gray-600">
        Curator schedule
      </h2>
      <p className="text-sm text-gray-600">
        The curator runs a full pass on the schedule below. Changes are
        applied by a background reconciler that patches the Kubernetes
        CronJob — the next scheduled tick will reflect the new value.
      </p>
      {loadError ? (
        <div className="ms-msgbar-danger">
          Failed to load schedule: {loadError}
        </div>
      ) : schedule ? (
        <ScheduleEditor
          initial={schedule}
          onSaved={(s) => setSchedule(s)}
        />
      ) : (
        <div className="h-24 animate-pulse rounded bg-gray-100" />
      )}
    </div>
  );
}
