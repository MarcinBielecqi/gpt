from __future__ import annotations

PROFILES = {
    "quick": {
        "limit": 50,
        "deadline_s": 30,
        "timeout_s": 15,
        "progress_every": 10,
        "grid_n": 10,
    },
    "normal": {
        "limit": 500,
        "deadline_s": 180,
        "timeout_s": 30,
        "progress_every": 50,
        "grid_n": 20,
    },
    "deep": {
        "limit": 5000,
        "deadline_s": 900,
        "timeout_s": 60,
        "progress_every": 100,
        "grid_n": 40,
    },
}


def get_profile(name: str) -> dict:
    if name not in PROFILES:
        raise ValueError(f"unknown profile: {name}")
    return dict(PROFILES[name])
