# YouKnowMe Curator Launcher Runbook

This runbook maintains the live Curator launcher on `hermes-vps`.

## Current Deployment

- sandbox-broker config: `/docker/gh-agent-broker/configs/sandbox-beta.yaml`
- broker env: `/docker/gh-agent-broker/.env`
- deterministic Curator image: `youknowme:curator-policy-tools-20260611-002930`
- manual model Curator image: `youknowme:curator-policy-tools-20260611-002930`
- state-only Curator image: `youknowme:curator-policy-tools-20260611-002930`
- launcher user: `sandbox-curator-timer`
- timer env: `/home/sandbox-curator-timer/.config/gh-agent-broker/operator.env`
- systemd service: `ykm-curator-launch.service`
- systemd timer: `ykm-curator-launch.timer`
- timer sandbox profile: `ykm-curator-dry-run`
- manual state-only sandbox profile: `ykm-curator-state-only`
- manual model sandbox profile: `ykm-curator-dry-run-model`

The timer user has only the scoped launch token. It cannot inspect runs, read artifacts, stop runs,
choose Docker images, choose mounts, or access broker secrets. sandbox-broker owns Docker execution,
template policy, mounts, credentials, artifacts, and audit.

## Normal Checks

Check the timer and sandbox-broker health:

```bash
ssh hermes-vps '
systemctl is-enabled ykm-curator-launch.timer
systemctl is-active ykm-curator-launch.timer
systemctl list-timers ykm-curator-launch.timer --no-pager
curl -fsS http://127.0.0.1:8091/healthz
'
```

Check recent launch attempts:

```bash
ssh hermes-vps 'journalctl -u ykm-curator-launch.service -n 50 --no-pager'
```

The journal should show a JSON launch response with `run_id`, `repo`, `branch`, `status`, and
`deadline`. It must not show token values.

## Manual Launch

Start the same path the timer uses:

```bash
ssh hermes-vps 'systemctl start ykm-curator-launch.service'
```

Read the run ID from journald:

```bash
ssh hermes-vps 'journalctl -u ykm-curator-launch.service -n 20 --no-pager'
```

Poll and inspect the run with the human operator token from the broker private env. Do not print the
token:

```bash
ssh hermes-vps '
cd /docker/gh-agent-broker
set -a; . ./.env; set +a
run_id=<RUN_ID>
base=http://127.0.0.1:8091
curl -fsS -H "Authorization: Bearer ${YKM_CURATOR_SANDBOX_ADMIN_TOKEN}" "$base/v1/runs/$run_id"
curl -fsS -H "Authorization: Bearer ${YKM_CURATOR_SANDBOX_ADMIN_TOKEN}" "$base/v1/runs/$run_id/artifacts"
'
```

For full local inspection on the VPS, read the report from the sandbox run directory:

```bash
ssh hermes-vps '
run_id=<RUN_ID>
python3 - <<PY
import json
from pathlib import Path
report = Path("/srv/hermes-sandbox-broker/runs") / "$run_id" / "output/run-report.json"
p = json.loads(report.read_text())
for key in [
    "status",
    "mode",
    "checkpoint_advanced",
    "feedback_decisions_appended",
    "upload_metadata_update_count",
    "github_mutation_count",
    "model_call_count",
    "validation_failure_count",
]:
    print(f"{key}={p.get(key)}")
PY
'
```

Expected dry-run safety values:

- `status=pass`
- `mode=dry_run`
- `checkpoint_advanced=False`
- `feedback_decisions_appended=0`
- `upload_metadata_update_count=0`
- `github_mutation_count=0`
- `model_call_count=0`

## Manual State-Only Launch

The state-only profile is available for manual operator launches. It may append safe feedback
decisions and write upload review plans, but it must not call models or mutate GitHub.

```bash
ssh hermes-vps '
cd /docker/gh-agent-broker
set -a; . ./.env; set +a
curl -fsS -X POST \
  -H "Authorization: Bearer ${YKM_CURATOR_SANDBOX_ADMIN_TOKEN}" \
  http://127.0.0.1:8091/v1/launch-profiles/ykm-curator-state-only/launch
'
```

Latest smoke results:

- `20260610T055824Z-3469843729f17a9d`: appended `22` safe feedback decisions, made no model calls
  and no GitHub mutations, wrote `3` upload review previews, and correctly left the feedback
  checkpoint unadvanced because two actionable feedback records remained unresolved.
- `20260610T055856Z-4aaf168189c84878`: appended `0` duplicate decisions and saw only those two
  unresolved feedback records, confirming decision idempotency.
- `20260610T060525Z-362130b4bb7a6567`: confirmed the upload draft-readiness classifier on the real
  queue. The smoke upload needs owner action because it has no frontmatter, the dev-environment
  upload needs owner action because `type: project` is not in corpus policy, and the Santa Cruz hot
  tub manual is a `corpus_pr_candidate` for
  `homemaint/santa-cruz-freeflow-excursion-owner-manual.md` with unsupported tags dropped for
  review.

