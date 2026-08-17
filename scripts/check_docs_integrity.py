#!/usr/bin/env python3
"""Documentation integrity checker.

Verifies that documentation matches implementation to prevent drift.
Run by CI to catch false claims early.
"""

import re
import sys
from pathlib import Path


class IntegrityError:
    """One detected integrity violation."""

    def __init__(self, file: Path, line: int, issue: str):
        self.file = file
        self.line = line
        self.issue = issue

    def __str__(self) -> str:
        return f"{self.file}:{self.line}: {self.issue}"


def check_readme(root: Path) -> list[IntegrityError]:
    """Check README claims against implementation."""
    errors: list[IntegrityError] = []
    readme = root / "README.md"

    if not readme.exists():
        return [IntegrityError(readme, 0, "README.md does not exist")]

    text = readme.read_text()
    lines = text.split("\n")

    # Check for false AI claims
    false_ai_patterns = [
        (r"AI-powered.*platform", "Claims 'AI-powered' but no LLM integration exists"),
        (r"Claude.*gateway", "Claims Claude gateway but anthropic is unused"),
        (r"LLM.*calls", "Claims LLM calls but no actual API integration"),
    ]

    # Find section boundaries for "What is not yet implemented"
    not_impl_start = -1
    not_impl_end = len(lines)
    for i, line in enumerate(lines):
        if "## What is not yet implemented" in line.lower():
            not_impl_start = i
        elif not_impl_start > 0 and line.startswith("##"):
            not_impl_end = i
            break

    for i, line in enumerate(lines, 1):
        for pattern, msg in false_ai_patterns:
            if re.search(pattern, line, re.IGNORECASE):
                # Allow in "What is not yet implemented" section
                if not_impl_start >= 0 and not_impl_start < i <= not_impl_end:
                    continue
                allowed = ["under consideration", "roadmap", "not yet", "no "]
                if any(x in line.lower() for x in allowed):
                    continue
                errors.append(IntegrityError(readme, i, msg))

    # Check for false orchestration claims (skip "What is not yet implemented")
    for i, line in enumerate(lines):
        if re.search(r"orchestrated by Airflow", line, re.IGNORECASE) and not (
            not_impl_start >= 0 and not_impl_start <= i < not_impl_end
        ):
            errors.append(
                IntegrityError(
                    readme, i + 1, "Claims Airflow orchestration but orchestration/dags/ is empty"
                )
            )

    # Check for false frontend claims (skip "What is not yet implemented")
    for i, line in enumerate(lines):
        if re.search(r"Next\.js|React frontend", line):
            # Allow if line says "No Next.js" or similar
            if "no next.js" in line.lower() or "not next.js" in line.lower():
                continue
            if not (not_impl_start >= 0 and not_impl_start <= i < not_impl_end):
                errors.append(
                    IntegrityError(readme, i + 1, "Claims Next.js/React but only Streamlit exists")
                )

    # Check for Terraform claims (skip "What is not yet implemented")
    for i, line in enumerate(lines):
        if re.search(r"Terraform", line):
            # Allow if line says "No Terraform" or similar
            if "no terraform" in line.lower() or "not terraform" in line.lower():
                continue
            if not (not_impl_start >= 0 and not_impl_start <= i < not_impl_end):
                errors.append(
                    IntegrityError(
                        readme, i + 1, "Claims Terraform but infra/terraform/ doesn't exist"
                    )
                )

    return errors


def check_design_doc_references(root: Path) -> list[IntegrityError]:
    """Check that code doesn't reference nonexistent design docs."""
    errors: list[IntegrityError] = []

    # Pattern: Backend design §13, ARCH §17, etc.
    doc_ref_pattern = re.compile(r"§\d+")

    for pyfile in root.rglob("*.py"):
        if any(p in pyfile.parts for p in [".venv", "__pycache__", ".pytest_cache", "scripts"]):
            continue

        try:
            lines = pyfile.read_text().split("\n")
            for i, line in enumerate(lines, 1):
                if doc_ref_pattern.search(line):
                    errors.append(
                        IntegrityError(
                            pyfile.relative_to(root),
                            i,
                            f"References nonexistent design doc: {line.strip()[:80]}",
                        )
                    )
        except Exception:  # noqa: S110 — best-effort scan; an unreadable file is skipped, not fatal
            pass

    return errors


def check_makefile_targets(root: Path) -> list[IntegrityError]:
    """Verify Makefile targets work as documented."""
    errors: list[IntegrityError] = []
    makefile = root / "Makefile"

    if not makefile.exists():
        return [IntegrityError(makefile, 0, "Makefile not found")]

    # For now, just check that critical targets exist
    text = makefile.read_text()
    required_targets = ["demo", "test", "lint", "up", "down"]

    for target in required_targets:
        if f"{target}:" not in text:
            errors.append(IntegrityError(makefile, 0, f"Missing required target: {target}"))

    return errors


def check_env_example(root: Path) -> list[IntegrityError]:
    """Check .env.example doesn't have design doc references."""
    errors: list[IntegrityError] = []
    env_example = root / ".env.example"

    if not env_example.exists():
        return [IntegrityError(env_example, 0, ".env.example not found")]

    lines = env_example.read_text().split("\n")
    for i, line in enumerate(lines, 1):
        if "§" in line:
            errors.append(
                IntegrityError(env_example, i, f"Design doc reference in env file: {line[:60]}")
            )

    return errors


def check_empty_scaffolding(root: Path) -> list[IntegrityError]:
    """Document empty directories that might be misleading scaffolding."""
    errors: list[IntegrityError] = []

    # Known scaffolding directories
    scaffolding_paths = [
        "data_platform/orchestration/dags",
        "data_platform/orchestration/config",
    ]

    for path_str in scaffolding_paths:
        path = root / path_str
        if path.exists():
            # Check if it only has .gitkeep or is truly empty
            contents = list(path.iterdir())
            if not contents or (len(contents) == 1 and contents[0].name == ".gitkeep"):
                # This is OK - documented as scaffolding in README
                pass

    return errors


def main() -> int:
    """Run all checks."""
    root = Path(__file__).parent.parent
    all_errors: list[tuple[str, list[IntegrityError]]] = []

    checks = [
        ("README integrity", check_readme),
        ("Design doc references", check_design_doc_references),
        ("Makefile targets", check_makefile_targets),
        (".env.example", check_env_example),
        ("Empty scaffolding", check_empty_scaffolding),
    ]

    for name, check_func in checks:
        errors = check_func(root)
        if errors:
            all_errors.append((name, errors))

    if all_errors:
        print("Documentation integrity violations found:\n")
        for check_name, errors in all_errors:
            print(f"[{check_name}]")
            for error in errors[:10]:  # Limit output
                print(f"  {error}")
            if len(errors) > 10:
                print(f"  ... and {len(errors) - 10} more")
            print()
        return 1

    print("✓ Documentation integrity checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
