"use client";

/**
 * Curator schedule editor (M5-7).
 *
 * Two modes:
 *   - "weekly"  — three pickers (day-of-week, hour, minute) emit
 *                 `M H * * D` cron strings; the timezone field is sent
 *                 alongside (the backend reconciler does NOT convert —
 *                 K8s CronJobs run in cluster TZ; timezone is metadata
 *                 for the UI to render a friendly preview).
 *   - "custom"  — raw 5-field cron in a textbox with a live next-run
 *                 estimate computed client-side.
 *
 * Validation:
 *   - Cron is checked client-side with the same RE as the backend
 *     (`validateCron`). Submit is disabled while invalid.
 *   - Server is the ultimate authority — server-side validation errors
 *     are surfaced as `error` state.
 *
 * The component is server-state-agnostic: it accepts an `initial`
 * schedule via prop. The page wrapper fetches `getSchedule()` then
 * mounts this component once the response is in.
 */

import { useMemo, useState } from "react";

import { api } from "@/lib/api/client";
import { computeNextRun, validateCron } from "@/lib/cron";
import type {
  CuratorSchedule,
  CuratorScheduleUpdate,
} from "@/lib/api/types";

interface Props {
  initial: CuratorSchedule;
  onSaved?: (schedule: CuratorSchedule) => void;
}

const WEEKDAYS = [
  "Sunday",
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
];

function parseWeekly(
  cron: string,
): { day: number; hour: number; minute: number } | null {
  // Recognises `M H * * D` where M, H, D are plain integers. Anything
  // fancier (steps, ranges, lists) is "custom" and we won't try to
  // round-trip it.
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return null;
  const [mStr, hStr, dom, mon, dStr] = parts;
  if (dom !== "*" || mon !== "*") return null;
  const minute = Number(mStr);
  const hour = Number(hStr);
  const day = Number(dStr);
  if (
    !Number.isInteger(minute) ||
    !Number.isInteger(hour) ||
    !Number.isInteger(day)
  )
    return null;
  if (minute < 0 || minute > 59) return null;
  if (hour < 0 || hour > 23) return null;
  if (day < 0 || day > 6) return null;
  return { minute, hour, day };
}

