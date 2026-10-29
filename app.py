import glob
import json
import os
import re
import subprocess
import threading
import uuid
from collections import deque
from datetime import datetime

from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    send_file,
    send_from_directory,
    url_for,
)

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Vive dentro de downloads/ para sobrevivir a la recreación del contenedor.
JOBS_FILE = os.path.join(DOWNLOAD_DIR, ".jobs.json")
LEGACY_JOBS_FILE = os.path.join(BASE_DIR, "jobs.json")


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# Descargas simultáneas. 1 por defecto: la cola va de una en una para no
# saturar CPU (ffmpeg), disco ni red.
MAX_CONCURRENT_DOWNLOADS = max(1, _env_int("MAX_CONCURRENT_DOWNLOADS", 1))

# Protege jobs, queue y active_processes. RLock para poder anidar llamadas
# internas (save_jobs, schedule_queue) sin autobloquearse.
state_lock = threading.RLock()

jobs = {}
queue = deque()          # job_ids esperando turno, FIFO
active_processes = {}    # job_id -> Popen (None mientras arranca)


def queue_position(job_id):
    """Posición 1-based en la cola, o None si no está esperando."""
    try:
        return list(queue).index(job_id) + 1
    except ValueError:
        return None


def load_jobs():
    """Recupera trabajos y cola del disco (compatible con el jobs.json antiguo)."""
    if not os.path.exists(JOBS_FILE) and os.path.exists(LEGACY_JOBS_FILE):
        try:
            os.replace(LEGACY_JOBS_FILE, JOBS_FILE)
        except OSError:
            pass

    if not os.path.exists(JOBS_FILE):
        return

    try:
        with open(JOBS_FILE, "r") as f:
            data = json.load(f)
    except Exception:
        return

    if not isinstance(data, dict):
        return

    saved_queue = data.pop("_queue", [])

    with state_lock:
        for job_id, job in data.items():
            if not isinstance(job, dict):
                continue

            status = job.get("status")

            if status == "downloading":
                # Su proceso desapareció con el reinicio: no se puede reanudar.
                job["status"] = "error"
                job["error"] = "Interrumpida por un reinicio del servidor"
                job["progress"] = 0
            elif status == "done" and job.get("file") and not os.path.exists(job["file"]):
                continue

            if job.get("status") not in ("queued", "done", "error", "cancelled"):
                continue

            jobs[job_id] = job

        # Respeta el orden guardado y añade cualquier pendiente que falte.
        for job_id in saved_queue:
            job = jobs.get(job_id)
            if job and job.get("status") == "queued" and job_id not in queue:
                queue.append(job_id)

        for job_id, job in jobs.items():
            if job.get("status") == "queued" and job_id not in queue:
                queue.append(job_id)


def save_jobs():
    """Persiste trabajos y orden de la cola."""
    with state_lock:
        persist = {}
        for job_id, job in jobs.items():
            persist[job_id] = {
                "status": job.get("status"),
                "url": job.get("url"),
                "title": job.get("title"),
                "filename": job.get("filename"),
                "file": job.get("file"),
                "progress": job.get("progress", 0),
                "total_size": job.get("total_size"),
                "error": job.get("error"),
                "created_at": job.get("created_at"),
                "format": job.get("format"),
                "format_id": job.get("format_id"),
            }

        persist["_queue"] = list(queue)

        try:
            with open(JOBS_FILE, "w") as f:
                json.dump(persist, f)
        except Exception:
            pass


def remove_partial_files(job_id):
    """Borra restos (.part, .ytdl) de una descarga cancelada."""
    patterns = [
        os.path.join(DOWNLOAD_DIR, f"{job_id}.*.part"),
        os.path.join(DOWNLOAD_DIR, f"{job_id}.*.part-*"),
        os.path.join(DOWNLOAD_DIR, f"{job_id}.*.ytdl"),
    ]
    for pattern in patterns:
        for f in glob.glob(pattern):
            try:
                os.remove(f)
            except OSError:
                pass


def stop_process(process):
    """Termina yt-dlp sin bloquear la cola si ignora SIGTERM."""
    if process is None:
        return
    try:
        process.terminate()
    except Exception:
        return
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
            process.wait(timeout=5)
        except Exception:
            pass


def schedule_queue():
    """Arranca pendientes mientras haya hueco libre."""
    with state_lock:
        while queue and len(active_processes) < MAX_CONCURRENT_DOWNLOADS:
            job_id = queue.popleft()
            job = jobs.get(job_id)

            if not job or job.get("status") != "queued":
                continue

            job["status"] = "downloading"
            job["progress"] = 0
            job["error"] = None
            job["started_at"] = datetime.now().isoformat()

            # Reserva la plaza antes de lanzar el hilo.
            active_processes[job_id] = None
            threading.Thread(
                target=run_download, args=(job_id,), daemon=True
            ).start()

        save_jobs()


