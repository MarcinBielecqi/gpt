from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .db import ensure_bus_db, ensure_canon_db, ensure_local_db
from .profiles import get_profile


@dataclass(frozen=True)
class SkillContext:
    skill: str
    run_id: str
    workspace: Path
    canon_db: Path
    bus_db: Path
    skill_dir: Path
    local_db: Path
    profile_name: str
    profile: dict


def build_context(*, skill: str, run_id: str, workspace: str, canon_db: str, profile: str) -> SkillContext:
    workspace_path = Path(workspace)
    canon_path = Path(canon_db)
    bus_path = canon_path.parent / "project_bus.sqlite"
    skill_dir = workspace_path / "skills" / skill
    return SkillContext(
        skill=skill,
        run_id=run_id,
        workspace=workspace_path,
        canon_db=canon_path,
        bus_db=bus_path,
        skill_dir=skill_dir,
        local_db=skill_dir / "run.sqlite",
        profile_name=profile,
        profile=get_profile(profile),
    )


def ensure_context(ctx: SkillContext, schema_sql: str | None = None) -> None:
    ctx.skill_dir.mkdir(parents=True, exist_ok=True)
    ensure_canon_db(ctx.canon_db)
    ensure_bus_db(ctx.bus_db)
    ensure_local_db(ctx.local_db, schema_sql=schema_sql)