The hourly timer still points at `ykm-curator-dry-run` until the remaining actionable feedback is
resolved or the state-only report status policy changes. Running state-only on the timer today would
be safe, but it would produce hourly `fail` reports while those unresolved records remain.

## Curator PR Reassignment

GitHub does not allow assigning pull requests to GitHub Apps, so Curator PR ownership is indicated
with labels and review state instead of assignees.

Use the label exactly as created in `grubbyhacker/ykmcorpus`:

```text
ym-curator: needs work
ym-curator: waiting-review
```

Applying that label to an open Curator-authored PR means the Curator should pick the PR back up on
the next reconciliation-capable run. A submitted `CHANGES_REQUESTED` review is also an automatic
Curator work signal; the label is the explicit manual wake-up signal. After the Curator pushes a
repair, it must also post a PR conversation comment saying the fix is complete and owner review is
needed again. The comment should state what changed, why the original PR broke, and why the repair
path should prevent a silent repeat. The Curator then dismisses addressed stale `CHANGES_REQUESTED`
reviews, resolves addressed review threads, adds `ym-curator: waiting-review`, and removes
`ym-curator: needs work`. That label suppresses repeated repair attempts while the owner reviews the
new head. If a Curator PR has no validation status or check runs, reconciliation should report
`checks_missing`, because skipped CI is not a passing validation state.

## Manual Model Launch

The model-backed profile is intentionally manual-only. The timer principal must not list
`ykm-curator-dry-run-model` in `allowed_profiles`; only `ykm-curator-operator` should be able to
launch or inspect it.

Launch it with the operator token:

```bash
ssh hermes-vps '
cd /docker/gh-agent-broker
set -a; . ./.env; set +a
curl -fsS -X POST \
  -H "Authorization: Bearer ${YKM_CURATOR_SANDBOX_ADMIN_TOKEN}" \
  http://127.0.0.1:8091/v1/launch-profiles/ykm-curator-dry-run-model/launch
'
```

Expected model dry-run safety values:

- `status=pass`
- `mode=dry_run`
- `checkpoint_advanced=False`
- `feedback_decisions_appended=0`
- `upload_metadata_update_count=0`
- `github_mutation_count=0`
- `model_call_count=1`
- `partial_failures=[]`

Inspect model planning quality before trusting the run:

- compare `proposed_actions` with deterministic feedback categories and evidence;
- confirm every included feedback ID is covered by at least one action;
- confirm no positive or non-actionable feedback became an issue or PR;
- confirm every `corpus_pr` cites source, section, or upload evidence;
- confirm every `link_to_upload` cites upload evidence;
- check `model_token_count` for unexpected prompt growth.

Run the committed offline model-planning evals locally after prompt or schema changes:

```bash
mise run curator-model-eval
```

The profile mounts only `/credentials/ykm-curator/proxy.env`, which contains the proxy token for
`gh-agent-proxy`. It must not mount provider keys or the broker `.env`.

Curator model aliases in `/docker/gh-agent-broker/configs/litellm.yaml` should use
`api_key: os.environ/OPENROUTER_CURATOR_API_KEY`. Keep the general YKM runtime/index keys separate
from the Curator key in `/docker/gh-agent-broker/.env`.

For PR repair runs, Codex uses the scoped proxy token, not provider credentials or borrowed
subscription credentials. The Curator worker should receive:

```text
CODEX_PROXY_BASE_URL=http://gh-agent-proxy:8092/v1
CODEX_PROXY_TOKEN=<scoped Codex proxy token>
```

The generated Codex config sets `wire_api = "responses"` and passes `X-GH-Agent-Run-ID` from
`YKM_CURATOR_RUN_ID` through `env_http_headers`. The default repair model alias is
`ykm-codex-gpt-5-mini`; `ykm-codex-haiku` and `ykm-codex-sonnet` remain useful comparison or
fallback aliases.

The current Curator GitHub App scope does not include workflow write permission. If Codex repairs
touch `.github/workflows/*`, the Curator should report the repair as rejected/permission-blocked
instead of attempting to push; base-repository CI path-filter changes need a separate privileged
maintenance PR or an explicit App permission change.

Production enablement still requires a scoped LiteLLM virtual key for the Codex proxy token and a
live LiteLLM/OpenRouter E2E after the broker change is merged.

Latest model-planning finding:

- `20260610T005317Z-563dd947d9bf42ef` failed closed on invalid model output while preserving a
  safer deterministic fallback plan.
- The fallback plan had `capacity=0`, `19` no-action records, `3` upload-linked records, and `2`
  corpus candidates.
- Treat model-backed planning as not ready for state advancement; deterministic planning is the
  current useful path.

## Timer Control

Disable scheduled launches without removing the manual service:

```bash
ssh hermes-vps 'systemctl disable --now ykm-curator-launch.timer'
```

Re-enable after a successful manual smoke:

