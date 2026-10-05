#!/usr/bin/env bash
#
# Install the repo's git hooks: scripts/hooks/pre-push, the leak check.
#
# Git never installs hooks from a clone, so each checkout runs this once.
# The installed hook only calls the tracked one, so later changes to
# scripts/hooks/ apply without reinstalling.
#
# Usage:  bash scripts/install_hooks.sh

set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
hooks="$(git rev-parse --git-path hooks)"
mkdir -p "$hooks"

for hook in scripts/hooks/*; do
    name="$(basename "$hook")"
    target="$hooks/$name"
    if [ -e "$target" ] && ! grep -q "scripts/hooks/$name" "$target"; then
        echo "$target exists and isn't ours; left alone. Merge it by hand."
        continue
    fi
    printf '#!/usr/bin/env bash\nexec bash "$(git rev-parse --show-toplevel)/scripts/hooks/%s" "$@"\n' \
        "$name" > "$target"
    chmod +x "$target"
    echo "installed $name"
done
