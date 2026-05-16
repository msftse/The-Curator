#!/usr/bin/env bash
# Sequentially: plan + execute M2.1, M3, frontend curator UI. Commit + push after each.
# Logs to .opencode/logs/queue-<ts>.log
set -uo pipefail
cd ~/projects/agentic-skill-hub

TS=$(date +%Y%m%d-%H%M%S)
QUEUE_LOG=".opencode/logs/queue-${TS}.log"
exec > >(tee -a "$QUEUE_LOG") 2>&1

echo "=== QUEUE START $(date) ==="

run_opencode () {
  local label="$1"; local prompt="$2"
  local log=".opencode/logs/${label}-${TS}.log"
  echo "$log" > .opencode/logs/latest
  echo ">>> [$label] starting -> $log"
  script -q /dev/null opencode run --print-logs --log-level INFO "$prompt" > "$log" 2>&1
  local ec=$?
  echo "<<< [$label] exit=$ec"
  return $ec
}

commit_push () {
  local msg="$1"
  git add -A
  if git diff --cached --quiet; then
    echo "    [skip commit: nothing staged for $msg]"
    return 0
  fi
  git commit -m "$msg" | tail -3
  git push origin main 2>&1 | tail -3
}

# =====================================================================
# M2.1 — finish the 3 missing integration tests
# =====================================================================
run_opencode "plan-m2.1" 'Run /plan-feature per .opencode/commands/plan-feature.md for M2.1 — complete the missing curator integration tests. Read AGENTS.md, docs/PRD.md, .agents/plans/m2-curator.md (esp. Phase 4 testing section) and the existing backend/tests/integration/ stubs (test_curator_run.py, test_curator_endpoints.py, test_usage_pipeline.py). Plan three new integration tests with skip-if-no-emulator markers: (1) test_curator_rollback_round_trip.py — seed N skills, run curator pass, rollback by snapshot id, assert Cosmos docs + Blob archive byte-for-byte match the pre-pass state, then assert a pre-rollback snapshot was created (rollback is itself reversible). (2) test_janitor_sweep.py — push N classifier messages into the queue, advance clock past visibility_timeout * stale_multiplier without ack, run janitor, assert all N requeued exactly once and audit events written. (3) test_curator_pin_unpin.py — pin a skill that would otherwise transition to stale, run a full curator pass twice with simulated time advance past stale_after_days and archive_after_days, assert pinned skill never transitions; then unpin and re-run, assert it transitions normally. Write plan to .agents/plans/m2.1-curator-integration-tests.md. Do not execute.'
commit_push "M2.1 plan: missing curator integration tests"

run_opencode "exec-m2.1" 'Execute /execute per .opencode/commands/execute.md against .agents/plans/m2.1-curator-integration-tests.md. Write all three integration tests. Run ruff and pytest backend/tests/unit as a gate. Integration tests will skip without emulators — that is fine, do not start docker. Do not touch Azure. Skip .env.local.example. When done print summary.'
commit_push "M2.1: complete curator integration tests (rollback round-trip, janitor sweep, pin/unpin)"

