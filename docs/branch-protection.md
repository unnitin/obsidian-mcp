# Branch Protection Setup

## What is actually configured

This repo is protected by a **ruleset** (Settings → Rules → Rulesets), not by
the classic per-branch rules this guide originally described. The live ruleset
is named `main` and its conditions include both `~DEFAULT_BRANCH` and `~ALL`,
so it applies to **every branch**, not just `main`.

| Rule | Effect |
|------|--------|
| `pull_request` | PR required, with **1 approving review** |
| `required_signatures` | Every commit must carry a verified signature |
| `creation` | Branch creation restricted |
| `deletion` | Branch deletion blocked |
| `non_fast_forward` | Force-pushes blocked |

Two consequences worth knowing before you wonder why a merge is blocked:

- **There is no required status check.** CI runs and the `All checks passed`
  job reports, but nothing blocks a merge on it — see *Making CI a real gate*
  below.
- **`required_signatures` applies to all branches**, so unsigned commits are
  refused on feature branches too. If you have not set up signing, pushes
  succeed only because an admin bypass is applied, and every merge needs
  `gh pr merge --admin`.

## Commit signing

Because `required_signatures` is active, configure signing before you start
work rather than discovering it at merge time. SSH signing is the least
ceremony:

```bash
git config --global gpg.format ssh
git config --global user.signingkey ~/.ssh/id_ed25519.pub
git config --global commit.gpgsign true
```

Then add that key as a **signing key** (not just an auth key) at
<https://github.com/settings/keys>. Verify with `git log --format='%h %G?'` —
`G` means a good signature, `N` means none.

Re-signing existing commits means rewriting history (`git rebase --exec 'git
commit --amend --no-edit -S'`) and force-pushing, which the `non_fast_forward`
rule blocks without a bypass. Cheaper to sign from the start.

## Making CI a real gate

CI currently passes decoratively. To have it block merges, add a
`required_status_checks` rule to the ruleset with the single check:

- `All checks passed`

That one job depends on lint, typecheck, and tests, so you do **not** need to
add `Lint & Format`, `Type Check`, or `Tests` individually.

Note the check only appears in the picker after it has run at least once on
the default branch.

## What runs on each PR

CI triggers on **every** pull request regardless of its base branch. It was
previously filtered to PRs targeting `main`, which meant a PR stacked on
another PR's branch got no checks at all.

```
pull_request (any base)
│
├── lint          ruff check + ruff format --check
├── typecheck     mypy strict
├── test          pytest + coverage ≥ 80%
│
└── all-checks-passed  ← the job to require (not yet required)
    (depends on all three; fails if any fail)
```

## Coverage threshold

The minimum coverage is set in `.github/workflows/ci.yml`:

```yaml
env:
  COVERAGE_THRESHOLD: "80"
```

Raise this as coverage improves. The `--cov-fail-under` flag makes pytest
exit non-zero if coverage drops below the threshold, which fails the `test`
job and therefore the gate.

## Local equivalent

Run the same checks locally before pushing:

```bash
uv run ruff check packages/backend/
uv run ruff format --check packages/backend/
uv run mypy packages/backend/src/
uv run pytest packages/backend/tests/ --cov=packages/backend/src --cov-fail-under=80 -v
```

Or just commit — pre-commit runs ruff and mypy automatically on every commit.
