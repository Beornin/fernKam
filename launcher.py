"""
fernKam Launcher - Starts both backend and frontend servers.
"""
import os
import queue
import re
import subprocess
from subprocess import TimeoutExpired
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError

PROJECT_ROOT = Path(__file__).parent
BACKEND_DIR = PROJECT_ROOT / "backend"
FRONTEND_DIR = PROJECT_ROOT / "frontend"
BACKEND_URL = "http://localhost:8000"
BACKEND_HEALTH_URL = "http://127.0.0.1:8000/api/health"  # Use 127.0.0.1 for Windows compatibility


def _kill_tree(proc) -> None:
    """Kill a process and all its children reliably.

    On Windows, proc.terminate() only kills the immediate shell (uv/npm),
    leaving granian workers and node/Vite processes orphaned.
    taskkill /F /T kills the entire process tree.
    """
    pid = proc.pid
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        try:
            import signal as _signal
            os.killpg(os.getpgid(pid), _signal.SIGTERM)
        except (ProcessLookupError, OSError):
            proc.terminate()
    try:
        proc.wait(timeout=5)
    except (TimeoutExpired, KeyboardInterrupt):
        proc.kill()


def wait_for_backend(backend_proc, timeout=60):
    """Wait for backend process to be ready."""
    print(f"[launcher] Waiting for backend (timeout: {timeout}s)...", flush=True)
    for i in range(timeout):
        # Check if process is still running
        if backend_proc.poll() is not None:
            print(f"[launcher] ✗ Backend process exited with code {backend_proc.returncode}", flush=True)
            return False
        
        # Try HTTP health check
        try:
            urlopen(BACKEND_HEALTH_URL, timeout=1)
            print("[launcher] ✓ Backend ready", flush=True)
            return True
        except Exception:
            pass
        
        if i % 10 == 0 and i > 0:
            print(f"[launcher] Still waiting... ({i}s elapsed)", flush=True)
        time.sleep(1)
    
    # If we timeout but process is still running, assume it's ready
    if backend_proc.poll() is None:
        print("[launcher] ⚠ Backend timeout but process running (assuming ready)", flush=True)
        return True
    
    print("[launcher] ✗ Backend did not start in time (check logs above for errors)", flush=True)
    return False


def stream_output(proc, prefix):
    """Read process stdout line by line, print with prefix."""
    for line in iter(proc.stdout.readline, ""):
        print(f"[{prefix}] {line}", end="", flush=True)


def start_backend():
    print("[launcher] Starting backend...", flush=True)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["NO_COLOR"] = "1"  # disable Rich color output (avoids Windows cp1252 crash)
    env["TERM"] = "dumb"
    proc = subprocess.Popen(
        ["uv", "run", "fernkam", "serve"],
        cwd=BACKEND_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    threading.Thread(target=stream_output, args=(proc, "backend"), daemon=True).start()
    return proc


def start_frontend():
    print("[launcher] Starting frontend...", flush=True)
    from shutil import which
    npm = which("npm")
    if not npm:
        for candidate in [
            r"C:\Program Files\nodejs\npm.cmd",
            r"C:\Program Files (x86)\nodejs\npm.cmd",
            os.path.expanduser(r"~\AppData\Roaming\npm\npm.cmd"),
        ]:
            if os.path.exists(candidate):
                npm = candidate
                break
    if not npm:
        raise FileNotFoundError("npm not found")

    proc = subprocess.Popen(
        [npm, "run", "dev"],
        cwd=FRONTEND_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=True,
    )
    return proc


def detect_vite_port(proc, timeout=20):
    """Read frontend stdout to find the actual port Vite chose."""
    port_queue = queue.Queue()

    def reader():
        for line in iter(proc.stdout.readline, ""):
            print(f"[frontend] {line}", end="", flush=True)
            m = re.search(r"Local:\s+http://localhost:(\d+)", line)
            if m:
                port_queue.put(int(m.group(1)))

    threading.Thread(target=reader, daemon=True).start()
    try:
        return port_queue.get(timeout=timeout)
    except queue.Empty:
        return 5173  # fallback


def main():
    print("=" * 60)
    print("fernKam Launcher")
    print("=" * 60)

    if not BACKEND_DIR.exists():
        print(f"Error: Backend dir not found: {BACKEND_DIR}")
        sys.exit(1)
    if not FRONTEND_DIR.exists():
        print(f"Error: Frontend dir not found: {FRONTEND_DIR}")
        sys.exit(1)

    backend_proc = start_backend()
    frontend_proc = start_frontend()

    # Detect actual Vite port from its output
    port = detect_vite_port(frontend_proc, timeout=30)
    frontend_url = f"http://localhost:{port}"
    print(f"[launcher] Frontend on {frontend_url}", flush=True)

    # Wait for backend before opening browser (60s timeout for model loading)
    wait_for_backend(backend_proc, timeout=60)

    print(f"[launcher] Opening browser to {frontend_url}...", flush=True)
    webbrowser.open(frontend_url)

    print()
    print("=" * 60)
    print(f"Backend:  {BACKEND_URL}")
    print(f"Frontend: {frontend_url}")
    print("Press Ctrl+C to stop")
    print("=" * 60)

    def shutdown(exit_code: int = 0) -> None:
        print("\n[launcher] Shutting down...", flush=True)
        _kill_tree(frontend_proc)
        _kill_tree(backend_proc)
        print("[launcher] Stopped.", flush=True)
        sys.exit(exit_code)

    try:
        while True:
            # Check if backend died (critical)
            if backend_proc.poll() is not None:
                print("[launcher] ✗ Backend process exited with code:", backend_proc.returncode, flush=True)
                shutdown(1)
            # Check if frontend died (non-critical but report)
            if frontend_proc.poll() is not None:
                print("[launcher] ⚠ Frontend process exited with code:", frontend_proc.returncode, flush=True)
            time.sleep(5)
    except KeyboardInterrupt:
        shutdown(0)


if __name__ == "__main__":
    main()
