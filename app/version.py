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


def _from_git_files():
    """Leer .git sin el binario git (imágenes slim montan .git:ro)."""
    import zlib

    gitdir = os.path.join(_ROOT, ".git")
    try:
        with open(os.path.join(gitdir, "HEAD")) as fh:
            head = fh.read().strip()
        sha = None
        if head.startswith("ref: "):
            ref = head[5:]
            ref_path = os.path.join(gitdir, ref)
            if os.path.isfile(ref_path):
                with open(ref_path) as fh:
                    sha = fh.read().strip()
            else:
                with open(os.path.join(gitdir, "packed-refs")) as fh:
                    for line in fh:
                        if line.strip().endswith(" " + ref):
                            sha = line.split()[0]
                            break
        else:
            sha = head
        if not sha or len(sha) != 40:
            return None, None
        obj_path = os.path.join(gitdir, "objects", sha[:2], sha[2:])
        with open(obj_path, "rb") as fh:
            raw = zlib.decompress(fh.read()).decode(errors="replace")
        for line in raw.splitlines():
            if line.startswith("committer "):
                ts = int(line.rsplit(" ", 2)[1])
                date = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y.%m.%d")
                return sha[:7], date
    except Exception:
        pass
    return None, None


@lru_cache(maxsize=1)
def get_build_info():
    sha, date = _from_build_info()
    if not sha:
        sha, date = _from_git()
    if not sha:
        sha, date = _from_git_files()
    version = (date or datetime.now(timezone.utc).strftime("%Y.%m.%d")).replace("-", ".")
    return {"version": version, "git_sha": sha or "dev"}