```bash
ssh hermes-vps 'systemctl enable --now ykm-curator-launch.timer'
```

The service remains manually runnable while the timer is disabled.

## Token Boundary Check

The timer token should be able to launch only. These checks should return `403` for run inspection
and mutation surfaces:

```bash
ssh hermes-vps '
cd /docker/gh-agent-broker
set -a; . ./.env; set +a
base=http://127.0.0.1:8091
for path in \
  /v1/runs \
  /v1/runs/not-a-run/logs \
  /v1/runs/not-a-run/artifacts \
  /v1/runs/not-a-run/lessons
do
  curl -sS -o /dev/null -w "%{http_code} $path\n" \
    -H "Authorization: Bearer ${YKM_CURATOR_SANDBOX_TIMER_TOKEN}" \
    "$base$path"
done
for path in /v1/runs/not-a-run/stop /v1/runs/not-a-run/cleanup
do
  curl -sS -o /dev/null -w "%{http_code} $path\n" \
    -X POST \
    -H "Authorization: Bearer ${YKM_CURATOR_SANDBOX_TIMER_TOKEN}" \
    "$base$path"
done
'
```

Missing-token launch should return `401`:

```bash
ssh hermes-vps '
curl -sS -o /dev/null -w "%{http_code}\n" \
  -X POST \
  http://127.0.0.1:8091/v1/launch-profiles/ykm-curator-dry-run/launch
'
```

## Updating The Curator Image

From this repository on the development machine:

```bash
docker build --platform linux/amd64 -t youknowme:curator-launcher-YYYYMMDD .
docker save youknowme:curator-launcher-YYYYMMDD | ssh hermes-vps 'docker load'
```

On the VPS:

1. Back up `/docker/gh-agent-broker/configs/sandbox-beta.yaml`.
2. Change `templates.ykm-curator-dry-run.image` to the new tag.
3. Validate compose rendering.
4. Recreate `sandbox-broker`.
5. Run a manual launch smoke before leaving the timer enabled.

Commands:

```bash
ssh hermes-vps '
cd /docker/gh-agent-broker
stamp=$(date -u +%Y%m%dT%H%M%SZ)
cp configs/sandbox-beta.yaml "configs/sandbox-beta.yaml.bak-curator-image-$stamp"
docker compose config >/tmp/gh-agent-broker-compose-rendered.yaml
docker compose up -d sandbox-broker
curl -fsS http://127.0.0.1:8091/healthz
systemctl start ykm-curator-launch.service
'
```

## Broker Config Changes

For launch-profile, template, token-env, or operator-principal changes:

1. Disable the timer if the change could break launches.
2. Back up `/docker/gh-agent-broker/configs/sandbox-beta.yaml` and `/docker/gh-agent-broker/.env`.
3. Edit config/env.
4. Run `docker compose config`.
5. Recreate `sandbox-broker`.
6. Verify health.
7. Run token-boundary checks and a manual Curator smoke.
8. Re-enable the timer.

```bash
ssh hermes-vps '
systemctl disable --now ykm-curator-launch.timer
cd /docker/gh-agent-broker
stamp=$(date -u +%Y%m%dT%H%M%SZ)
cp configs/sandbox-beta.yaml "configs/sandbox-beta.yaml.bak-$stamp"
cp .env ".env.bak-$stamp"
# edit configs/sandbox-beta.yaml and .env
docker compose config >/tmp/gh-agent-broker-compose-rendered.yaml
docker compose up -d sandbox-broker
curl -fsS http://127.0.0.1:8091/healthz
'
```

## Token Rotation

Rotate the timer token in two places:

- broker env: `/docker/gh-agent-broker/.env`
- timer env: `/home/sandbox-curator-timer/.config/gh-agent-broker/operator.env`

The values must match. The timer user must not receive the full broker env file.

After rotation:

```bash
ssh hermes-vps '
cd /docker/gh-agent-broker
docker compose up -d sandbox-broker
curl -fsS http://127.0.0.1:8091/healthz
systemctl start ykm-curator-launch.service
'
```

Rotate the human operator token only in `/docker/gh-agent-broker/.env`, then recreate
`sandbox-broker`. Do not put the human operator token in the timer user's env file.

## Rollback

If a config change breaks sandbox-broker:

```bash
ssh hermes-vps '
cd /docker/gh-agent-broker
cp configs/sandbox-beta.yaml.bak-<STAMP> configs/sandbox-beta.yaml
cp .env.bak-<STAMP> .env
docker compose up -d sandbox-broker
curl -fsS http://127.0.0.1:8091/healthz
'
```

If scheduled launches are misbehaving but sandbox-broker is healthy:

```bash
ssh hermes-vps 'systemctl disable --now ykm-curator-launch.timer'
```

Do not delete run directories during incident response unless disk pressure requires it. Reports and
artifacts under `/srv/hermes-sandbox-broker/runs/<run_id>/` are the primary evidence for debugging.
