# M5 Follow-Up: Gaps & Red Flags Fix Plan

> **For Hermes:** Use subagent-driven-development to execute task-by-task on branch `feature/m5-followup-gaps`. Run tests after each task. Do not push without explicit approval.

**Goal:** Close the six remaining gaps left after M5-8 + janitor follow-ups so the M5 surface area is internally consistent, deployable, and verifiably end-to-end.

**Architecture:** Pure cleanup pass. No new product features. Each task is isolated to one concern (chart, infra-compile, helm template, docs, smoke validation, rename).

**Tech Stack:** Helm, Bicep, Python (FastAPI / pytest), Docker Compose, Azurite + Cosmos emulator + Redis.

**Branch:** `feature/m5-followup-gaps` cut from `feature/m5-defender-notifier`.

---

## Gap Inventory (from review)

| # | Gap | Severity | Fix Task |
|---|---|---|---|
| 1 | Schedule reconciler worker has no Helm deployment — runs nowhere in cluster | High | Task 1 |
| 2 | Janitor sweep is admin-trigger only, no periodic CronJob | Medium | Task 2 |
| 3 | `infra/main.json` (compiled ARM) is stale vs new `communication.bicep` + UAMIs + RBAC | High | Task 3 |
| 4 | `AGENTS.md` has duplicated M5 wording from incremental milestone commits | Low | Task 4 |
| 5 | E2E `test_m5_full_flow.py` + `smoke_m5.sh` have never executed against a live stack | High | Task 5 |
| 6 | Helm chart directory is still `charts/agentic-skill-hub` after repo rename | Medium | Task 6 |

---

## Task 1: Ship the curator schedule reconciler as a Helm Deployment

**Objective:** Make the existing `backend/workers/curator_schedule_reconciler.py` actually run somewhere — single replica Deployment in the chart, alongside the notifier/defender deployments.

**Files:**
- Create: `charts/agentic-skill-hub/templates/curator/reconciler-deployment.yaml`
- Create: `charts/agentic-skill-hub/templates/curator/reconciler-networkpolicy.yaml`
- Modify: `charts/agentic-skill-hub/values.yaml` (add `curator.reconciler` block)
- Modify: `charts/agentic-skill-hub/values-dev.yaml`, `values-staging.yaml`, `values-prod.yaml` (override image tag / poll interval)
- Modify: `charts/agentic-skill-hub/templates/serviceaccounts.yaml` (add `curator-reconciler` SA bound to existing curator UAMI — schedule lives in Cosmos so it reuses Cosmos data-plane role)

**Step 1:** Read existing deployment as the pattern donor.

```bash
cat charts/agentic-skill-hub/templates/defender/deployment.yaml
cat charts/agentic-skill-hub/templates/curator/cronjob.yaml
```

**Step 2:** Create `reconciler-deployment.yaml`. Single replica, no HPA, no KEDA. Mounts the same SecretProviderClass as the curator cronjob. Command: `python -m backend.workers.curator_schedule_reconciler`. Env: `RECONCILER_POLL_INTERVAL_SECONDS={{ .Values.curator.reconciler.pollIntervalSeconds }}`. Liveness probe: process-up only (no HTTP).

**Step 3:** Create `reconciler-networkpolicy.yaml`. Egress only to: Cosmos (443), Kubernetes API (for CronJob patching — IMPORTANT, this is new vs other workers), DNS. No ingress.

**Step 4:** RBAC. Since the reconciler patches a `CronJob` in its own namespace, add a Role + RoleBinding in the same template file:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: {{ include "agentic-skill-hub.fullname" . }}-reconciler
rules:
  - apiGroups: ["batch"]
    resources: ["cronjobs"]
    verbs: ["get", "patch", "update"]
```

Bind to the `curator-reconciler` ServiceAccount.

**Step 5:** Add to `values.yaml`:

```yaml
curator:
  reconciler:
    enabled: true
    image:
      repository: backend
      tag: ""  # falls back to global tag
    pollIntervalSeconds: 60
    resources:
      requests: { cpu: 50m, memory: 128Mi }
      limits:   { cpu: 200m, memory: 256Mi }
