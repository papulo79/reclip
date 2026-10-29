"""Infraestructura compartida por los tests de ReClip.

Cada test trabaja sobre una copia de la app en un directorio temporal, con un
`yt-dlp` falso en el PATH: nada toca descargas reales ni la red.
"""

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent

DEFAULT_VIDEO = {
    "title": "Vídeo de prueba",
    "thumbnail": "",
    "duration": 10,
    "uploader": "tester",
    "formats": [
        {"format_id": "137", "height": 1080, "vcodec": "avc1", "tbr": 4000},
        {"format_id": "136", "height": 720, "vcodec": "avc1", "tbr": 2000},
    ],
}

DEFAULT_PLAYLIST = {
    "_type": "playlist",
    "title": "Lista de prueba",
    "entries": [
        {"url": "https://example.com/video/1", "title": "Uno", "duration": 10},
        {"url": "https://example.com/video/2", "title": "Dos", "duration": 20},
        {"url": "https://example.com/video/3", "title": "Tres", "duration": 30},
    ],
}

FAKE_YTDLP = r'''#!/usr/bin/env python3
import json, os, sys, time

args = sys.argv[1:]
delay = float(os.environ.get("FAKE_YTDLP_DELAY", "0.3"))
args_file = os.environ.get("FAKE_YTDLP_ARGS")
if args_file:
    with open(args_file, "a") as f:
        f.write(json.dumps(args) + "\n")

url = args[-1]

if "-j" in args or "-J" in args:
    if "playlist" in url:
        print(json.dumps(json.loads(os.environ.get("FAKE_PLAYLIST_JSON", "{}"))))
    else:
        print(json.dumps(json.loads(os.environ.get("FAKE_VIDEO_JSON", "{}"))))
    sys.exit(0)

if "fail" in url:
    print("ERROR: [test] fallo simulado", flush=True)
    sys.exit(1)

out = None
for i, a in enumerate(args):
    if a == "-o":
        out = args[i + 1]
if not out:
    print("ERROR: fake yt-dlp necesita -o", file=sys.stderr)
    sys.exit(2)

ext = "mp3" if "-x" in args else "mp4"
path = out.replace("%(ext)s", ext)
part = path + ".part"
with open(part, "wb") as f:
    f.write(b"x" * 1024)

for pct in (10, 50, 100):
    print(f"[download] {pct:5.1f}% of ~ 10.00MiB at 1.00MiB/s ETA 00:01", flush=True)
    time.sleep(delay)

os.replace(part, path)
print(f"[download] Destination: {path}", flush=True)
'''


class AppHarness:
    """Copia la app a un temporal, la importa y expone un test client."""

    def __init__(self, env=None, delay="0.2"):
        self.env = dict(env or {})
        self.delay = delay
        self.tmp = Path(tempfile.mkdtemp(prefix="reclip-test-"))
        self.app_dir = self.tmp / "app"
        self.app_dir.mkdir()
        shutil.copy(REPO_DIR / "app.py", self.app_dir / "app.py")
        for name in ("templates", "static"):
            shutil.copytree(REPO_DIR / name, self.app_dir / name)
        (self.app_dir / "downloads").mkdir()

        self.bin_dir = self.tmp / "bin"
        self.bin_dir.mkdir()
        fake = self.bin_dir / "yt-dlp"
        fake.write_text(FAKE_YTDLP)
        fake.chmod(0o755)

        self.args_file = self.tmp / "yt-dlp-args.jsonl"
        self._old_env = {}
        self.module = None

    def start(self):
        values = {
            "PATH": f"{self.bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "FAKE_YTDLP_DELAY": self.delay,
            "FAKE_VIDEO_JSON": json.dumps(DEFAULT_VIDEO),
            "FAKE_PLAYLIST_JSON": json.dumps(DEFAULT_PLAYLIST),
            "FAKE_YTDLP_ARGS": str(self.args_file),
        }
        values.update(self.env)

        for key, value in values.items():
            self._old_env[key] = os.environ.get(key)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

        name = f"reclip_app_{uuid.uuid4().hex}"
        spec = importlib.util.spec_from_file_location(name, self.app_dir / "app.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        module.app.config["TESTING"] = True
        self.module = module
        return module

    @property
    def client(self):
        return self.module.app.test_client()

    @property
    def downloads(self):
        return self.app_dir / "downloads"

    def ytdlp_calls(self):
        if not self.args_file.exists():
            return []
        return [
            json.loads(line)
            for line in self.args_file.read_text().splitlines()
            if line.strip()
        ]

    def stop(self):
        for key, old in self._old_env.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
        self._old_env = {}
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.module = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc_info):
        self.stop()


def basic_auth(user, password):
    import base64

    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def post_download(client, url, title, fmt="video"):
    response = client.post(
        "/api/download", json={"url": url, "format": fmt, "title": title}
    )
    assert response.status_code == 200, response.data
    return response.get_json()


def job_status(client, job_id):
    response = client.get(f"/api/status/{job_id}")
    assert response.status_code in (200, 404), response.status_code
    return response.get_json()


def wait_for_status(client, job_id, wanted, timeout=15):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = job_status(client, job_id)
        if last and last.get("status") == wanted:
            return last
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} no llegó a {wanted}; último: {last}")


def wait_until_idle(client, timeout=20):
    deadline = time.time() + timeout
    jobs = []
    while time.time() < deadline:
        jobs = client.get("/api/jobs").get_json()
        if all(j["status"] in ("done", "error", "cancelled") for j in jobs):
            return jobs
        time.sleep(0.05)
    raise AssertionError(f"la cola no terminó: {jobs}")


def visible_files(downloads):
    return sorted(p.name for p in downloads.iterdir() if not p.name.startswith("."))
