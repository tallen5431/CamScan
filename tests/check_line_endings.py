"""Fail if a change converts a file's line endings wholesale (CRLF <-> LF).

This repository is mixed — some files are CRLF, most are LF — and has no .gitattributes to
pin them. A scripted edit that reads and writes in text mode silently converts a CRLF file to
LF, and the diff then shows every line changed: twice during one round of work, a few real
edits were buried under 3,700 lines of line-ending churn in calibrationOverlay.js and
circle_detection.py, and only an eye on the diff stat caught it.

    python tests/check_line_endings.py <base-ref>

compares every file changed since <base-ref> with its version there. A file whose dominant
line ending flipped fails the check; new, deleted and binary files are skipped. Converting a
file on purpose is fine — do it in its own commit and say so — but it should never ride along
unnoticed with an edit.
"""
import subprocess
import sys


def _style(data: bytes):
    """'crlf', 'lf', or None when the file has no line breaks to judge."""
    crlf = data.count(b"\r\n")
    lf = data.count(b"\n") - crlf
    if crlf == 0 and lf == 0:
        return None
    return "crlf" if crlf >= lf else "lf"


def _git(*args) -> bytes:
    return subprocess.run(["git", *args], check=True, capture_output=True).stdout


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    base = argv[1]
    try:
        _git("rev-parse", "--verify", f"{base}^{{commit}}")
    except subprocess.CalledProcessError:
        print(f"line endings: base {base!r} not found — nothing to compare against, skipping")
        return 0
    changed = _git("diff", "--name-only", "--diff-filter=M", f"{base}...HEAD").decode().split()
    flipped = []
    for path in changed:
        try:
            old = _git("show", f"{base}:{path}")
            new = _git("show", f"HEAD:{path}")
        except subprocess.CalledProcessError:
            continue
        if b"\0" in old[:8192] or b"\0" in new[:8192]:
            continue                                     # binary
        a, b = _style(old), _style(new)
        if a and b and a != b:
            flipped.append((path, a, b))
    if flipped:
        print("Line endings changed wholesale — almost always an accident of a text-mode write:")
        for path, a, b in flipped:
            print(f"  {path}: {a} -> {b}")
        print("Restore the original endings, or convert in a commit of its own if it is intended.")
        return 1
    print(f"line endings: {len(changed)} changed file(s), none converted")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