```

**Step 6:** Lint and template-render.

```bash
helm lint charts/agentic-skill-hub
helm template test charts/agentic-skill-hub -f charts/agentic-skill-hub/values-dev.yaml \
  --show-only templates/curator/reconciler-deployment.yaml
helm template test charts/agentic-skill-hub -f charts/agentic-skill-hub/values-dev.yaml \
  --show-only templates/curator/reconciler-networkpolicy.yaml
```

Expected: no errors, manifests render with correct names/UAMI annotations.

**Step 7:** Commit.

```bash
git add charts/agentic-skill-hub/
git commit -m "feat(m5-gap1): curator schedule reconciler deployment + RBAC + netpol"
```

---

## Task 2: Add a periodic janitor CronJob

**Objective:** The two janitor sweeps (classifier-queue + defender-queue) are wired into one admin endpoint. Add a `CronJob` that calls them on a schedule so they don't depend on a human clicking the button.

**Files:**
- Create: `charts/agentic-skill-hub/templates/curator/janitor-cronjob.yaml`
- Create: `backend/workers/janitor_runner.py` — thin entrypoint that imports `backend.services.janitor` and runs both sweeps once, then exits 0
- Create: `backend/tests/unit/test_janitor_runner.py`
- Modify: `charts/agentic-skill-hub/values.yaml` (`curator.janitor.schedule`, default `"*/15 * * * *"`)

**Step 1:** Write failing test.

```python
# backend/tests/unit/test_janitor_runner.py
import pytest
from backend.workers import janitor_runner

@pytest.mark.asyncio
async def test_runner_invokes_both_sweeps(monkeypatch):
    calls = []
    async def fake_classifier(**_): calls.append("classifier"); return {"requeued": 0}
    async def fake_defender(**_):   calls.append("defender");   return {"requeued": 0}
    monkeypatch.setattr("backend.services.janitor.janitor_classifier_queue", fake_classifier)
    monkeypatch.setattr("backend.services.janitor.janitor_defender_queue", fake_defender)
    await janitor_runner.run_once()
    assert calls == ["classifier", "defender"]
```

**Step 2:** Run, expect ImportError on `janitor_runner`.

```bash
source .venv/bin/activate
pytest backend/tests/unit/test_janitor_runner.py -v
```

**Step 3:** Implement `backend/workers/janitor_runner.py` — `run_once()` opens Cosmos + Redis clients via the same factory the admin endpoint uses, calls both sweeps, logs JSON line, closes clients. `if __name__ == "__main__": asyncio.run(run_once())`.

**Step 4:** Re-run test. Expect pass.

**Step 5:** Create `janitor-cronjob.yaml`. Pattern after `charts/agentic-skill-hub/templates/curator/cronjob.yaml`. Schedule `{{ .Values.curator.janitor.schedule }}`. `concurrencyPolicy: Forbid`, `successfulJobsHistoryLimit: 3`, `failedJobsHistoryLimit: 3`, `startingDeadlineSeconds: 120`. Command: `python -m backend.workers.janitor_runner`.

**Step 6:** Helm lint + render.

```bash
helm lint charts/agentic-skill-hub
helm template t charts/agentic-skill-hub --show-only templates/curator/janitor-cronjob.yaml
```

**Step 7:** Full unit suite.

```bash
pytest backend/tests/unit -q
```

Expected: previous count +1 (`327 passed`).

**Step 8:** Commit.

```bash
git add backend/ charts/
git commit -m "feat(m5-gap2): periodic janitor CronJob + runner entrypoint + test"
```

---

## Task 3: Regenerate `infra/main.json` from current bicep

**Objective:** `main.json` is the compiled ARM output. It must match `main.bicep` + all module additions (communication, defender/notifier UAMIs, RBAC). Today it doesn't, which means an ARM-only deploy path would silently miss ACS and the new identities.

**Pitfall:** Earlier work flagged `bicep`/`az` CLI as unavailable on this machine — this is the blocker that left main.json stale in the first place.

**Files:**
- Modify: `infra/main.json` (regenerated)
- Modify: `infra/main.parameters.json` only if new required parameters appear (e.g. ACS data location)

**Step 1:** Check CLI availability.

```bash
which az bicep || brew install azure-cli && az bicep install
az bicep version
```

If brew install needed and not authorized, **stop and escalate to Michael** before proceeding — don't half-fix.

**Step 2:** Lint bicep first.

```bash
az bicep build --file infra/main.bicep --stdout > /tmp/main.json.preview
diff -u infra/main.json /tmp/main.json.preview | head -100
```

This shows the drift in a reviewable diff before overwriting.

**Step 3:** Regenerate.

```bash
az bicep build --file infra/main.bicep --outfile infra/main.json
```

**Step 4:** Sanity grep the new artifacts.

```bash
grep -c 'Microsoft.Communication/communicationServices' infra/main.json   # expect >=1
grep -c 'defender-uami\|defenderUami' infra/main.json                     # expect >=1
grep -c 'notifier-uami\|notifierUami' infra/main.json                     # expect >=1
grep -c 'quarantine' infra/main.json                                       # expect >=1
```

**Step 5:** What-if (read-only) against a dev subscription if Michael has one wired — otherwise skip with a note.

```bash
az deployment sub what-if --location westeurope \
  --template-file infra/main.bicep \
  --parameters @infra/main.parameters.json || true
