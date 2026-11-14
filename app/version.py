"""Versión de la app (CalVer: YYYY.MM.DD del build/commit + git SHA corto).

Fuentes en orden:
1. ``app/build_info.py`` generado al deploy (builds sin .git, ej. gplace).
2. ``git`` del checkout (prod/QA son repos bind-mounteados).
3. Fallback ``dev``.
"""
import os
import subprocess
from datetime import datetime, timezone
from functools import lru_cache

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _from_build_info():
    try:
        from app.build_info import GIT_SHA, BUILT_AT

        return GIT_SHA, BUILT_AT
    except Exception:
        return None, None


def _from_git():
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_ROOT, capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        date = subprocess.run(
            ["git", "log", "-1", "--format=%cI"],
            cwd=_ROOT, capture_output=True, text=True, timeout=5,
        ).stdout.strip()[:10]
        if sha:
            return sha, (date or None)
    except Exception:
        pass
    return None, None


@lru_cache(maxsize=1)
def get_build_info():
    sha, date = _from_build_info()
    if not sha:
        sha, date = _from_git()
    version = (date or datetime.now(timezone.utc).strftime("%Y.%m.%d")).replace("-", ".")
    return {"version": version, "git_sha": sha or "dev"}
