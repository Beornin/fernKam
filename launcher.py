"""
fernKam Launcher - Starts the backend server and shows it in a native window.

Single process: FastAPI (backend/.venv, launched via `fernkam serve`) serves
both the API and the built static frontend (frontend/build) on one port.
This launcher just supervises that process and hosts it in a pywebview
window, so closing the window (X button) reliably tears everything down —
no separate frontend dev server, no leftover browser tab.

The window opens immediately with a splash screen (backend startup can take
up to a minute — migrations, DB connectivity, GPU face-model load) and swaps
to the real app once the backend's health check passes.
"""
import collections
import json
import os
import subprocess
from subprocess import TimeoutExpired
import sys
import threading
import time
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError

# This process's own stdout/stderr may be a plain Windows console (cp1252),
# which raises UnicodeEncodeError on the ✓/⚠/✗ symbols below — happens
# regardless of the PYTHONIOENCODING we set for the backend subprocess,
# since that only affects the child's encoding, not ours.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


class _Tee:
    """Mirror writes to multiple streams — used to send every print() both to
    the console (when one exists) and to a log file, since the packaged exe
    is windowed (console=False) and otherwise gives no way to see what a
    stuck/crashed startup was actually doing."""
    def __init__(self, *streams):
        self.streams = [s for s in streams if s is not None]

    def write(self, data):
        for s in self.streams:
            try:
                s.write(data)
            except Exception:
                pass

    def flush(self):
        for s in self.streams:
            try:
                s.flush()
            except Exception:
                pass


def _resolve_project_root() -> Path:
    """Where backend/ (and frontend/build/) actually live.

    Frozen builds don't infer this from the exe's own location — the exe is
    meant to be moved/pinned anywhere — they read it from a JSON file that
    launcher.spec bakes in at build time with this machine's repo path (see
    launcher.spec). Falls back to sitting next to the exe if that's missing,
    e.g. someone ran PyInstaller by hand outside the documented spec flow.
    """
    if not getattr(sys, "frozen", False):
        return Path(__file__).parent
    meipass = Path(getattr(sys, "_MEIPASS", ""))
    try:
        import json
        config = json.loads((meipass / "_launcher_build_config.json").read_text(encoding="utf-8"))
        return Path(config["repo_root"])
    except Exception as e:
        print(f"[launcher] Warning: couldn't read baked repo path ({e}); "
              f"falling back to the exe's own folder.", flush=True)
        return Path(sys.executable).parent


PROJECT_ROOT = _resolve_project_root()
BACKEND_DIR = PROJECT_ROOT / "backend"
ICON_PATH = PROJECT_ROOT / "assets" / "fernkam.ico"
LOG_PATH = PROJECT_ROOT / "logs" / "launcher.log"
BACKEND_URL = "http://localhost:8000"
BACKEND_HEALTH_URL = "http://127.0.0.1:8000/api/health"  # Use 127.0.0.1 for Windows compatibility


def _setup_log_file() -> None:
    """Best-effort: tee stdout/stderr into logs/launcher.log so a windowed
    (console-less) build still leaves a trail when something goes wrong —
    the splash screen shows the live tail, but the full history survives
    after the window's closed too."""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        log_fh = open(LOG_PATH, "a", encoding="utf-8", errors="replace")
        log_fh.write(f"\n{'=' * 60}\n[launcher] Session started {time.strftime('%Y-%m-%d %H:%M:%S')}\n{'=' * 60}\n")
        log_fh.flush()
        sys.stdout = _Tee(sys.stdout, log_fh)
        sys.stderr = _Tee(sys.stderr, log_fh)
    except Exception:
        pass


_setup_log_file()

# Backend stdout lines (see backend/src/fernkam/api/app.py's lifespan) mapped
# to human-friendly splash-screen status text, checked in the order a fresh
# startup actually prints them.
_STATUS_MARKERS = [
    ("Starting granian", "Starting server…"),
    ("transactional DDL", "Preparing database…"),
    ("Database migrations up to date", "Database ready"),
    ("Checking database connectivity", "Connecting to database…"),
    ("Database connection successful", "Connected to database"),
    ("pgvector", "Configuring vector search…"),
    ("index_setup", "Optimizing indexes…"),
    ("Pre-warming InsightFace", "Loading face recognition model…"),
    ("Pre-warm complete", "Face recognition ready"),
    ("Started worker", "Starting API…"),
]