export function ScheduleEditor({ initial, onSaved }: Props) {
  const weekly = parseWeekly(initial.cron);
  const [mode, setMode] = useState<"weekly" | "custom">(
    weekly ? "weekly" : "custom",
  );
  const [day, setDay] = useState(weekly?.day ?? 0);
  const [hour, setHour] = useState(weekly?.hour ?? 3);
  const [minute, setMinute] = useState(weekly?.minute ?? 0);
  const [customCron, setCustomCron] = useState(initial.cron);
  const [timezone, setTimezone] = useState(initial.timezone || "UTC");
  const [enabled, setEnabled] = useState(initial.enabled);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const effectiveCron = useMemo(
    () =>
      mode === "weekly"
        ? `${minute} ${hour} * * ${day}`
        : customCron.trim(),
    [mode, minute, hour, day, customCron],
  );

  const validationError = useMemo(() => {
    try {
      validateCron(effectiveCron);
      return null;
    } catch (e) {
      return (e as Error).message;
    }
  }, [effectiveCron]);

  const nextRun = useMemo(() => {
    if (validationError) return null;
    try {
      return computeNextRun(effectiveCron, new Date());
    } catch {
      return null;
    }
  }, [effectiveCron, validationError]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (validationError) return;
    setError(null);
    setSaving(true);
    const body: CuratorScheduleUpdate = {
      cron: effectiveCron,
      timezone: timezone.trim() || "UTC",
      enabled,
      mode,
    };
    try {
      const updated = await api.curator.putSchedule(body);
      onSaved?.(updated);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <form
      onSubmit={onSubmit}
      className="space-y-4 rounded border border-gray-200 bg-white p-4"
      data-testid="schedule-editor"
    >
      <div className="flex items-center gap-3">
        <label className="text-sm font-medium text-gray-700">Mode:</label>
        <select
          data-testid="schedule-mode"
          className="rounded border border-gray-300 px-2 py-1 text-sm"
          value={mode}
          onChange={(e) => setMode(e.target.value as "weekly" | "custom")}
        >
          <option value="weekly">Weekly</option>
          <option value="custom">Custom cron</option>
        </select>
      </div>

      {mode === "weekly" ? (
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col">
            <label className="text-xs text-gray-600" htmlFor="sched-day">
              Day
            </label>
            <select
              id="sched-day"
              data-testid="schedule-day"
              className="rounded border border-gray-300 px-2 py-1 text-sm"
              value={day}
              onChange={(e) => setDay(Number(e.target.value))}
            >
              {WEEKDAYS.map((label, i) => (
                <option key={i} value={i}>
                  {label}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col">
            <label className="text-xs text-gray-600" htmlFor="sched-hour">
              Hour
            </label>
            <input
              id="sched-hour"
              data-testid="schedule-hour"
              type="number"
              min={0}
              max={23}
              className="w-20 rounded border border-gray-300 px-2 py-1 text-sm"
              value={hour}
              onChange={(e) => setHour(Number(e.target.value))}
            />
          </div>
          <div className="flex flex-col">
            <label className="text-xs text-gray-600" htmlFor="sched-minute">
              Minute
            </label>
            <input
              id="sched-minute"
              data-testid="schedule-minute"
              type="number"
              min={0}
              max={59}
              className="w-20 rounded border border-gray-300 px-2 py-1 text-sm"
              value={minute}
              onChange={(e) => setMinute(Number(e.target.value))}
            />
          </div>
          <div className="flex flex-col">
            <label className="text-xs text-gray-600" htmlFor="sched-tz">
              Timezone
            </label>
            <input
              id="sched-tz"
              data-testid="schedule-timezone"
              className="w-40 rounded border border-gray-300 px-2 py-1 text-sm"
              value={timezone}
              onChange={(e) => setTimezone(e.target.value)}
            />
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          <label className="text-xs text-gray-600" htmlFor="sched-cron">
            Cron (minute hour dom mon dow)
          </label>
          <input
            id="sched-cron"
            data-testid="schedule-cron"
            className="w-72 rounded border border-gray-300 px-2 py-1 font-mono text-sm"
            value={customCron}
            onChange={(e) => setCustomCron(e.target.value)}
          />
          <input
            data-testid="schedule-timezone-custom"
            className="w-40 rounded border border-gray-300 px-2 py-1 text-sm"
            value={timezone}
            onChange={(e) => setTimezone(e.target.value)}
            aria-label="Timezone"
          />
        </div>
      )}

      <div className="flex items-center gap-2">
        <input
          id="sched-enabled"
          data-testid="schedule-enabled"
          type="checkbox"
          checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
        />
        <label htmlFor="sched-enabled" className="text-sm text-gray-700">
          Schedule enabled (uncheck to suspend the CronJob)
        </label>
      </div>

      <div className="rounded border border-gray-100 bg-gray-50 p-2 text-xs text-gray-700">
        <div>
          Effective cron:{" "}
          <span
            data-testid="schedule-effective"
            className="font-mono text-gray-900"
          >
            {effectiveCron}
          </span>
        </div>
        <div data-testid="schedule-next-run">
          {validationError
            ? `Invalid: ${validationError}`
            : nextRun
              ? `Next run (local): ${nextRun.toISOString()}`
              : "Next run: (cannot compute)"}
        </div>
      </div>

      {error ? (
        <div
          data-testid="schedule-error"
          className="rounded border border-red-200 bg-red-50 p-2 text-xs text-red-700"
        >
          {error}
        </div>
      ) : null}

      <button
        type="submit"
        data-testid="schedule-save"
        disabled={saving || Boolean(validationError)}
        className="rounded bg-ms-blue px-3 py-1.5 text-sm font-medium text-white disabled:bg-gray-300"
      >
        {saving ? "Saving…" : "Save schedule"}
      </button>
    </form>
  );
}
