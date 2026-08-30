#!/usr/bin/env bash
#
# Phase 0 item 12 leak grep, over every tracked file.
#
# Two passes, because the two classes of leak need different matching:
#
#   1. Secret-shaped strings — API keys, Windows home paths, OneDrive
#      paths. Case-insensitive; these are structural patterns, not words.
#      This is the pass CI runs.
#   2. Real people's names, from third-party-names.txt. Case-sensitive,
#      capitalized entries only — see the name-pass notes below.
#
# The name pass is local-only and skipped in CI, which cannot see its
# input: the list is gitignored, because the list is itself sensitive.
# That is why this is a pre-push gate as well as a CI job — CI proves the
# patterns, only a local run proves the names.
#
# Usage:  bash scripts/check_leaks.sh
# Exit:   0 clean, 1 findings, 2 not a git repo.

set -uo pipefail

cd "$(git rev-parse --show-toplevel)" || exit 2

SELF="scripts/check_leaks.sh"
NAME_LIST="third-party-names.txt"
# The generator's docstring has to name the collisions it deliberately
# leaves in the list (real people whose names are also common words), so
# it trips the name pass on its own documentation -- the same failure the
# `sk-ant-` pattern below is written to avoid. Exempt from the name pass
# only; it is still searched for secret-shaped strings.
NAME_SELF="scripts/gen_name_list.py"

# `sk-ant-` needs the key body, not just the prefix — .env.example ships
# `# ANTHROPIC_API_KEY=sk-ant-...` as a placeholder, and a check that
# fails on its own documentation gets deleted rather than fixed.
PATTERNS='sk-ant-[A-Za-z0-9_-]{10,}|C:[/\]Users|OneDrive'

# Tracked files only: untracked local working docs are not shipping, and
# including them would bury real findings under noise. Self-excluded —
# this file contains every pattern it searches for.
mapfile -d '' -t FILES < <(git ls-files -z | grep -zv "^${SELF}\$")

echo "leak check: ${#FILES[@]} tracked file(s)"

FOUND=0

if hits=$(printf '%s\0' "${FILES[@]}" | xargs -0 grep -IinE -- "$PATTERNS"); then
    echo
    echo "LEAKS FOUND — secret-shaped strings:"
    printf '%s\n' "$hits" | sed 's/^/  /'
    FOUND=1
fi

# --- name pass (local only) -------------------------------------------------
#
# Capitalized entries only, matched case-sensitively as whole words.
# The list is generated from entity_graph/index.json, which holds every
# entity of every kind — so it also carries lowercase junk like `name`,
# `type`, `place` and `target` that were extracted as entities but are
# ordinary identifiers in this codebase. Matching those produces
# thousands of hits in Python and JS source, and a check that cries wolf
# is one people learn to skim. A real name used as an illustrative
# example is capitalized, so this is both quieter and sufficient.

if [ ! -f "$NAME_LIST" ]; then
    echo "name pass: skipped (no $NAME_LIST — expected in CI, not locally)"
else
    names=$(grep -E '^[A-Z]' "$NAME_LIST" | grep -v '^[[:space:]]*$')
    count=$(printf '%s\n' "$names" | grep -c . )
    mapfile -d '' -t NAME_FILES < <(printf '%s\0' "${FILES[@]}" \
                                    | grep -zv "^${NAME_SELF}$")
    if hits=$(printf '%s\0' "${NAME_FILES[@]}" \
              | xargs -0 grep -Inwf <(printf '%s\n' "$names")); then
        echo
        echo "LEAKS FOUND — real names:"
        printf '%s\n' "$hits" | sed 's/^/  /'
        FOUND=1
    fi
    echo "name pass: $count capitalized name(s) checked"
fi

if [ "$FOUND" -eq 1 ]; then
    echo
    echo "do not push."
    exit 1
fi

echo "clean"
