"""Set up and launch both services: python3 setup_backend.py [--install-only]."""
from __future__ import annotations
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import venv

ROOT = Path(__file__).resolve().parent


def environment(folder):
    path = ROOT / folder / ".venv"
    python = path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        print(f"Creating {folder} environment…", flush=True)
        venv.EnvBuilder(with_pip=True).create(path)
    requirements = ROOT / folder / "requirements.txt"
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
    marker = path / ".papertrail-requirements"
    if not marker.exists() or marker.read_text() != digest:
        if folder == "backend":
            subprocess.run([str(python), "-m", "pip", "install", "torch==2.14.1", "--index-url", "https://download.pytorch.org/whl/cpu"], check=True)
        subprocess.run([str(python), "-m", "pip", "install", "-r", str(requirements)], check=True)
        marker.write_text(digest)
    return python


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-only", action="store_true")
    arguments = parser.parse_args()
    if not (ROOT / ".env").exists():
        shutil.copyfile(ROOT / ".env.example", ROOT / ".env")
        print("Created .env. Add your CHAT_API_KEY before asking questions.", flush=True)
    backend, frontend = environment("backend"), environment("frontend")
    if arguments.install_only:
        return
    processes = []
    def stop(*_):
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
    signal.signal(signal.SIGTERM, lambda *_: (stop(), sys.exit(0)))
    try:
        processes.append(subprocess.Popen([str(backend), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000", "--workers", "1"], cwd=ROOT / "backend"))
        print("Preparing the document service. The first launch may download the embedding model…", flush=True)
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            if processes[0].poll() is not None:
                raise RuntimeError("Backend exited. Check the startup message above.")
            try:
                with urllib.request.urlopen("http://localhost:8000/health", timeout=2) as response:
                    if response.status == 200:
                        break
            except (urllib.error.URLError, TimeoutError):
                time.sleep(1)
        else:
            raise RuntimeError("Backend startup timed out. Check the model download and try again.")
        env = os.environ.copy()
        env["BACKEND_URL"] = "http://localhost:8000"
        processes.append(subprocess.Popen([str(frontend), "-m", "streamlit", "run", "app.py", "--server.address", "127.0.0.1", "--server.port", "8501"], cwd=ROOT / "frontend", env=env))
        print("Open http://localhost:8501 — press Ctrl+C to stop both services.", flush=True)
        while all(process.poll() is None for process in processes):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        stop()


if __name__ == "__main__":
    main()
