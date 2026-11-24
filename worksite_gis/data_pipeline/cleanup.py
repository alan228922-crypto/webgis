from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class CleanupPlan:
    remove: list[Path]
    keep: list[Path]


def plan_cleanup(source_root: Path, converted_sources: list[Path]) -> CleanupPlan:
    root = Path(source_root).resolve()
    remove: list[Path] = []
    keep: list[Path] = []
    for source in converted_sources:
        path = Path(source).resolve()
        if root not in path.parents:
            keep.append(path)
            continue
        if path.suffix.lower() == ".shp":
            for sibling in path.parent.glob(path.stem + ".*"):
                remove.append(sibling)
        elif path.name == "帳號密碼.xlsx":
            remove.append(path)
        else:
            keep.append(path)
    return CleanupPlan(remove=sorted(set(remove)), keep=sorted(set(keep)))


def apply_cleanup(plan: CleanupPlan, source_root: Path) -> list[str]:
    root = Path(source_root).resolve()
    removed: list[str] = []
    for path in plan.remove:
        if root not in path.resolve().parents:
            raise ValueError(f"refusing path outside source root: {path}")
        if path.exists() and path.is_file():
            path.unlink()
            removed.append(str(path))
    return removed