```

**Step 6:** Commit.

```bash
git add infra/main.json infra/main.parameters.json
git commit -m "chore(m5-gap3): regenerate infra/main.json from current bicep modules"
```

---

## Task 4: De-dupe M5 wording in `AGENTS.md`

**Objective:** Each M5 sub-milestone appended its own paragraph. Result: same statements appear 2-3 times. Collapse into one M5 section.

**Files:**
- Modify: `AGENTS.md`

**Step 1:** Read.

```bash
grep -n "M5\|defender\|notifier\|quarantine" AGENTS.md
```

**Step 2:** Replace the scattered M5 lines with one consolidated `## M5 — Defender, Quarantine, Notifier` section. Keep one canonical statement of each invariant:
- Quarantine is the only delete-after-retention path
- Defender runs after classifier on the defender queue, KEDA-scaled
- Notifier consumes a dedicated queue, fire-and-forget enqueue, ACS for email + Graph for admin lookup (fake locally)
- Schedule reconciler patches the curator CronJob from Cosmos config every 60s

**Step 3:** Diff review.

```bash
git diff AGENTS.md
```

**Step 4:** Commit.

```bash
git add AGENTS.md
git commit -m "docs(m5-gap4): consolidate duplicated M5 sections in AGENTS.md"
```

---

## Task 5: Run the E2E + smoke script against the live emulator stack

**Objective:** `backend/tests/e2e/test_m5_full_flow.py` and `scripts/smoke_m5.sh` have only ever skipped. Prove they actually pass.

**Files:** None modified unless a real bug surfaces — then the bugfix becomes its own commit.

**Step 1:** Bring the stack up in foreground (no `--wait` flag — Hermes background pattern).

```bash
docker compose -f docker-compose.yml up -d
# wait loop, max ~120s
for i in {1..40}; do
  curl -sk https://localhost:8081/_explorer/emulator.pem -o /dev/null && \
  redis-cli -p 6379 ping >/dev/null 2>&1 && \
  curl -s http://localhost:10000/devstoreaccount1 >/dev/null 2>&1 && \
  echo READY && break
  sleep 3
done
```

**Step 2:** Run the smoke script end-to-end.

```bash
bash scripts/smoke_m5.sh
```

Expected: green. If it fails, capture the failure mode, file a fix in a separate commit on this branch labelled `fix(m5-gap5):` — do not loosen the test.

**Step 3:** Run the e2e file directly to be sure it was not skipped.

```bash
source .venv/bin/activate
pytest backend/tests/e2e/test_m5_full_flow.py -v --no-header
```

Expected: `passed`, **not** `skipped`.

**Step 4:** Run the full integration suite while emulators are up.

```bash
pytest backend/tests/integration -q
```

Expected: previously `26 skipped` should now be ≥20 passed.

**Step 5:** Tear down.

```bash
docker compose down -v
```

**Step 6:** If the run was clean, commit a one-liner status update in `docs/PRD.md` under the M5 section: "E2E validated on Azurite + Cosmos emulator + Redis on YYYY-MM-DD."

```bash
git add docs/PRD.md
git commit -m "docs(m5-gap5): record successful E2E validation against emulator stack"
```