def run_download(job_id):
    with state_lock:
        job = jobs.get(job_id)
        if not job or job.get("status") != "downloading":
            active_processes.pop(job_id, None)
            job = None

    if job is None:
        schedule_queue()
        return

    url = job.get("url")
    format_choice = job.get("format") or "video"
    format_id = job.get("format_id")
    out_template = os.path.join(DOWNLOAD_DIR, f"{job_id}.%(ext)s")

    cmd = ["yt-dlp", "--no-playlist", "-o", out_template, "--newline"]

    if format_choice == "audio":
        cmd += ["-x", "--audio-format", "mp3"]
    elif format_id:
        cmd += ["-f", f"{format_id}+bestaudio/best", "--merge-output-format", "mp4"]
    else:
        cmd += ["-f", "bestvideo+bestaudio/best", "--merge-output-format", "mp4"]

    cmd.append(url)

    process = None
    last_error = None

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        with state_lock:
            if job.get("status") == "cancelled":
                process.terminate()
            else:
                active_processes[job_id] = process

        for line in process.stdout:
            if job.get("status") == "cancelled":
                stop_process(process)
                break

            line = line.strip()
            if not line:
                continue

            if line.startswith("ERROR:"):
                last_error = line.removeprefix("ERROR:").strip()

            # Progreso que imprime yt-dlp
            match = re.search(
                r'\[download\]\s+([\d.]+)%\s+of\s+~?([\d.]+[KMGT]?i?B)',
                line,
            )
            if match:
                with state_lock:
                    job["progress"] = float(match.group(1))
                    job["total_size"] = match.group(2)

            dest_match = re.search(r'\[download\] Destination:\s+(.+)', line)
            if dest_match:
                job["destination"] = dest_match.group(1)

        process.wait()

        if job.get("status") == "cancelled":
            remove_partial_files(job_id)
            save_jobs()
            return

        if process.returncode != 0:
            with state_lock:
                job["status"] = "error"
                job["error"] = last_error or "Download failed"
            save_jobs()
            return

        files = glob.glob(os.path.join(DOWNLOAD_DIR, f"{job_id}.*"))
        files = [
            f
            for f in files
            if not f.endswith(".part") and not f.endswith(".ytdl")
        ]

        if not files:
            with state_lock:
                job["status"] = "error"
                job["error"] = "Download completed but no file was found"
            save_jobs()
            return

        if format_choice == "audio":
            target = [f for f in files if f.endswith(".mp3")]
            chosen = target[0] if target else files[0]
        else:
            target = [f for f in files if f.endswith(".mp4")]
            chosen = target[0] if target else files[0]

        for f in files:
            if f != chosen:
                try:
                    os.remove(f)
                except OSError:
                    pass

        # Renombra con el título saneado
        ext = os.path.splitext(chosen)[1]
        title = (job.get("title") or "").strip()
        if title:
            safe = "".join(c.lower() if c.isalnum() else "_" for c in title)
            safe = re.sub(r"_+", "_", safe).strip("_")
            if safe:
                new_path = os.path.join(DOWNLOAD_DIR, f"{safe}{ext}")
                if new_path != chosen and not os.path.exists(new_path):
                    try:
                        os.rename(chosen, new_path)
                        chosen = new_path
                    except OSError:
                        pass

        with state_lock:
            if job.get("status") == "cancelled":
                remove_partial_files(job_id)
            else:
                job["status"] = "done"
                job["file"] = chosen
                job["filename"] = os.path.basename(chosen)
        save_jobs()

    except Exception as e:
        with state_lock:
            job["status"] = "error"
            job["error"] = str(e)
        save_jobs()

    finally:
        with state_lock:
            active_processes.pop(job_id, None)
        schedule_queue()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/info", methods=["POST"])
