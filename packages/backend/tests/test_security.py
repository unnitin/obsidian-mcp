"""
Security tests — ensure no secrets, API keys, or sensitive data are committed.

Scans all git-tracked files for:
  - Hardcoded credentials (API keys, passwords, tokens, private keys)
  - Sensitive files accidentally tracked (.env, *.pem, *.p12, etc.)
  - Insecure defaults in the Settings config class
  - Plaintext secrets in CI/CD workflows
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

# ── Repo root ─────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).parent.parent.parent.parent


def _git_tracked_files() -> list[Path]:
    """Return all files currently tracked by git."""
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [REPO_ROOT / p for p in result.stdout.splitlines() if p.strip()]


def _read_safe(path: Path) -> str:
    """Read a text file, skipping binary files."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# ── Secret patterns ───────────────────────────────────────────────────────────

# Each tuple: (label, compiled_regex, list_of_safe_example_strings_that_must_not_match)
_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "hardcoded API key assignment",
        re.compile(
            r'(?i)(api[_-]?key|apikey)\s*[:=]\s*["\'][A-Za-z0-9+/\-_]{16,}["\']',
            re.MULTILINE,
        ),
    ),
    (
        "hardcoded password assignment",
        re.compile(
            r'(?i)(password|passwd|pwd)\s*[:=]\s*["\'][^"\']{6,}["\']',
            re.MULTILINE,
        ),
    ),
    (
        "hardcoded secret/token assignment",
        re.compile(
            r'(?i)(secret|auth[_-]?token|access[_-]?token)\s*[:=]\s*["\'][A-Za-z0-9+/\-_]{16,}["\']',
            re.MULTILINE,
        ),
    ),
    (
        "PEM private key header",
        re.compile(
            r"-----BEGIN\s+(RSA|DSA|EC|OPENSSH|PRIVATE)\s+(PRIVATE\s+)?KEY-----",
            re.MULTILINE,
        ),
    ),
    (
        "AWS access key ID",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    ),
    (
        "GitHub personal access token (classic)",
        re.compile(r"\bghp_[A-Za-z0-9]{36}\b"),
    ),
    (
        "GitHub fine-grained token",
        re.compile(r"\bgithub_pat_[A-Za-z0-9_]{82}\b"),
    ),
    (
        "Anthropic API key",
        re.compile(r"\bsk-ant-[A-Za-z0-9\-_]{40,}\b"),
    ),
    (
        "OpenAI API key",
        re.compile(r"\bsk-[A-Za-z0-9]{32,}\b"),
    ),
    (
        "Slack bot/webhook token",
        re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"),
    ),
    (
        "generic high-entropy hex secret (32+ chars)",
        re.compile(r'(?i)(secret|key|token|password)\s*[:=]\s*["\'][0-9a-f]{32,}["\']'),
    ),
    (
        "connection string with inline password",
        re.compile(
            r"(?i)(postgres|mysql|mongodb|redis|amqp)://[^:]+:[^@\s\"']{4,}@",
            re.MULTILINE,
        ),
    ),
]

# Files/patterns to exclude from secret scanning (test fixtures, docs, examples)
_EXCLUDE_PATHS: frozenset[str] = frozenset(
    {
        "packages/backend/tests/test_security.py",  # this file — contains patterns as strings
    }
)

# Extensions to skip (binary, lock files, images)
_SKIP_EXTENSIONS: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff", ".woff2", ".ttf", ".eot"}
)


# ── Sensitive file names ───────────────────────────────────────────────────────

_SENSITIVE_FILENAME_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^\.env(\..+)?$"),  # .env, .env.local, .env.production
    re.compile(r"^\.env\.example$"),  # .env.example can contain real values
    re.compile(r".*\.(pem|p12|pfx|key|jks|keystore|crt|cer)$", re.IGNORECASE),
    re.compile(r"^credentials(\.json)?$", re.IGNORECASE),
    re.compile(r"^secrets(\.(json|yaml|yml|toml))?$", re.IGNORECASE),
    re.compile(r"^.*service.?account.*\.json$", re.IGNORECASE),  # GCP service accounts
    re.compile(r"^id_(rsa|dsa|ecdsa|ed25519)(\.pub)?$"),  # SSH keys
]


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestNoHardcodedSecrets:
    """Scan every tracked file for credential patterns."""

    @pytest.fixture(scope="class")
    def tracked_files(self) -> list[Path]:
        return _git_tracked_files()

    def test_no_secret_patterns_in_tracked_files(self, tracked_files: list[Path]) -> None:
        """Fail if any tracked file contains a known secret pattern."""
        violations: list[str] = []

        for path in tracked_files:
            rel = str(path.relative_to(REPO_ROOT))
            if rel in _EXCLUDE_PATHS:
                continue
            if path.suffix in _SKIP_EXTENSIONS:
                continue

            content = _read_safe(path)
            if not content:
                continue

            for label, pattern in _SECRET_PATTERNS:
                for match in pattern.finditer(content):
                    line_no = content[: match.start()].count("\n") + 1
                    violations.append(f"{rel}:{line_no}: [{label}] → {match.group()!r}")

        assert not violations, (
            f"Potential secrets found in {len(violations)} location(s):\n"
            + "\n".join(f"  {v}" for v in violations)
        )