SPLASH_HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
  html, body {
    margin: 0; height: 100%;
    background: #14161a;
    color: #e6e6e6;
    font-family: -apple-system, "Segoe UI", Arial, sans-serif;
    display: flex; align-items: center; justify-content: center;
  }
  .card { text-align: center; max-width: 640px; padding: 0 24px; }
  .logo { font-size: 28px; font-weight: 600; letter-spacing: 0.5px; margin-bottom: 24px; }
  .spinner {
    width: 32px; height: 32px; margin: 0 auto 18px;
    border: 3px solid #33363d; border-top-color: #6ea8fe;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  .spinner.error { animation: none; border-color: #7f1d1d; border-top-color: #f87171; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .status { font-size: 14px; color: #9aa0a6; min-height: 20px; }
  .status.error { color: #f87171; font-weight: 600; }
  .detail {
    font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
    font-size: 11px; color: #5b6270; margin-top: 10px; min-height: 14px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 600px;
  }
  .errbox {
    display: none; text-align: left; margin-top: 18px; padding: 12px 14px;
    background: #1c1012; border: 1px solid #7f1d1d; border-radius: 8px;
    font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
    font-size: 11px; color: #d6a0a0; white-space: pre-wrap; max-height: 260px;
    overflow-y: auto; line-height: 1.5;
  }
  .hint { font-size: 11px; color: #6b7280; margin-top: 10px; }
</style>
</head>
<body>
  <div class="card">
    <div class="logo">fernKam</div>
    <div class="spinner" id="spinner"></div>
    <div class="status" id="status">Starting…</div>
    <div class="detail" id="detail"></div>
    <div class="errbox" id="errbox"></div>
    <div class="hint" id="hint"></div>
  </div>
  <script>
    window.fernkamSetStatus = function(text) {
      var el = document.getElementById('status');
      if (el) el.textContent = text;
    };
    window.fernkamSetDetail = function(text) {
      var el = document.getElementById('detail');
      if (el) el.textContent = text;
    };
    window.fernkamShowError = function(statusText, bodyText, hintText) {
      var spinner = document.getElementById('spinner');
      var status = document.getElementById('status');
      var detail = document.getElementById('detail');
      var errbox = document.getElementById('errbox');
      var hint = document.getElementById('hint');
      if (spinner) spinner.className = 'spinner error';
      if (status) { status.textContent = statusText; status.className = 'status error'; }
      if (detail) detail.textContent = '';
      if (errbox) { errbox.textContent = bodyText || ''; errbox.style.display = bodyText ? 'block' : 'none'; }
      if (hint) hint.textContent = hintText || '';
    };
  </script>
</body>
</html>"""


def _status_for_line(line: str):
    for marker, text in _STATUS_MARKERS:
        if marker in line:
            return text
    return None


def _backend_exe() -> Path:
    """Path to the backend's own venv console script (avoids `uv run` overhead)."""
    exe_name = "fernkam.exe" if sys.platform == "win32" else "fernkam"
    return BACKEND_DIR / ".venv" / ("Scripts" if sys.platform == "win32" else "bin") / exe_name


def _kill_tree(proc) -> None:
    """Kill a process and all its children reliably.

    On Windows, proc.terminate() only kills the immediate process, leaving
    granian workers (and anything they spawned, e.g. an in-flight ffmpeg
    call) orphaned. taskkill /F /T kills the entire process tree.
    """
    if proc is None or proc.poll() is not None:
        return
    pid = proc.pid
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
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
        if backend_proc.poll() is not None:
            print(f"[launcher] ✗ Backend process exited with code {backend_proc.returncode}", flush=True)
            return False
        try:
            urlopen(BACKEND_HEALTH_URL, timeout=1)
            print("[launcher] ✓ Backend ready", flush=True)
            return True
        except Exception:
            pass
        if i % 10 == 0 and i > 0:
            print(f"[launcher] Still waiting... ({i}s elapsed)", flush=True)
        time.sleep(1)

    if backend_proc.poll() is None:
        print("[launcher] ⚠ Backend timeout but process running (assuming ready)", flush=True)
        return True
    print("[launcher] ✗ Backend did not start in time (check logs above for errors)", flush=True)
    return False


def stream_output(proc, prefix, on_line=None):
    """Read process stdout line by line, print with prefix, and notify a listener."""
    for line in iter(proc.stdout.readline, ""):
        print(f"[{prefix}] {line}", end="", flush=True)
        if on_line is not None:
            try:
                on_line(line)
            except Exception:
                pass


def start_backend(on_line=None):
    print("[launcher] Starting backend...", flush=True)
    exe = _backend_exe()
    if not exe.exists():
        print(f"[launcher] Error: backend venv not found at {exe}", flush=True)
        print("[launcher]   Run `uv sync` in backend/ first.", flush=True)
        sys.exit(1)

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"  # avoids a cp1252 crash on Windows consoles for our '✓'/'✗' output
    env["NO_COLOR"] = "1"  # disable Rich color output
    env["TERM"] = "dumb"
    # Without this, Windows pops up a fresh console window for this child —
    # the launcher itself is windowed (no console to inherit), so a plain
    # Popen() here would flash a visible cmd window for the backend.
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    proc = subprocess.Popen(
        [str(exe), "serve"],
        cwd=BACKEND_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        creationflags=creationflags,
    )
    threading.Thread(target=stream_output, args=(proc, "backend", on_line), daemon=True).start()
    return proc


def _set_status(window, text: str) -> None:
    try:
        window.evaluate_js(f"window.fernkamSetStatus && window.fernkamSetStatus({json.dumps(text)})")
    except Exception:
        pass


def _set_detail(window, text: str) -> None:
    try:
        window.evaluate_js(f"window.fernkamSetDetail && window.fernkamSetDetail({json.dumps(text)})")
    except Exception:
        pass


def _show_error(window, status_text: str, body_text: str, hint_text: str) -> None:
    try:
        window.evaluate_js(
            "window.fernkamShowError && window.fernkamShowError("
            f"{json.dumps(status_text)}, {json.dumps(body_text)}, {json.dumps(hint_text)})"
        )
    except Exception:
        pass


def _build_error_html(status_text: str, body_text: str, hint_text: str) -> str:
    """Same splash markup, pre-rendered in its error state — used when the
    backend dies *after* the real app already loaded, so load_html() shows
    something coherent instead of racing a fresh evaluate_js against a page
    that just navigated."""
    import html as _html
    return (
        SPLASH_HTML
        .replace(
            '<div class="spinner" id="spinner"></div>',
            '<div class="spinner error" id="spinner"></div>',
        )
        .replace(
            '<div class="status" id="status">Starting…</div>',
            f'<div class="status error" id="status">{_html.escape(status_text)}</div>',
        )
        .replace(
            '<div class="errbox" id="errbox"></div>',
            f'<div class="errbox" id="errbox" style="display:block">{_html.escape(body_text)}</div>',
        )
        .replace(
            '<div class="hint" id="hint"></div>',
            f'<div class="hint" id="hint">{_html.escape(hint_text)}</div>',
        )
    )


def _format_backend_failure(backend_proc, buffer) -> tuple[str, str]:
    """Build (status_text, body_text) describing why the backend didn't come up."""
    exit_code = backend_proc.poll()
    if exit_code is not None:
        status = f"Backend process exited (code {exit_code})"
    else:
        status = "Backend did not respond in time"
    tail = "\n".join(buffer) if buffer else "(no output captured)"
    return status, tail


def main():
    print("=" * 60)
    print("fernKam Launcher")
    print("=" * 60)

    if not BACKEND_DIR.exists():
        print(f"Error: Backend dir not found: {BACKEND_DIR}")
        sys.exit(1)

    status_holder = {"status_cb": None, "detail_cb": None}
    output_buffer = collections.deque(maxlen=60)

    def _on_backend_line(line: str) -> None:
        stripped = line.rstrip("\n")
        if stripped:
            output_buffer.append(stripped)
        detail_cb = status_holder["detail_cb"]
        if detail_cb is not None and stripped:
            detail_cb(stripped)
        status_cb = status_holder["status_cb"]
        if status_cb is not None:
            text = _status_for_line(line)
            if text:
                status_cb(text)

    backend_proc = start_backend(on_line=_on_backend_line)
    print(f"[launcher] Logging to {LOG_PATH}", flush=True)

    def shutdown(exit_code: int = 0) -> None:
        print("\n[launcher] Shutting down...", flush=True)
        _kill_tree(backend_proc)
        print("[launcher] Stopped.", flush=True)
        sys.exit(exit_code)

    try:
        import webview
    except ImportError:
        # Dev-console fallback: no pywebview installed, just supervise the
        # backend like before and let the user hit the app in a browser.
        print("[launcher] pywebview not installed — running headless.", flush=True)
        if not wait_for_backend(backend_proc, timeout=60):
            shutdown(1)
        print(f"[launcher] Open {BACKEND_URL} in a browser. Press Ctrl+C to stop.", flush=True)
        try:
            while True:
                if backend_proc.poll() is not None:
                    print(f"[launcher] ✗ Backend process exited with code: {backend_proc.returncode}", flush=True)
                    shutdown(1)
                time.sleep(5)
        except KeyboardInterrupt:
            shutdown(0)
        return

    window = webview.create_window(
        "fernKam", html=SPLASH_HTML, width=1400, height=900, min_size=(900, 600), background_color="#14161a"
    )

    def _on_closing():
        # Runs on the pywebview window's own close (X button) — this is the
        # primary shutdown path once packaged (no console to Ctrl+C).
        _kill_tree(backend_proc)

    window.events.closing += _on_closing

    def _watchdog():
        # Backend dying unexpectedly (after the real app already loaded)
        # shows an error page instead of silently vanishing the window —
        # closing via X still works from here, _on_closing is unaffected.
        while True:
            if backend_proc.poll() is not None:
                print(f"[launcher] ✗ Backend process exited with code: {backend_proc.returncode}", flush=True)
                status, body = _format_backend_failure(backend_proc, output_buffer)
                hint = f"Log: {LOG_PATH}"
                try:
                    window.load_html(_build_error_html(f"Backend stopped — {status}", body, hint))
                except Exception:
                    pass
                return
            time.sleep(2)

    def _boot(window):
        # Runs in a background thread once the GUI loop (and splash screen)
        # is up — swaps the splash for the real app once the backend answers
        # its health check, updating status/detail text from backend log
        # lines (_on_backend_line) in the meantime.
        try:
            status_holder["status_cb"] = lambda text: _set_status(window, text)
            status_holder["detail_cb"] = lambda text: _set_detail(window, text)
            _set_status(window, "Starting backend…")
            if not wait_for_backend(backend_proc, timeout=60):
                status_holder["detail_cb"] = None
                status, body = _format_backend_failure(backend_proc, output_buffer)
                hint = f"Log: {LOG_PATH}"
                _show_error(window, status, body, hint)
                return
            status_holder["status_cb"] = None
            status_holder["detail_cb"] = None
            window.load_url(BACKEND_URL)
            threading.Thread(target=_watchdog, daemon=True).start()
        except Exception as exc:
            # Anything unexpected during boot itself (not the backend) —
            # still surface it instead of leaving the spinner stuck forever.
            print(f"[launcher] ✗ Boot error: {exc}", flush=True)
            import traceback as _tb
            _show_error(window, f"Launcher error: {exc}", _tb.format_exc(), f"Log: {LOG_PATH}")

    try:
        webview.start(_boot, window, icon=str(ICON_PATH) if ICON_PATH.is_file() else None)
    except KeyboardInterrupt:
        pass
    finally:
        _kill_tree(backend_proc)


if __name__ == "__main__":
    main()
