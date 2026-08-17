#!/usr/bin/env python3
"""Remove dangling design document references from code comments.

Removes section references like "(Backend design §13)", "per ETL §5", etc.
while preserving the actual technical reasoning in comments.
"""

import re
import sys
from pathlib import Path


def remove_doc_refs(text: str) -> str:
    """Remove design document section references.

    Conservative patterns that only match clear design doc references,
    not function calls or other code structures.
    """
    # Pattern 1: Specific parenthetical references with known doc names
    # Only match when the entire content is a design doc reference
    # Use [ \t] instead of \s to avoid matching newlines
    doc_names = r'(ARCH|Backend|DevOps|Analytics|ETL|AI|UX|Database|System)[ \t]+design'
    text = re.sub(rf'[ \t]*\([ \t]*{doc_names}[ \t]+§\d+[ \t]*\)', '', text)
    text = re.sub(
        rf'[ \t]*\([ \t]*{doc_names}[ \t]+§\d+[ \t]*/[ \t]*{doc_names}[ \t]+§\d+[ \t]*\)', '', text
    )

    # Pattern 2: Standalone section references like "Analytics §9" or "ARCH §17"
    # Only match spaces, not newlines, to preserve line structure
    text = re.sub(
        r'[ \t]+(ARCH|DevOps|Backend|Analytics|ETL|AI|UX|Database|System)[ \t]+§\d+\b', '', text
    )

    # Pattern 3: References at the start of comments like "per Backend design §16,"
    text = re.sub(rf'[ \t]*(per|see)[ \t]+{doc_names}[ \t]+§\d+[,.]?[ \t]*', ' ', text)

    # Pattern 4: Standalone §references that are clearly just section numbers
    text = re.sub(r'[ \t]+§\d+\b', '', text)

    # Pattern 5: Parenthetical section references like "(§9)" or "(§14)"
    text = re.sub(r'[ \t]*\(§\d+\)', '', text)

    # Pattern 6: Section references with just numbers like "(§8,)" or "(§16)"
    text = re.sub(r'\(§\d+[,)]\)', '', text)

    return text


def process_file(path: Path) -> bool:
    """Process one file. Returns True if modified."""
    try:
        original = path.read_text()
        modified = remove_doc_refs(original)

        if modified != original:
            path.write_text(modified)
            return True
        return False
    except Exception as e:
        print(f"Error processing {path}: {e}", file=sys.stderr)
        return False


def main():
    """Process all Python files in backend, data_platform, ml, ui."""
    root = Path(__file__).parent.parent

    targets = [
        root / "backend",
        root / "data_platform",
        root / "ml",
        root / "ui",
    ]

    modified_count = 0
    for target in targets:
        if not target.exists():
            continue

        for pyfile in target.rglob("*.py"):
            # Skip virtual envs, caches
            if any(p in pyfile.parts for p in [".venv", "__pycache__", ".pytest_cache"]):
                continue

            if process_file(pyfile):
                print(f"Updated: {pyfile.relative_to(root)}")
                modified_count += 1

    print(f"\nProcessed {modified_count} files")


if __name__ == "__main__":
    main()
