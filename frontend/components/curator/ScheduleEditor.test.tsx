/**
 * Component tests for `ScheduleEditor` (M5-7).
 *
 * The editor has two modes (weekly + custom) and pushes a typed body to
 * `api.curator.putSchedule(...)`. We assert:
 *   - mode toggle swaps the visible controls
 *   - weekly pickers emit `M H * * D` cron strings
 *   - custom cron disables submit on invalid input
 *   - successful save invokes the API client and the `onSaved` callback
 *   - server errors surface in an inline banner
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ScheduleEditor } from "@/components/curator/ScheduleEditor";
import type { CuratorSchedule } from "@/lib/api/types";

// ---- mocks ----------------------------------------------------------

const putScheduleMock = vi.fn();
vi.mock("@/lib/api/client", () => ({
  api: {
    curator: {
      putSchedule: (...args: unknown[]) => putScheduleMock(...args),
    },
  },
}));

// ---- fixtures -------------------------------------------------------

function makeSchedule(overrides: Partial<CuratorSchedule> = {}): CuratorSchedule {
  return {
    cron: "0 3 * * 0",
    timezone: "UTC",
    enabled: true,
    updated_by: "alice@org",
    updated_at: "2026-05-21T11:00:00Z",
    ...overrides,
  };
}

// ---- tests ----------------------------------------------------------

describe("ScheduleEditor", () => {
  beforeEach(() => {
    putScheduleMock.mockReset();
  });

  it("renders weekly mode for a parseable schedule", () => {
    render(<ScheduleEditor initial={makeSchedule()} />);
    expect(screen.getByTestId("schedule-mode")).toHaveValue("weekly");
    expect(screen.getByTestId("schedule-day")).toHaveValue("0");
    expect(screen.getByTestId("schedule-hour")).toHaveValue(3);
    expect(screen.getByTestId("schedule-minute")).toHaveValue(0);
    expect(screen.getByTestId("schedule-effective")).toHaveTextContent(
      "0 3 * * 0",
    );
  });

  it("falls back to custom mode for non-weekly cron", () => {
    render(<ScheduleEditor initial={makeSchedule({ cron: "*/15 * * * *" })} />);
    expect(screen.getByTestId("schedule-mode")).toHaveValue("custom");
    expect(screen.getByTestId("schedule-cron")).toHaveValue("*/15 * * * *");
  });

  it("weekly pickers update the effective cron string", () => {
    render(<ScheduleEditor initial={makeSchedule()} />);
    fireEvent.change(screen.getByTestId("schedule-day"), {
      target: { value: "1" },
    });
    fireEvent.change(screen.getByTestId("schedule-hour"), {
      target: { value: "6" },
    });
    fireEvent.change(screen.getByTestId("schedule-minute"), {
      target: { value: "30" },
    });
    expect(screen.getByTestId("schedule-effective")).toHaveTextContent(
      "30 6 * * 1",
    );
  });

  it("disables save when custom cron is invalid", () => {
    render(<ScheduleEditor initial={makeSchedule({ cron: "*/15 * * * *" })} />);
    const save = screen.getByTestId("schedule-save") as HTMLButtonElement;
    expect(save).not.toBeDisabled();

    fireEvent.change(screen.getByTestId("schedule-cron"), {
      target: { value: "this is not a cron" },
    });
    expect(save).toBeDisabled();
    expect(screen.getByTestId("schedule-next-run").textContent).toMatch(
      /^Invalid:/,
    );
  });

  it("submits weekly schedule via api.curator.putSchedule", async () => {
    const onSaved = vi.fn();
    putScheduleMock.mockResolvedValueOnce(
      makeSchedule({ cron: "30 6 * * 1", updated_by: "admin@org" }),
    );
    render(<ScheduleEditor initial={makeSchedule()} onSaved={onSaved} />);
    fireEvent.change(screen.getByTestId("schedule-day"), {
      target: { value: "1" },
    });
    fireEvent.change(screen.getByTestId("schedule-hour"), {
      target: { value: "6" },
    });
    fireEvent.change(screen.getByTestId("schedule-minute"), {
      target: { value: "30" },
    });
    fireEvent.click(screen.getByTestId("schedule-save"));

    await waitFor(() => {
      expect(putScheduleMock).toHaveBeenCalledWith({
        cron: "30 6 * * 1",
        timezone: "UTC",
        enabled: true,
        mode: "weekly",
      });
    });
    await waitFor(() => {
      expect(onSaved).toHaveBeenCalled();
    });
  });

  it("submits custom cron + enabled=false", async () => {
    putScheduleMock.mockResolvedValueOnce(
      makeSchedule({ cron: "0 0 * * *", enabled: false }),
    );
    render(<ScheduleEditor initial={makeSchedule({ cron: "*/15 * * * *" })} />);
    fireEvent.change(screen.getByTestId("schedule-cron"), {
      target: { value: "0 0 * * *" },
    });
    fireEvent.click(screen.getByTestId("schedule-enabled"));
    fireEvent.click(screen.getByTestId("schedule-save"));

    await waitFor(() => {
      expect(putScheduleMock).toHaveBeenCalledWith({
        cron: "0 0 * * *",
        timezone: "UTC",
        enabled: false,
        mode: "custom",
      });
    });
  });

  it("surfaces server-side errors inline", async () => {
    putScheduleMock.mockRejectedValueOnce(new Error("API 422: bad"));
    render(<ScheduleEditor initial={makeSchedule()} />);
    fireEvent.click(screen.getByTestId("schedule-save"));
    await waitFor(() => {
      expect(screen.getByTestId("schedule-error")).toHaveTextContent(
        "API 422: bad",
      );
    });
  });
});
