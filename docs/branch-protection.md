# Branch Protection Setup

After pushing CI workflows, configure GitHub to block merges that fail tests.

## Steps

1. Go to **Settings → Branches** in the GitHub repo
2. Click **Add branch protection rule**
3. Branch name pattern: `main`
4. Enable the following:

| Setting | Value |
|---------|-------|
| Require a pull request before merging | ✅ |
| Require approvals | 1 (or 0 for solo projects) |
| Require status checks to pass before merging | ✅ |
| Require branches to be up to date before merging | ✅ |
| Do not allow bypassing the above settings | ✅ |

5. Under **Status checks that are required**, search for and add:
   - `All checks passed`

   This single job gates on lint + typecheck + tests all passing.
   You do **not** need to add `lint`, `typecheck`, or `Tests` individually.

6. Click **Save changes**

## What runs on each PR to main

```
pull_request → main
│
├── lint          ruff check + ruff format --check
├── typecheck     mypy strict
├── test          pytest + coverage ≥ 80%
│
└── all-checks-passed  ← required status check
    (depends on all three; blocks merge if any fail)
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
