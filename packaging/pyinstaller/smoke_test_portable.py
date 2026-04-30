"""Smoke-test a portable PyInstaller Bazaar Tracker build."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test packaged BazaarTracker.exe")
    parser.add_argument(
        "--exe",
        type=Path,
        default=Path("dist") / "BazaarTracker" / "BazaarTracker.exe",
        help="Path to BazaarTracker.exe",
    )
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()

    exe = args.exe.resolve()
    if not exe.is_file():
        print(f"[Smoke] Missing exe: {exe}")
        return 2

    with tempfile.TemporaryDirectory(prefix="bazaar-tracker-smoke-") as tmp:
        tmp_path = Path(tmp)
        env = os.environ.copy()
        env["BAZAAR_TRACKER_DATA_DIR"] = str(tmp_path / "data")
        env["BAZAAR_TRACKER_SETTINGS_DIR"] = str(tmp_path / "settings")
        env["BAZAAR_TRACKER_CACHE_DIR"] = str(tmp_path / "data" / "static_cache")

        commands = [
            [str(exe), "doctor"],
            [str(exe), "setup", "--refresh-content", "never"],
            [str(exe), "setup-status", "--json"],
        ]

        for command in commands:
            print(f"[Smoke] Running: {' '.join(command)}")
            completed = subprocess.run(
                command,
                env=env,
                capture_output=True,
                text=True,
                timeout=args.timeout,
            )
            print(completed.stdout)
            if completed.stderr:
                print(completed.stderr, file=sys.stderr)
            if completed.returncode != 0:
                print(f"[Smoke] Command failed with exit code {completed.returncode}")
                return completed.returncode

        server_cmd = [str(exe), "--no-overlay", "--no-mono"]
        print(f"[Smoke] Starting server: {' '.join(server_cmd)}")
        proc = subprocess.Popen(
            server_cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            deadline = time.time() + args.timeout
            last_error = None
            while time.time() < deadline:
                try:
                    with urllib.request.urlopen("http://127.0.0.1:5555/", timeout=1) as response:
                        if response.status != 200:
                            raise RuntimeError(f"index returned HTTP {response.status}")
                        index_html = response.read(512).decode("utf-8", errors="replace")
                    with urllib.request.urlopen("http://127.0.0.1:5555/overlay", timeout=1) as response:
                        if response.status != 200:
                            raise RuntimeError(f"overlay returned HTTP {response.status}")
                        overlay_html = response.read(512).decode("utf-8", errors="replace")
                    if "Bazaar Tracker" not in index_html:
                        raise RuntimeError("index HTML did not look like Bazaar Tracker")
                    if "<html" not in overlay_html.lower():
                        raise RuntimeError("overlay HTML did not look like HTML")
                    print("[Smoke] Packaged dashboard and overlay HTML loaded.")
                    break
                except Exception as exc:
                    last_error = exc
                    if proc.poll() is not None:
                        output = proc.stdout.read() if proc.stdout else ""
                        print(output)
                        print(f"[Smoke] Server exited early with code {proc.returncode}")
                        return proc.returncode or 1
                    time.sleep(0.5)
            else:
                print(f"[Smoke] Server did not become ready: {last_error}")
                return 1

            request = urllib.request.Request("http://127.0.0.1:5555/api/control/shutdown", method="POST")
            with urllib.request.urlopen(request, timeout=5) as response:
                if response.status != 200:
                    print(f"[Smoke] shutdown returned HTTP {response.status}")
                    return 1
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.terminate()
                proc.wait(timeout=10)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()

        expected = [
            tmp_path / "data" / "bazaar_runs.db",
            tmp_path / "settings" / "settings.json",
        ]
        missing = [path for path in expected if not path.exists()]
        if missing:
            print("[Smoke] Missing expected generated files:")
            for path in missing:
                print(f"  {path}")
            return 1

    print("[Smoke] Portable package smoke test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
