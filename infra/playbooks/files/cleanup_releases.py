"""Remove explicitly selected old Mutating releases, under the activation lock."""

import argparse
import fcntl
import json
import os
import re
import shutil
import time
from pathlib import Path


def cleanup(root, names, *, apply=False, keep=3, minimum_age_hours=24):
    if keep < 3 or minimum_age_hours < 1:
        raise ValueError("Keep at least three releases and one hour of history")
    releases = (root / "releases").resolve(strict=True)
    with (root / "deploy.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = (root / "current").resolve(strict=True)
        if current.parent != releases:
            raise ValueError("Current release is outside the release directory")
        directories = [
            path for path in releases.iterdir() if path.is_dir() and not path.is_symlink()
        ]
        newest = sorted(directories, key=lambda path: path.stat().st_mtime, reverse=True)[:keep]
        protected = {current, *newest}
        selected = []
        for name in dict.fromkeys(names):
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", name):
                raise ValueError("Invalid release ID")
            path = releases / name
            if path.is_symlink() or path.resolve(strict=True).parent != releases:
                raise ValueError("Release must be a direct directory, not a symlink")
            if path in protected:
                raise ValueError(f"Protected current or recent release: {name}")
            if time.time() - path.stat().st_mtime < minimum_age_hours * 3600:
                raise ValueError(f"Release is too recent: {name}")
            if (
                not (path / "platform/web/dist/index.html").is_file()
                or not (path / "site/public/index.html").is_file()
            ):
                raise ValueError(f"Not a complete Mutating release: {name}")
            pid_file = path / ".activation/pid"
            if pid_file.exists():
                pid = pid_file.read_text().strip()
                if not re.fullmatch(r"[1-9][0-9]*", pid):
                    raise ValueError(f"Invalid activation PID: {name}")
                try:
                    os.kill(int(pid), 0)
                except ProcessLookupError:
                    pass
                else:
                    raise ValueError(f"Activation PID is still present: {name}")
            selected.append(path)
        # Validate the entire selection before deleting any directory.
        before = shutil.disk_usage(root).free
        if apply:
            for path in selected:
                shutil.rmtree(path)
        return {
            "applied": apply,
            "current": str(current),
            "protected": sorted(str(path) for path in protected),
            "selected": [str(path) for path in selected],
            "free_before": before,
            "free_after": shutil.disk_usage(root).free,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/opt/mutating"))
    parser.add_argument("--release", action="append", default=[])
    parser.add_argument("--keep", type=int, default=3)
    parser.add_argument("--minimum-age-hours", type=int, default=24)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            cleanup(
                args.root,
                args.release,
                apply=args.apply,
                keep=args.keep,
                minimum_age_hours=args.minimum_age_hours,
            )
        )
    )


if __name__ == "__main__":
    main()
