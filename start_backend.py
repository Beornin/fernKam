#!/usr/bin/env python
"""
Start just the backend server (useful for debugging).
Usage: python start_backend.py
"""
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
BACKEND_DIR = PROJECT_ROOT / "backend"

if not BACKEND_DIR.exists():
    print(f"Error: Backend dir not found: {BACKEND_DIR}")
    sys.exit(1)

print("=" * 60)
print("fernKam Backend (standalone)")
print("=" * 60)
print("Starting backend on http://localhost:8000")
print("Press Ctrl+C to stop")
print("=" * 60)
print()

env = os.environ.copy()
env["PYTHONIOENCODING"] = "utf-8"
env["NO_COLOR"] = "1"
env["TERM"] = "dumb"

proc = subprocess.run(
    ["uv", "run", "fernkam", "serve"],
    cwd=BACKEND_DIR,
    env=env,
)
sys.exit(proc.returncode)
