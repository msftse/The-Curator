/**
 * Component tests for `JanitorPanel`.
 *
 * Asserts:
 *   - calls api.curator.janitor() after confirm
 *   - renders both classifier + defender result rows
 *   - surfaces API errors in an inline banner
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { JanitorPanel } from "@/components/curator/JanitorPanel";

const janitorMock = vi.fn();
vi.mock("@/lib/api/client", () => ({
  api: {
    curator: {
      janitor: (...args: unknown[]) => janitorMock(...args),
    },
  },
}));

describe("JanitorPanel", () => {
  beforeEach(() => {
    janitorMock.mockReset();
  });

  it("calls the janitor API and renders both queue rows", async () => {
    janitorMock.mockResolvedValueOnce({
      classifier: { scanned: 7, requeued: 2 },
      defender: { scanned: 3, requeued: 1 },
    });

    render(<JanitorPanel />);

    fireEvent.click(screen.getByRole("button", { name: /run janitor/i }));
    // Confirm modal
    fireEvent.click(
      screen.getAllByRole("button", { name: /run janitor/i }).pop()!,
    );

    await waitFor(() => expect(janitorMock).toHaveBeenCalledTimes(1));
    expect(screen.getByText("classifier")).toBeInTheDocument();
    expect(screen.getByText("defender")).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("renders an error banner on failure", async () => {
    janitorMock.mockRejectedValueOnce(new Error("boom"));

    render(<JanitorPanel />);
    fireEvent.click(screen.getByRole("button", { name: /run janitor/i }));
    fireEvent.click(
      screen.getAllByRole("button", { name: /run janitor/i }).pop()!,
    );

    await waitFor(() =>
      expect(screen.getByText(/boom/i)).toBeInTheDocument(),
    );
  });
});
