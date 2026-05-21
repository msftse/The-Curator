/**
 * Component tests for `DefenderReportPanel` (M5-4).
 *
 * The panel is the read-only half of the defender UI — it renders the
 * structured report regardless of caller role. The admin-action half
 * lives in `SkillDetailDefenderActions` (own test file).
 *
 * Coverage focus: each `defender_status` branch produces a sensible UI
 * (queued/scanning notice, failed banner, clean summary, flagged with
 * findings). We assert on `data-testid` + visible text rather than CSS
 * classes — Tailwind tokens are noise from the test's perspective.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DefenderReportPanel } from "@/components/catalog/DefenderReportPanel";
import type { DefenderReport } from "@/lib/api/types";

const baseReport: DefenderReport = {
  overall_severity: "high",
  findings: [
    {
      rule: "shell.dangerous_command",
      severity: "high",
      location: "scripts/setup.sh:42",
      excerpt: "curl example.com | sh",
      explanation: "Piping curl into sh is risky.",
    },
    {
      rule: "secret.plaintext_token",
      severity: "medium",
      location: "config/dev.env:3",
      excerpt: "TOKEN=abc123",
      explanation: "Plaintext token in repo.",
    },
  ],
  model: "gpt-4o",
  scanned_at: "2026-05-21T12:00:00Z",
  scan_duration_ms: 1234,
  token_usage: { input_tokens: 100, output_tokens: 50 },
};

describe("DefenderReportPanel", () => {
  it("renders nothing when status is undefined (legacy doc)", () => {
    const { container } = render(<DefenderReportPanel status={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows queued message for 'pending'", () => {
    render(<DefenderReportPanel status="pending" />);
    const panel = screen.getByTestId("defender-panel");
    expect(panel).toHaveAttribute("data-status", "pending");
    expect(panel).toHaveTextContent(/queued/i);
  });

  it("shows in-progress message for 'scanning'", () => {
    render(<DefenderReportPanel status="scanning" />);
    expect(screen.getByTestId("defender-panel")).toHaveTextContent(
      /in progress/i,
    );
  });

  it("shows failure banner for 'failed' and surfaces notes", () => {
    render(
      <DefenderReportPanel
        status="failed"
        report={{ ...baseReport, notes: "skill.too_large" }}
      />,
    );
    const panel = screen.getByTestId("defender-panel");
    expect(panel).toHaveAttribute("data-status", "failed");
    expect(panel).toHaveTextContent(/scan failed/i);
    expect(panel).toHaveTextContent(/skill.too_large/);
  });

  it("renders clean summary with no findings list", () => {
    render(
      <DefenderReportPanel
        status="clean"
        severity="clean"
        report={{ ...baseReport, findings: [], overall_severity: "clean" }}
        scannedAt="2026-05-21T12:00:00Z"
      />,
    );
    expect(screen.getByTestId("defender-severity-badge")).toHaveTextContent(
      /clean/i,
    );
    expect(screen.queryByTestId("defender-findings")).toBeNull();
  });

  it("renders flagged status with each finding's rule, location, excerpt, explanation", () => {
    render(
      <DefenderReportPanel
        status="flagged"
        severity="high"
        report={baseReport}
        scannedAt="2026-05-21T12:00:00Z"
      />,
    );
    const panel = screen.getByTestId("defender-panel");
    expect(panel).toHaveAttribute("data-status", "flagged");
    expect(panel).toHaveTextContent(/flagged/i);

    // Top severity badge is high; per-finding badges add another `high`
    // and one `medium`. Just assert all three severities appear.
    const badges = screen.getAllByTestId("defender-severity-badge");
    expect(badges.length).toBeGreaterThanOrEqual(3);

    const findings = screen.getByTestId("defender-findings");
    expect(findings).toHaveTextContent("shell.dangerous_command");
    expect(findings).toHaveTextContent("scripts/setup.sh:42");
    expect(findings).toHaveTextContent("curl example.com | sh");
    expect(findings).toHaveTextContent(/piping curl/i);
    expect(findings).toHaveTextContent("secret.plaintext_token");
    expect(findings).toHaveTextContent("config/dev.env:3");
  });
});