def get_info():
    data = request.json or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    cmd = ["yt-dlp", "--no-playlist", "-j", url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            return jsonify({"error": result.stderr.strip().split("\n")[-1]}), 400

        info = json.loads(result.stdout)

        best_by_height = {}
        for f in info.get("formats", []):
            height = f.get("height")
            if height and f.get("vcodec", "none") != "none":
                tbr = f.get("tbr") or 0
                if height not in best_by_height or tbr > (
                    best_by_height[height].get("tbr") or 0
                ):
                    best_by_height[height] = f

        formats = []
        for height, f in best_by_height.items():
            formats.append(
                {
                    "id": f["format_id"],
                    "label": f"{height}p",
                    "height": height,
                }
            )
        formats.sort(key=lambda x: x["height"], reverse=True)

        return jsonify(
            {
                "title": info.get("title", ""),
                "thumbnail": info.get("thumbnail", ""),
                "duration": info.get("duration"),
                "uploader": info.get("uploader", ""),
                "formats": formats,
            }
        )
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timed out fetching video info"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/download", methods=["POST"])
def start_download():
    data = request.json or {}
    url = (data.get("url") or "").strip()
    format_choice = data.get("format", "video")
    format_id = data.get("format_id")
    title = data.get("title", "")

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    job_id = uuid.uuid4().hex[:10]

    with state_lock:
        jobs[job_id] = {
            "status": "queued",
            "url": url,
            "title": title,
            "format": format_choice,
            "format_id": format_id,
            "progress": 0,
            "created_at": datetime.now().isoformat(),
        }
        queue.append(job_id)
        save_jobs()

    schedule_queue()

    with state_lock:
        job = jobs.get(job_id) or {}
        return jsonify(
            {
                "job_id": job_id,
                "status": job.get("status", "queued"),
                "position": queue_position(job_id),
            }
        )


@app.route("/api/status/<job_id>")
def check_status(job_id):
    with state_lock:
        job = jobs.get(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404

        return jsonify(
            {
                "status": job.get("status"),
                "error": job.get("error"),
                "filename": job.get("filename"),
                "progress": job.get("progress", 0),
                "total_size": job.get("total_size"),
                "position": queue_position(job_id),
                "queue_size": len(queue),
            }
        )


@app.route("/api/cancel/<job_id>", methods=["POST"])
def cancel_download(job_id):
    process = None

    with state_lock:
        job = jobs.get(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404

        status = job.get("status")

        if status == "queued":
            # Aún no ha empezado: basta con sacarlo de la cola.
            try:
                queue.remove(job_id)
            except ValueError:
                pass
            job["status"] = "cancelled"
            job["error"] = "Quitado de la cola"
            save_jobs()
            return jsonify({"status": "cancelled"})

        if status != "downloading":
            return jsonify({"status": status})

        job["status"] = "cancelled"
        process = active_processes.get(job_id)

    # Nunca matar el proceso con el lock cogido.
    stop_process(process)
    return jsonify({"status": "cancelled"})


@app.route("/api/jobs")
def list_jobs():
    """Lista todos los trabajos (en cola, activos y terminados)."""
    result = []
    with state_lock:
        for job_id, job in jobs.items():
            result.append(
                {
                    "job_id": job_id,
                    "status": job.get("status"),
                    "title": job.get("title"),
                    "url": job.get("url"),
                    "filename": job.get("filename"),
                    "progress": job.get("progress", 0),
                    "total_size": job.get("total_size"),
                    "error": job.get("error"),
                    "created_at": job.get("created_at"),
                    "position": queue_position(job_id),
                }
            )
    result.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return jsonify(result)


@app.route("/api/jobs/<job_id>", methods=["DELETE"])
def delete_job(job_id):
    with state_lock:
        job = jobs.get(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404
        if job.get("status") == "downloading":
            return jsonify({"error": "Cancel the download before removing it"}), 409

        if job.get("status") == "queued":
            # Quitar de la cola antes de que llegue a descargar.
            try:
                queue.remove(job_id)
            except ValueError:
                pass

        del jobs[job_id]
        save_jobs()

    return "", 204


@app.route("/api/files")
def list_files():
    """Lista los ficheros descargados, ocultando los internos."""
    files = []
    try:
        for f in os.listdir(DOWNLOAD_DIR):
            if f.startswith("."):
                continue
            filepath = os.path.join(DOWNLOAD_DIR, f)
            if os.path.isfile(filepath) and not f.endswith(".part") and not f.endswith(".ytdl"):
                stat = os.stat(filepath)
                files.append(
                    {
                        "name": f,
                        "size": stat.st_size,
                        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        "download_url": url_for("download_named_file", filename=f),
                    }
                )
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    files.sort(key=lambda x: x["modified"], reverse=True)
    return jsonify(files)


@app.route("/api/files/<path:filename>")
def download_named_file(filename):
    if filename.startswith("."):
        return jsonify({"error": "Not found"}), 404
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)


@app.route("/api/file/<job_id>")
def download_file(job_id):
    with state_lock:
        job = jobs.get(job_id)
        if not job or job.get("status") != "done" or not job.get("file"):
            return jsonify({"error": "File not ready"}), 404
        path = job["file"]
        name = job.get("filename")

    return send_file(path, as_attachment=True, download_name=name)


# Recupera estado y reanuda la cola pendiente
load_jobs()
schedule_queue()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8899))
    host = os.environ.get("HOST", "127.0.0.1")
    app.run(host=host, port=port)
