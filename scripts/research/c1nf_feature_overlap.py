#!/usr/bin/env python3
"""c1nf_feature_overlap: names-only list of the C1-NF model's 107 features (tools.c1nf_features.FEATURE_NAMES) against r4-b's 63 (models/r4b/features.txt, a names file; no model, tool or
data of r4-b is opened). QP-RULING-1116-c1nf-forward-sim 1.5: the PR states whether r4-b's 63 are a subset. Writes ARTIFACTS/c1nf_model/feature_overlap_r4b.txt (--write) or prints it.
Usage: c1nf_feature_overlap.py [--write]"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
OUT = REPO / "ARTIFACTS" / "c1nf_model" / "feature_overlap_r4b.txt"
R4B_NAMES = REPO / "models" / "r4b" / "features.txt"


def render(c1nf_names=None, r4b_names=None):
    if c1nf_names is None:
        from tools.c1nf_features import FEATURE_NAMES
        c1nf_names = list(FEATURE_NAMES)
    if r4b_names is None:
        r4b_names = [ln.strip() for ln in R4B_NAMES.read_text().splitlines() if ln.strip()]
    a, b = set(c1nf_names), set(r4b_names)
    both = [n for n in c1nf_names if n in b]
    only_r = [n for n in r4b_names if n not in a]
    lines = ["# C1-NF model features vs r4-b features: names only (scripts/research/c1nf_feature_overlap.py)",
             "c1nf_features: %d" % len(c1nf_names), "r4b_features: %d" % len(r4b_names), "in_both: %d" % len(both), "r4b_subset_of_c1nf: %s" % ("yes" if not only_r else "no"),
             "", "## in both"] + both + ["", "## r4-b only"] + only_r + ["", "## C1-NF only"] + [n for n in c1nf_names if n not in b]
    return "\n".join(lines) + "\n"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    text = render()
    if "--write" in argv:
        OUT.write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