---

## Task 6: Rename Helm chart `agentic-skill-hub` → `the-curator`

**Objective:** Repo is `The-Curator`, local path is `the-curator`, but the chart is still `agentic-skill-hub`. Cosmetic drift that will bite future deployments (release names, ServiceMonitor matchers, ArgoCD app paths).

**Risk note:** This rename is breaking for any cluster that already has a release of the old name. Since M5 has never been deployed (we never even shipped the reconciler), the cost is zero today and growing every day. Doing it now.

**Files:**
- Move: `charts/agentic-skill-hub/` → `charts/the-curator/`
- Modify: `charts/the-curator/Chart.yaml` (`name: the-curator`)
- Modify: `charts/the-curator/templates/_helpers.tpl` (template names use `the-curator.fullname`)
- Search and replace any string `agentic-skill-hub` across all `values*.yaml`, templates, and `scripts/`, `docs/`, `README.md`, `AGENTS.md`

**Step 1:** Inventory references first.

```bash
grep -rn "agentic-skill-hub" --exclude-dir=.venv --exclude-dir=node_modules --exclude-dir=.git .
```

**Step 2:** Rename directory + Chart.yaml.

```bash
git mv charts/agentic-skill-hub charts/the-curator
sed -i.bak 's/agentic-skill-hub/the-curator/g' charts/the-curator/Chart.yaml && rm charts/the-curator/Chart.yaml.bak
```

**Step 3:** Helper template + every reference in templates.

```bash
grep -rl 'agentic-skill-hub' charts/the-curator | xargs sed -i.bak 's/agentic-skill-hub/the-curator/g'
find charts/the-curator -name "*.bak" -delete
```

**Step 4:** Update non-chart references (scripts/docs).

```bash
grep -rl 'agentic-skill-hub' --exclude-dir=.git --exclude-dir=.venv --exclude-dir=node_modules .
# manually patch each, then re-grep to confirm zero hits remain
```

**Step 5:** Helm lint + render full chart.

```bash
helm lint charts/the-curator
helm template t charts/the-curator -f charts/the-curator/values-dev.yaml | head -50
helm template t charts/the-curator -f charts/the-curator/values-dev.yaml | grep -c "kind:"  # sanity
```

**Step 6:** Backend + frontend test suites — nothing should have changed but confirm.

```bash
source .venv/bin/activate
pytest backend/tests/unit -q
cd frontend && npx vitest run && cd ..
```

**Step 7:** Commit as one logical change.

```bash
git add -A
git commit -m "refactor(m5-gap6): rename helm chart agentic-skill-hub -> the-curator"
```

---

## Final Verification

After all 6 tasks:

```bash
source .venv/bin/activate
pytest backend/tests/unit -q                    # expect ~327 passed
cd frontend && npx vitest run && cd ..          # expect 23+ passed
helm lint charts/the-curator
git log --oneline feature/m5-defender-notifier..HEAD
git diff --stat feature/m5-defender-notifier
```

Open PR `feature/m5-followup-gaps` → `main` once Michael approves the push.

---

## Risks / Tradeoffs

1. **Task 3 (main.json):** depends on `az` + `bicep` CLI. If unavailable and Michael won't install, defer with a `TODO(m5-gap3)` block in `infra/README.md` and surface it.
2. **Task 5 (E2E):** real emulator runs can surface latent bugs. Budget for 1–2 extra fix commits inside this branch. If a deeper bug appears (e.g. message format mismatch between classifier and defender), STOP and escalate rather than papering over the test.
3. **Task 6 (chart rename):** burns the upgrade path for any existing Helm release of the old name. Confirmed acceptable because nothing is deployed yet. If the AKS cluster turns out to have a stale release, `helm uninstall agentic-skill-hub -n <ns>` before installing the new name.
4. **Task 2 (cron janitor):** default schedule `*/15 * * * *` is aggressive. Tune down to hourly in `values-prod.yaml` if Cosmos RU usage spikes.
5. **No new product features added.** This branch is intentionally just hygiene. New feature work goes in M6.

**Estimated effort:** ~3-4 hours focused, sequentially. Tasks 1, 2, 6 are mechanical. Task 5 is the wildcard — could be 20 min or could surface real bugs.
