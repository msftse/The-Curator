/**
 * Component tests for `SkillDetailDefenderActions` (M5-4).
 *
 * Covers:
 *   - Hidden when caller is not admin.
 *   - Hidden when defender_status is not 'flagged' or 'failed'.
 *   - For `failed`: only the Override button is shown (no Quarantine).
 *   - For `flagged`: both buttons are shown.
 *   - Override modal: confirm button stays disabled until justification
 *     reaches MIN_JUSTIFICATION chars AND the skill_id confirm field
 *     matches; on submit, calls `api.admin.defenderOverride(...)`.
 *   - Quarantine modal: same disable-gating; on submit, calls
 *     `api.admin.quarantine(...)` and navigates to /catalog.
 *
 * We mock the API client + admin probe + next/navigation at the module
 * boundary — same shape any other component test in this repo would use.
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SkillDetailDefenderActions } from "@/components/catalog/SkillDetailDefenderActions";
import type { SkillDetail } from "@/lib/api/types";

// ---- mocks ------------------------------------------------------------

const refreshSpy = vi.fn();
const pushSpy = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: refreshSpy, push: pushSpy }),
}));

const probeMock = vi.fn();
vi.mock("@/lib/hooks/useAdminProbe", () => ({
  useAdminProbe: () => probeMock(),
}));

const overrideMock = vi.fn();
const quarantineMock = vi.fn();
vi.mock("@/lib/api/client", () => ({
  api: {
    admin: {
      defenderOverride: (...args: unknown[]) => overrideMock(...args),
      quarantine: (...args: unknown[]) => quarantineMock(...args),
    },
  },
}));

// ---- fixtures ---------------------------------------------------------

function makeSkill(overrides: Partial<SkillDetail> = {}): SkillDetail {
  return {
    skill_id: "flagged-skill",
    version: "1.0.0",
    name: "Flagged Skill",
    description: "",
    status: "classified",
    classifier_status: "done",
    uploader: "alice@org",
    uploaded_at: "2026-05-21T10:00:00Z",
    approved_at: null,
    classification: null,
    bundle: null,
    pinned: false,
    user_category: null,
    user_tags: [],
    skill_md_text: "# bad\n",
    defender_status: "flagged",
    defender_severity: "high",
    defender_report: null,
    defender_scanned_at: "2026-05-21T11:00:00Z",
    ...overrides,
  };
}

const LONG_JUSTIFICATION = "x".repeat(25); // > MIN_JUSTIFICATION (20)

// ---- tests ------------------------------------------------------------

describe("SkillDetailDefenderActions", () => {
  beforeEach(() => {
    refreshSpy.mockReset();
    pushSpy.mockReset();
    overrideMock.mockReset();
    quarantineMock.mockReset();
    probeMock.mockReturnValue({ isAdmin: true, isLoading: false });
  });

  it("renders nothing when probe is loading", () => {
    probeMock.mockReturnValue({ isAdmin: false, isLoading: true });
    const { container } = render(
      <SkillDetailDefenderActions skill={makeSkill()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when caller is not admin", () => {
    probeMock.mockReturnValue({ isAdmin: false, isLoading: false });
    const { container } = render(
      <SkillDetailDefenderActions skill={makeSkill()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when defender_status is 'clean'", () => {
    const { container } = render(
      <SkillDetailDefenderActions
        skill={makeSkill({ defender_status: "clean" })}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows Override but NOT Quarantine for 'failed' status", () => {
    render(
      <SkillDetailDefenderActions
        skill={makeSkill({ defender_status: "failed" })}
      />,
    );
    expect(screen.getByTestId("defender-override-button")).toBeInTheDocument();
    expect(screen.queryByTestId("defender-quarantine-button")).toBeNull();
  });

  it("shows both buttons for 'flagged' status", () => {
    render(<SkillDetailDefenderActions skill={makeSkill()} />);
    expect(screen.getByTestId("defender-override-button")).toBeInTheDocument();
    expect(
      screen.getByTestId("defender-quarantine-button"),
    ).toBeInTheDocument();
  });

  it("override modal: confirm disabled until justification length AND name match", () => {
    render(<SkillDetailDefenderActions skill={makeSkill()} />);
    fireEvent.click(screen.getByTestId("defender-override-button"));
    expect(screen.getByTestId("defender-override-modal")).toBeInTheDocument();

    const confirm = screen.getByTestId(
      "defender-confirm-button",
    ) as HTMLButtonElement;
    expect(confirm).toBeDisabled();

    // Just the name — still disabled (no justification).
    fireEvent.change(screen.getByTestId("defender-confirm-name"), {
      target: { value: "flagged-skill" },
    });
    expect(confirm).toBeDisabled();

    // Short justification — still disabled.
    fireEvent.change(screen.getByTestId("defender-justification-input"), {
      target: { value: "too short" },
    });
    expect(confirm).toBeDisabled();

    // Long justification + matching name — enabled.
    fireEvent.change(screen.getByTestId("defender-justification-input"), {
      target: { value: LONG_JUSTIFICATION },
    });
    expect(confirm).not.toBeDisabled();
  });

  it("override submit calls api.admin.defenderOverride and refreshes route", async () => {
    overrideMock.mockResolvedValueOnce({});
    render(<SkillDetailDefenderActions skill={makeSkill()} />);
    fireEvent.click(screen.getByTestId("defender-override-button"));
    fireEvent.change(screen.getByTestId("defender-justification-input"), {
      target: { value: LONG_JUSTIFICATION },
    });
    fireEvent.change(screen.getByTestId("defender-confirm-name"), {
      target: { value: "flagged-skill" },
    });
    fireEvent.click(screen.getByTestId("defender-confirm-button"));

    await waitFor(() => {
      expect(overrideMock).toHaveBeenCalledWith(
        "flagged-skill",
        LONG_JUSTIFICATION,
      );
    });
    expect(refreshSpy).toHaveBeenCalled();
    expect(pushSpy).not.toHaveBeenCalled();
  });

  it("quarantine submit calls api.admin.quarantine and pushes to /catalog", async () => {
    quarantineMock.mockResolvedValueOnce({});
    render(<SkillDetailDefenderActions skill={makeSkill()} />);
    fireEvent.click(screen.getByTestId("defender-quarantine-button"));
    expect(screen.getByTestId("defender-quarantine-modal")).toBeInTheDocument();

    fireEvent.change(screen.getByTestId("defender-justification-input"), {
      target: { value: LONG_JUSTIFICATION },
    });
    fireEvent.change(screen.getByTestId("defender-confirm-name"), {
      target: { value: "flagged-skill" },
    });
    fireEvent.click(screen.getByTestId("defender-confirm-button"));

    await waitFor(() => {
      expect(quarantineMock).toHaveBeenCalledWith(
        "flagged-skill",
        LONG_JUSTIFICATION,
      );
    });
    expect(pushSpy).toHaveBeenCalledWith("/catalog");
  });
});
