# SPDX-License-Identifier: AGPL-3.0-or-later
"""Portable content manifests for the captured demo transition."""
import hashlib
import json
from pathlib import Path

TREES = ("categories", "entity_graph", "summaries", "dreams", "patterns")


def content_manifest(root: Path) -> dict[str, str]:
    result = {}
    for tree in TREES:
        for path in sorted((root / tree).rglob("*")):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            if path.suffix == ".json":
                data = json.loads(text)
                if isinstance(data, dict):
                    data.pop("generated", None)
                if path.relative_to(root).as_posix() == "entity_graph/index.json":
                    for record in data.values():
                        record["path"] = record["path"].replace("\\", "/")
                text = json.dumps(data, sort_keys=True, ensure_ascii=False)
            result[path.relative_to(root).as_posix()] = hashlib.sha256(
                text.encode("utf-8")).hexdigest()
    return result
