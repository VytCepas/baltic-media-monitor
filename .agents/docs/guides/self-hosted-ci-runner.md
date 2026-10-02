# Self-hosted CI runner — the Actions-minutes escape hatch (PI-666)

When a **private** repo exhausts its GitHub Actions minutes (or hits a
spending-limit/payment problem), every workflow job fails at start:
*"The job was not started because recent account payments have failed or your
spending limit needs to be increased."* Self-hosted runners bill **zero**
minutes on private repos, and checks keep reporting to GitHub — branch
protection and the merge gate stay intact.

The scaffolded `ci.yml` reads `vars.CI_RUNS_ON` on its compute jobs; one repo
variable routes them to your machine. Nothing else changes.

## Diagnose first

```bash
gh run list --limit 5 --json conclusion,displayTitle   # billing lockout shows startup_failure
gh api /users/{username}/settings/billing/actions      # included vs used minutes (org repos: /orgs/{org}/...)
```

Meanwhile, verification never stops: `just ci` runs the exact same gate
locally (the recipes are what CI runs), and the pre-push git hook already
enforces it.

## Set up the runner (once)

1. Repo → **Settings → Actions → Runners → New self-hosted runner**, or:

   ```bash
   gh api -X POST repos/{owner}/{repo}/actions/runners/registration-token --jq .token
   ```

2. On the machine that will run CI (Linux x64 shown):

   ```bash
   mkdir -p ~/actions-runner && cd ~/actions-runner
   # download + extract the runner package per the Settings page instructions
   ./config.sh --url https://github.com/{owner}/{repo} --token <TOKEN> --ephemeral
   ./run.sh   # or install as a service: sudo ./svc.sh install && sudo ./svc.sh start
   ```

   **Prefer `--ephemeral`** — the runner takes one job and unregisters, so a
   compromised job can't persist on the machine. Re-register per job via a
   small loop or the service.

3. Install the host prerequisites below. The workflows install most of their
   own toolchain via pinned actions, so "the first run tells you what's
   missing" is only half-true — see the next section for the half it doesn't.

## Host prerequisites (PI-840)

The scaffolded workflows provision their own tools where they can (pinned
actions install `just`, `shfmt`, the language toolchain, gitleaks, semgrep) —
but they **assume** a few binaries are already on the host, because GitHub's
`ubuntu-24.04` images preinstall them. A bare box (fresh WSL distro, minimal
container) does not. The failure mode is nasty: a missing binary kills the
step with **exit 127** mid-job — `command not found` deep inside a workflow
step reads as a failure of whatever that step checks, not an environment
problem (observed live: a missing `jq` on a fresh WSL box masqueraded as a
PR-metadata failure and cost a debugging detour).

Assumed-present binaries:

| Binary | Used by | Debian/Ubuntu install |
|---|---|---|
| `git` | checkout, every workflow step that touches the repo | `sudo apt install git` |
| `curl` | download/API steps | `sudo apt install curl` |
| `tar`, `gzip` | action tool downloads | usually present |
| `gh` | `ci.yml`'s post-failure PR comment step runs `gh pr comment` on this runner whenever a PR job fails | [cli.github.com](https://cli.github.com) |
| `shellcheck` | the shell-lint step (preinstalled on GitHub images — **absent on bare hosts**) | `sudo apt install shellcheck` |
| `docker` | container image jobs, if enabled | [docs.docker.com/engine/install](https://docs.docker.com/engine/install/) |

Preflight a new runner host with:

```bash
for b in git curl tar gzip gh jq shellcheck; do
  command -v "$b" >/dev/null || echo "MISSING: $b"
done
```

## Switch CI over

```bash
just ci-local-on     # gh variable set CI_RUNS_ON --body self-hosted
just ci-local-off    # back to GitHub-hosted
```

**Only set the variable when a runner is actually registered and online** —
otherwise jobs queue forever, which is harder to notice than the billing
error.

**Single runner = sequential CI.** One runner executes jobs one at a time
(~15–20 min for a full pipeline). Register a second runner to restore
parallelism.


## Trust model — read before enabling

- Use this only on **private repos with trusted collaborators**. A
  self-hosted runner executes whatever the workflow (and the PR) tells it to,
  on your machine.
- Never process public-fork PRs on a self-hosted runner.
- Secret/PAT-bearing workflows — including any
  release/deploy/registry-publish workflows you enable — are **deliberately
  pinned** to GitHub-hosted ephemeral runners so tokens never materialize on
  your machine. They cost seconds and fit the free tier once `ci.yml` moves
  off. Do not point them at `CI_RUNS_ON`.
- The `scorecard` job stays GitHub-hosted (an OSSF requirement).

## Alternatives

Make the repo public (Actions is free), raise the spending limit, or wait for
the monthly reset. `act` (nektos/act, needs Docker) can run workflows ad hoc,
but image drift from real runners makes it a debugging project of its own —
prefer the runner.
