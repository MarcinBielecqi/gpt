#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import core
from skills.shared.ai_protocol import add_standard_args, run_skill

SKILL = "osm_hotspot_grid"
SCHEMA_SQL = (Path(__file__).resolve().parents[1] / "schema.sql").read_text(encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=core.DESCRIPTION)
    add_standard_args(parser)
    core.add_domain_args(parser)
    return parser


if __name__ == "__main__":
    raise SystemExit(run_skill(skill=SKILL, build_parser=build_parser, run=core.run, schema_sql=SCHEMA_SQL))