# =====================================================================
# M3 — Curator LLM review pass
# =====================================================================
run_opencode "plan-m3" 'Run /plan-feature per .opencode/commands/plan-feature.md for M3 — Curator LLM review pass. Read AGENTS.md, docs/PRD.md (Phase M3 around line 542), the existing curator implementation (backend/services/curator.py, backend/services/snapshot.py, backend/api/curator.py), and .agents/plans/m2-curator.md. Design and plan: (1) an aux-model review pass that runs after the deterministic curator pass; selects active agent-created skills (cap configurable, default 50), reads their SKILL.md bundles from Blob, and produces a structured proposal set: consolidation candidates (merge skill A + B into umbrella C), drift patches (suggested patch to a single skill), and keep-as-is verdicts. (2) Proposals are NEVER auto-applied — they land as records in a new Cosmos container review_proposals (PK /run_id) with status pending → approved → applied | rejected. (3) Manager UI surface: extend admin curator router with /reviews list/get/approve/reject endpoints. Approve triggers the actual skill_manage equivalent (patch or merge) wrapped in the same snapshot+audit+cache-bust machinery. (4) Aux model config: new auxiliary.curator_review setting (provider, model, timeout). Default and primary provider is Azure AI Foundry (Microsoft Foundry) — use the Azure AI Inference SDK (azure-ai-inference) against a Foundry-deployed model. Config keys: foundry.endpoint, foundry.deployment, foundry.api_version, auth via Managed Identity in Azure (DefaultAzureCredential) and AZURE_AI_FOUNDRY_API_KEY for local dev only. Wrap behind a thin LLMProvider interface so a fake provider can be injected in tests; do not call other providers (no OpenAI, no Anthropic) — Foundry only. (5) Per-run report extended with proposals section. (6) Cost guard: hard token cap per review, hard per-run skill cap, abort if exceeded. (7) Unit tests: proposal serialization, cost guard, never-auto-apply invariant (AST gate extension). Integration tests skip-if-no-emulator: end-to-end fake-LLM review pass produces N proposals, approve flow updates Cosmos + writes audit, reject flow leaves skill untouched. Hard constraints: still never silently destroy; manager approval gate is mandatory; same Redis lock prevents concurrent review passes. Write plan to .agents/plans/m3-curator-llm-review.md. Do not execute.'
commit_push "M3 plan: curator LLM review pass (manager-gated proposals)"

run_opencode "exec-m3" 'Execute /execute per .opencode/commands/execute.md against .agents/plans/m3-curator-llm-review.md. Write all files. Use a fake/in-process LLM provider for tests (do not call real model APIs). Run ruff + pytest backend/tests/unit as a gate. Integration tests skip without emulators. Do not push, do not touch Azure, skip .env.local.example. When done print summary including new endpoints, new Cosmos container, new config keys, and the manual deploy steps delta vs M2.'
commit_push "M3: curator LLM review pass implementation"

# =====================================================================
# Frontend curator admin UI
# =====================================================================
run_opencode "plan-frontend-curator" 'Run /plan-feature per .opencode/commands/plan-feature.md for "Frontend admin UI for curator". Read AGENTS.md, docs/PRD.md (sections on web UI and admin), the existing Next.js frontend under frontend/ (look at the existing admin pages, layout, API client at frontend/lib/api/client.ts, auth wiring), and the backend admin endpoints in backend/api/curator.py and (after M3) backend/api/curator.py review endpoints. Plan a new section /admin/curator with: (1) Status dashboard — current state (running/paused/idle), last run, last snapshot, counts of active/stale/archived/pinned, recent run history (clickable to REPORT.md viewer). (2) Run controls — pause/resume buttons with confirmation, run-now and dry-run buttons (dry-run streams report inline without mutating). (3) Snapshot browser — list snapshots with reason + size + timestamp, rollback to any snapshot with two-step confirmation. (4) Per-skill actions — pin/unpin toggle on every skill row, restore-from-archive button on archived skills. (5) Review proposals queue (M3) — list pending proposals grouped by run, diff viewer for patch proposals, side-by-side merge view for consolidations, approve/reject with optional comment. (6) Auth gate — entire /admin/curator section requires admin role from the current Principal. Use the same SWR patterns and component library already in frontend/. Plan should call out file paths under frontend/app/admin/curator/, new components under frontend/components/curator/, API client extensions in frontend/lib/api/curator.ts, and frontend tests under frontend/__tests__/curator/. Write plan to .agents/plans/m2.2-frontend-curator-ui.md. Do not execute.'
commit_push "frontend-curator plan: admin UI for curator (status/snapshots/reviews)"

run_opencode "exec-frontend-curator" 'Execute /execute per .opencode/commands/execute.md against .agents/plans/m2.2-frontend-curator-ui.md. Write all frontend files. Run frontend lint + typecheck + build (cd frontend && npm run lint && npm run typecheck && npm run build) as the gate. If npm scripts fail, fix and retry up to 2 times. Do not push, do not touch Azure. When done print summary including new pages, new components, and a screenshot-readiness checklist (which routes a reviewer should click through).'
commit_push "frontend-curator: admin UI implementation"

echo "=== QUEUE COMPLETE $(date) ==="
echo "Latest commits:"
git log --oneline -10