class TestNoSensitiveFilesTracked:
    """Ensure sensitive file types are not tracked in git."""

    @pytest.fixture(scope="class")
    def tracked_filenames(self) -> list[tuple[str, str]]:
        """Return (relative_path, filename) for all tracked files."""
        return [(str(p.relative_to(REPO_ROOT)), p.name) for p in _git_tracked_files()]

    def test_no_env_files_tracked(self, tracked_filenames: list[tuple[str, str]]) -> None:
        matches = [
            rel
            for rel, name in tracked_filenames
            if re.match(r"^\.env(\..+)?$", name) and not name.endswith(".example")
        ]
        assert not matches, f".env files must not be tracked: {matches}"

    def test_no_private_key_files_tracked(self, tracked_filenames: list[tuple[str, str]]) -> None:
        key_patterns = re.compile(r"\.(pem|p12|pfx|key|jks|keystore)$", re.IGNORECASE)
        matches = [rel for rel, name in tracked_filenames if key_patterns.search(name)]
        assert not matches, f"Private key files must not be tracked: {matches}"

    def test_no_credential_files_tracked(self, tracked_filenames: list[tuple[str, str]]) -> None:
        for pattern in _SENSITIVE_FILENAME_PATTERNS:
            matches = [rel for rel, name in tracked_filenames if pattern.match(name)]
            assert not matches, (
                f"Sensitive files matching {pattern.pattern!r} must not be tracked: {matches}"
            )


class TestGitignoreCoversSecrets:
    """.gitignore must exclude common sensitive file patterns."""

    @pytest.fixture(scope="class")
    def gitignore_content(self) -> str:
        gitignore = REPO_ROOT / ".gitignore"
        assert gitignore.exists(), ".gitignore must exist"
        return gitignore.read_text()

    @pytest.mark.parametrize(
        "pattern",
        [
            ".env",
            "*.pem",
            "*.key",
            "*.p12",
        ],
    )
    def test_gitignore_includes_sensitive_pattern(
        self, gitignore_content: str, pattern: str
    ) -> None:
        assert pattern in gitignore_content, (
            f".gitignore must include '{pattern}' to prevent accidental commits"
        )


class TestConfigHasNoHardcodedSecrets:
    """Settings class must not expose hardcoded secrets as defaults."""

    def test_vault_path_has_no_default(self) -> None:
        """vault_path must be required (no default) so it's always from env/config."""
        from obsidian_search.config import Settings
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Settings()  # type: ignore[call-arg]

    def test_settings_source_is_env_not_hardcoded(self) -> None:
        """Settings must read from environment variables, not hardcoded literals."""
        import inspect

        from obsidian_search import config

        source = inspect.getsource(config)
        # Ensure no real-looking API keys are embedded in the config module
        for label, pattern in _SECRET_PATTERNS:
            match = pattern.search(source)
            assert match is None, (
                f"config.py contains a potential secret [{label}]: {match.group()!r}"
            )


class TestCIWorkflowSecurity:
    """CI/CD workflow files must not contain plaintext secrets."""

    @pytest.fixture(scope="class")
    def workflow_files(self) -> list[Path]:
        workflows_dir = REPO_ROOT / ".github" / "workflows"
        if not workflows_dir.exists():
            return []
        return list(workflows_dir.glob("*.yml")) + list(workflows_dir.glob("*.yaml"))

    def test_workflows_use_secrets_context_not_plaintext(self, workflow_files: list[Path]) -> None:
        """Workflow files must reference ${{ secrets.X }}, not plaintext values."""
        violations: list[str] = []
        for wf in workflow_files:
            content = _read_safe(wf)
            rel = str(wf.relative_to(REPO_ROOT))
            for label, pattern in _SECRET_PATTERNS:
                for match in pattern.finditer(content):
                    line_no = content[: match.start()].count("\n") + 1
                    violations.append(f"{rel}:{line_no}: [{label}] → {match.group()!r}")

        assert not violations, "Plaintext secrets found in CI workflows:\n" + "\n".join(
            f"  {v}" for v in violations
        )

    def test_workflows_exist(self, workflow_files: list[Path]) -> None:
        assert workflow_files, "At least one GitHub Actions workflow must exist"
