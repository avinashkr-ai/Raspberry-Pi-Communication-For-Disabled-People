"""Password-protected local dashboard. No browser can retrieve the API key."""
import os
import queue
import secrets
import threading
import time
from pathlib import Path

from flask import Flask, Response, abort, jsonify, redirect, render_template, request, send_file, session, url_for

from .commands import CommandGate
from .device import device_info
from .vision import AssistantError


class CommandQueue:
    def __init__(self, history):
        self.history = history
        self.pending = queue.Queue(maxsize=8)
        self.lock = threading.Lock()

    def submit(self, text, speak=True):
        if not isinstance(text, str) or len(text) > 200:
            raise ValueError("Enter a command of at most 200 characters.")
        text = " ".join(text.lower().split())
        probe = CommandGate()
        if not probe.accept(text, 100):
            text = "hey bot " + text
        if not CommandGate().accept(text, 100):
            raise ValueError("Try click photo, repeat, help, cancel, or hey bot.")
        with self.lock:
            if self.pending.full():
                raise queue.Full
            run_id = self.history.start(text, "web", "queued")
            self.pending.put_nowait((run_id, text, bool(speak)))
            return run_id

    def pop(self):
        try:
            return self.pending.get_nowait()
        except queue.Empty:
            return None


def private_secret(path, length=24):
    path = Path(path)
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_text().strip()
    secret = secrets.token_urlsafe(length)
    with os.fdopen(descriptor, "w") as output:
        output.write(secret + "\n")
    return secret


def create_app(history, commands, password, secret_key, camera=None):
    app = Flask(__name__, template_folder="web", static_folder="web/static")
    app.config.update(SECRET_KEY=secret_key, MAX_CONTENT_LENGTH=4096,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
                      PERMANENT_SESSION_LIFETIME=3600 * 12)
    attempts = {}
    attempt_lock = threading.Lock()

    @app.before_request
    def security():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(24)
        if request.method == "POST":
            token = request.headers.get("X-CSRF-Token") or request.form.get("csrf", "")
            if not secrets.compare_digest(str(token), session["csrf"]):
                abort(403)
        if request.endpoint not in {"login", "static"} and not session.get("authenticated"):
            if request.path.startswith(("/api/", "/photos/")):
                abort(401)
            return redirect(url_for("login"))

    @app.after_request
    def headers(response):
        response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                                 "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin",
                                 "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; form-action 'self'"})
        return response

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        if request.method == "POST":
            address = request.remote_addr
            now = time.monotonic()
            with attempt_lock:
                recent = [t for t in attempts.get(address, []) if now - t < 60]
                if len(recent) >= 5:
                    return render_template("login.html", error="Too many attempts. Wait one minute."), 429
                if len(attempts) > 1000:
                    attempts.clear()
                attempts[address] = recent + [now]
            if secrets.compare_digest(request.form.get("password", "").encode(), password.encode()):
                session.clear()
                session.update(authenticated=True, csrf=secrets.token_urlsafe(24))
                session.permanent = True
                return redirect(url_for("index"))
            error = "Incorrect dashboard password."
        return render_template("login.html", error=error)

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/api/state")
    def state():
        before = request.args.get("before", type=int)
        data = history.snapshot(before)
        data["audio"] = {"input": os.getenv("HEYBOT_INPUT_DEVICE", "auto"),
                         "output": os.getenv("HEYBOT_OUTPUT_DEVICE", "auto")}
        data["camera"] = camera.info() if camera else {"status": "unavailable", "error": "Live preview is unavailable."}
        data["device"] = device_info(history.directory)
        data["queue_depth"] = commands.pending.qsize()
        return jsonify(data)

    @app.get("/api/camera/frame")
    def camera_frame():
        if camera is None:
            return jsonify(error="Live preview is unavailable."), 503
        try:
            image, sequence = camera.get_frame(timeout=2.5)
        except AssistantError as error:
            return jsonify(error=str(error)), 503, {"Retry-After": "1"}
        return Response(image, mimetype="image/jpeg", headers={"X-Frame-Sequence": str(sequence)})

    @app.get("/api/sessions/<uuid:run_id>")
    def detail(run_id):
        item = history.get(str(run_id))
        if item is None:
            abort(404)
        return jsonify(item)

    @app.post("/api/commands")
    def command():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error="Expected a JSON command."), 400
        try:
            run_id = commands.submit(payload.get("command"), payload.get("speak", True))
        except ValueError as error:
            return jsonify(error=str(error)), 400
        except queue.Full:
            return jsonify(error="The command queue is full. Please wait."), 429
        return jsonify(id=run_id), 202

    @app.post("/api/sessions/<uuid:run_id>/delete")
    def delete(run_id):
        try:
            if not history.delete(str(run_id)):
                abort(404)
        except ValueError as error:
            return jsonify(error=str(error)), 409
        return jsonify(deleted=True)

    @app.get("/photos/<uuid:run_id>.jpg")
    def photo(run_id):
        item = history.get(str(run_id))
        if not item or not item["photo"]:
            abort(404)
        path = history.photo_path(run_id)
        if not path.is_file():
            abort(404)
        return send_file(path, mimetype="image/jpeg", conditional=False)

    return app
