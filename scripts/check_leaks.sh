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
#   3. Other words from the journal, from private-terms.txt: film titles,
#      substances, nicknames, misspellings -- the details a test set
#      quotes, which aren't people and so aren't in the name list.
#      Case-insensitive whole words. Built by scripts/gen_private_terms.py.
#
# Passes 2 and 3 are local-only and skipped in CI, which cannot see their
# input: both lists are gitignored, because they are themselves sensitive.
# That is why this is a pre-push gate as well as a CI job — CI proves the
# patterns, only a local run proves the names and terms. The gate is
# scripts/hooks/pre-push; `bash scripts/install_hooks.sh` installs it.
#
# Usage:  bash scripts/check_leaks.sh            every tracked file
#         bash scripts/check_leaks.sh DIR        every file under DIR instead --
#                                                build output such as dist/web-demo/,
#                                                which is published but never tracked
#         bash scripts/check_leaks.sh --rev REV  every file in commit REV, as
#                                                committed -- what the pre-push
#                                                hook checks, whatever is checked out
# Exit:   0 clean, 1 findings, 2 not a git repo (or DIR or REV missing).

set -uo pipefail

TARGET=""
REV=""
if [ "${1:-}" = "--rev" ]; then
    REV="$(git rev-parse --verify --quiet "${2:-}^{commit}")" \
        || { echo "not a commit: ${2:-}"; exit 2; }
elif [ $# -gt 0 ]; then
    [ -d "$1" ] || { echo "not a directory: $1"; exit 2; }
    TARGET="$(cd "$1" && pwd)"
fi

cd "$(git rev-parse --show-toplevel)" || exit 2

SELF="scripts/check_leaks.sh"
NAME_LIST="third-party-names.txt"
# Exempt from the name pass only; both are still searched for secret-shaped
# strings. Each holds text that cannot be reworded to dodge a collision:
#
#   gen_name_list.py -- its docstring has to name the collisions it
#     deliberately leaves in the list (real people whose names are also
#     common words), so it trips the name pass on its own documentation --
#     the same failure the `sk-ant-` pattern below is written to avoid.
#   LICENSE -- the verbatim AGPL-3.0 text (Phase 3 item 1). "Major
#     Component" is one of the license's own defined terms. A canonical
#     license is not ours to edit, so the alternative to exempting it is a
#     check that fails on every run until someone deletes the check.
NAME_EXEMPT='^(scripts/gen_name_list\.py|LICENSE)$'

# `sk-ant-` needs the key body, not just the prefix — .env.example ships
# `# ANTHROPIC_API_KEY=sk-ant-...` as a placeholder, and a check that
# fails on its own documentation gets deleted rather than fixed.
PATTERNS='sk-ant-[A-Za-z0-9_-]{10,}|C:[/\]Users|OneDrive'

# Tracked files only: untracked local working docs are not shipping, and
# including them would bury real findings under noise. Self-excluded —
# this file contains every pattern it searches for.
if [ -n "$REV" ]; then
    mapfile -d '' -t FILES < <(git ls-tree -r -z --name-only "$REV" | grep -zv "^${SELF}\$")
    echo "leak check: ${#FILES[@]} file(s) in ${REV:0:10}"
elif [ -n "$TARGET" ]; then
    mapfile -d '' -t FILES < <(find "$TARGET" -type f -print0)
    echo "leak check: ${#FILES[@]} file(s) under $TARGET"
else
    mapfile -d '' -t FILES < <(git ls-files -z | grep -zv "^${SELF}\$")
    echo "leak check: ${#FILES[@]} tracked file(s)"
fi

FOUND=0

# The name and terms lists go to grep as a file, never as `-f <(...)`:
# xargs runs grep once per batch of files, the first grep drains the
# process substitution's pipe, and every later batch reads an empty
# list and matches nothing -- a pass that checked only its first few
# hundred files and still said "clean".
PATTERN_FILE="$(mktemp)"
trap 'rm -f "$PATTERN_FILE"' EXIT

# search EXEMPT GREP_ARGS...: matching lines, as path:line:text, from
# every file in scope -- with EXEMPT=names, less NAME_EXEMPT. With --rev
# that is git grep over the commit's tree, not the files on disk.
#
# The output is what decides, never the exit status: xargs returns 123
# when *any* of its batches exits non-zero, and a batch with no match is
# exactly that. Branching on the status drops the real findings from the
# other batches and prints "clean" — a leak check that fails open.
search() {
    local exempt="$1"; shift
    if [ -n "$REV" ]; then
        local specs=(":(exclude)$SELF")
        if [ "$exempt" = names ]; then
            specs+=(":(exclude)scripts/gen_name_list.py" ":(exclude)LICENSE")
        fi
        git grep "$@" "$REV" -- "${specs[@]}" | sed "s|^$REV:||"
    else
        local files=("${FILES[@]}")
        if [ "$exempt" = names ]; then
            mapfile -d '' -t files < <(printf '%s\0' "${FILES[@]}" \
                                       | grep -zvE "$NAME_EXEMPT")
        fi
        printf '%s\0' "${files[@]}" | xargs -0 grep "$@"
    fi
}

hits=$(search none -IinE -e "$PATTERNS")
if [ -n "$hits" ]; then
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
fi

# A list with no capitalized entries would hand `grep -f` a lone blank
# line, which matches every line of every file — the pass has to report
# that it has no names, not report the whole codebase as a leak.
if [ -z "${names:-}" ]; then
    if [ -f "$NAME_LIST" ]; then
        echo "name pass: skipped ($NAME_LIST has no capitalized entries)"
    fi
else
    printf '%s\n' "$names" > "$PATTERN_FILE"
    hits=$(search names -Inw -f "$PATTERN_FILE")
    if [ -n "$hits" ]; then
        echo
        echo "LEAKS FOUND — real names:"
        printf '%s\n' "$hits" | sed 's/^/  /'
        FOUND=1
    fi
    echo "name pass: $count capitalized name(s) checked"
fi

# --- terms pass (local only) ------------------------------------------------
#
# Words, not names: matched case-insensitively as whole words, since a
# detail copied into a comment or a test is as likely lowercase as not.

TERM_LIST="private-terms.txt"
if [ ! -f "$TERM_LIST" ]; then
    echo "terms pass: skipped (no $TERM_LIST — expected in CI, not locally)"
else
    terms=$(grep -vE '^[[:space:]]*(#|$)' "$TERM_LIST")
    if [ -z "$terms" ]; then
        echo "terms pass: skipped ($TERM_LIST has no terms)"
    else
        printf '%s\n' "$terms" > "$PATTERN_FILE"
        hits=$(search names -Iinw -f "$PATTERN_FILE")
        if [ -n "$hits" ]; then
            echo
            echo "LEAKS FOUND — journal terms:"
            printf '%s\n' "$hits" | sed 's/^/  /'
            FOUND=1
        fi
        echo "terms pass: $(printf '%s\n' "$terms" | grep -c .) term(s) checked"
    fi
fi

if [ "$FOUND" -eq 1 ]; then
    echo
    echo "do not push."
    exit 1
fi

echo "clean"
