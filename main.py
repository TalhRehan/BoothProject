"""
Sticker Booth — Multi-image, sequential generation with processing/print flow.

This version adds thorough, practical comments explaining what each block does,
key security considerations, and places to extend the app safely. Functionality
is unchanged from the original.
"""

from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import base64, uuid, time, os, subprocess, shutil, threading
from datetime import datetime
from openai import OpenAI

# load .env if python-dotenv is installed so local dev can use a .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    # It's okay if python-dotenv isn't available in prod images
    pass

# -------------------
# App & Config
# -------------------
app = Flask(__name__)
# Secret key is required for signed cookies (Flask sessions). In production, set APP_SECRET_KEY.
app.secret_key = os.environ.get("APP_SECRET_KEY", "change-it")

# Security & limits — keep request bodies small and cookies hardened
app.config.update(
    MAX_CONTENT_LENGTH=5 * 1024 * 1024,  # limit request bodies to ~5MB to mitigate abuse
    SESSION_COOKIE_HTTPONLY=True,        # block JS from reading the session cookie
    SESSION_COOKIE_SAMESITE="Lax",       # reduce CSRF risk for cross-site requests
    SESSION_COOKIE_SECURE=(os.environ.get("SESSION_COOKIE_SECURE", "0") == "1"),  # set to 1 behind HTTPS
)

# In-memory store scoped by session id; nothing is written to disk for PII minimization
STORE: dict[str, dict] = {}

# Idle session TTL (seconds). Default 10 minutes.
SESSION_TTL_SECONDS = int(os.environ.get("SESSION_TTL_SECONDS", "600"))

def purge_expired() -> None:
    """Remove idle sessions to free memory and avoid unbounded growth.

    NOTE: This runs opportunistically on each request via a before_request hook.
    """
    now = time.time()
    for sid, s in list(STORE.items()):
        ts = s.get("ts", now)
        if now - ts > SESSION_TTL_SECONDS:
            STORE.pop(sid, None)

@app.before_request
def _touch_and_purge() -> None:
    """Refresh last-activity timestamp and purge idle sessions on every request."""
    purge_expired()
    sid = session.get("sid")
    if sid in STORE:
        STORE[sid]["ts"] = time.time()

# -------------------
# OpenAI Client (REQUIRED)
# -------------------
# The app requires an API key at startup so we fail fast rather than at first request.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is required. Set it in the environment before starting the app.")
client = OpenAI(api_key=OPENAI_API_KEY)

# -------------------
# Style prompts / labels
# -------------------
# These serve as the base instructions per selected style. Kept deliberately concise.
STYLE_PROMPTS: dict[str, str] = {
    "realistic_cutout": (
        "Remove background cleanly, preserve subject edges, add a 8–10px white sticker border. "
        "No color shifts, keep original realism. Center the subject. Output 1024x1024 PNG."
    ),
    "cartoonize": (
        "Convert photo to a high-quality cartoon/illustration style with smooth shading, clean line art, "
        "vibrant but balanced colors. Keep subject identity and pose. Output 1024x1024 PNG with transparent background."
    ),
    "text_icons": (
        "Keep original photo, add playful overlays: text that is in the picture. "
        "Compose tastefully, avoid covering faces. Output 1024x1024 PNG with transparent background."
    ),
}

# Human-readable labels for UI
STYLE_KEY_TO_HUMAN = {
    "realistic_cutout": "Realistic Cutout",
    "cartoonize": "Cartoonize",
    "text_icons": "Text & Icons",
}

# -------------------
# Template globals (dynamic year, generation size knob)
# -------------------
@app.context_processor
def inject_globals():
    """Provide small global values for Jinja templates (e.g., footer year, image size)."""
    return {
        "current_year": datetime.utcnow().year,               # keep UTC to avoid TZ surprises
        "GEN_SIZE": os.environ.get("GEN_SIZE", "1024x1024"),  # allow overriding via env
    }

# -------------------
# Helpers
# -------------------

def get_sid() -> str:
    """Fetch or create a stable per-browser session id and ensure backing store exists."""
    sid = session.get("sid")
    if not sid:
        sid = uuid.uuid4().hex
        session["sid"] = sid
    if sid not in STORE:
        STORE[sid] = {}
    return sid

# -------------------
# Routes — Page 1: Camera
# -------------------
@app.route("/")
def camera():
    """Landing page with camera UI to capture a single photo."""
    _ = get_sid()
    return render_template("camera.html")

@app.route("/capture", methods=["POST"])
def capture():
    """Accept a data URL from the front-end camera and stash bytes in memory.

    Expects { imageData: "data:image/<type>;base64,<...>" }
    """
    sid = get_sid()
    data = (request.json or {}).get("imageData")

    # Validate minimal shape of data URL to avoid decoding untrusted junk
    if not data or not data.startswith("data:image/"):
        return jsonify({"ok": False, "error": "Invalid image data"}), 400

    header, b64 = data.split(",", 1)
    try:
        img_bytes = base64.b64decode(b64)
    except Exception:
        return jsonify({"ok": False, "error": "Bad base64 payload"}), 400

    # Stash into the in-memory session bucket
    STORE[sid]["captured_image"] = img_bytes
    # Example: header = "data:image/png;base64" → mime = "image/png"
    STORE[sid]["captured_mime"] = header.split(";")[0].split(":", 1)[1]
    STORE[sid]["ts"] = time.time()

    return jsonify({"ok": True, "redirect": url_for("review")})

# -------------------
# Routes — Page 2: Review
# -------------------
@app.route("/review")
def review():
    """Render a quick preview of the captured image for user confirmation."""
    sid = get_sid()
    img = STORE.get(sid, {}).get("captured_image")
    mime = STORE.get(sid, {}).get("captured_mime", "image/png")
    if not img:
        # If no image in session (e.g., refresh/new session), send back to camera
        return redirect(url_for("camera"))
    # Convert bytes back into a data URL for <img src="...">
    b64 = base64.b64encode(img).decode("ascii")
    data_url = f"data:{mime};base64,{b64}"
    return render_template("review.html", image_data=data_url)

# -------------------
# Routes — Page 3: Style Selection
# -------------------
@app.route("/style", methods=["GET", "POST"])
def style_select():
    """Either show the style selection page (GET) or accept a chosen style (POST)."""
    sid = get_sid()
    store = STORE.get(sid, {})
    img = store.get("captured_image")
    mime = store.get("captured_mime", "image/png")
    if not img:
        return redirect(url_for("camera"))

    if request.method == "POST":
        selected = (request.json or {}).get("style")
        if selected not in STYLE_PROMPTS:
            return jsonify({"ok": False, "error": "Invalid style"}), 400
        # Persist selected style for later steps
        store["selected_style"] = selected
        store["selected_prompt"] = STYLE_PROMPTS[selected]
        # Move to the multi-prompt page where users can add up to 4 tweaks
        return jsonify({"ok": True, "redirect": url_for("multi_prompts")})

    # GET: re-render the photo and show style options; remember previously chosen style if any
    b64 = base64.b64encode(img).decode("ascii")
    data_url = f"data:{mime};base64,{b64}"
    return render_template("style.html", image_data=data_url, preselected=store.get("selected_style"))

# -------------------
# Routes — Page 4: Multi-Prompt page
# -------------------
@app.route("/multi")
def multi_prompts():
    """Display UI for four sequential generations with a base style + per-image additions."""
    sid = get_sid()
    store = STORE.get(sid, {})
    if not store.get("captured_image"):
        return redirect(url_for("camera"))
    if not store.get("selected_style"):
        return redirect(url_for("style_select"))
    style_key = store["selected_style"]
    style_label = STYLE_KEY_TO_HUMAN.get(style_key, style_key)
    base_prompt = store.get("selected_prompt") or STYLE_PROMPTS.get(style_key, "")
    return render_template("multi.html", style_label=style_label, base_prompt=base_prompt)

# -------------------
# Background worker — sequentially generate 4 images
# -------------------

def _run_multi_generation(sid: str, base_prompt: str, style_key: str, img: bytes, mime: str, user_prompts: list[str]) -> None:
    """Generate 4 images sequentially using OpenAI Image Edit, updating progress in STORE.

    This function is intentionally run in a background thread. It reads/writes
    session-specific keys in STORE so the front-end can poll progress. If the
    user cancels, we exit early by checking gen_status.
    """
    store = STORE.get(sid, {})
    try:
        store["gen_status"] = "running"
        store["gen_progress"] = 0
        store["gen_error"] = None

        # A short prefix to guide consistent sticker output across styles
        sys_prefix = (
            "You are an expert sticker-maker. Produce one high-quality PNG suitable for printing stickers. "
            "Prefer transparent backgrounds when applicable. Keep the subject centered and sharp."
        )

        images_out: list[bytes] = []
        for i in range(4):
            # Early cancel check — if /api/gen-cancel flips the status, stop work
            if STORE.get(sid, {}).get("gen_status") == "canceled":
                return

            # Merge base style guidance with per-image user tweak
            merged_prompt = (
                f"{sys_prefix}\n"
                f"Style: {STYLE_KEY_TO_HUMAN.get(style_key, style_key)}\n"
                f"Instructions: {base_prompt}\n\n"
                f"Additional requirement: {user_prompts[i].strip() or 'No additional requirement.'}"
            )

            # Perform image edit using the captured photo bytes
            resp = client.images.edit(
                model="gpt-image-1",
                image=[("input.png", img)],  # single image edit in bytes form
                prompt=merged_prompt,
                size=os.environ.get("GEN_SIZE", "1024x1024"),
            )

            # The SDK has multiple shapes depending on version; extract base64 robustly
            gen_b64 = None
            try:
                gen_b64 = resp.data[0].b64_json
            except Exception:
                pass
            if not gen_b64:
                try:
                    out = getattr(resp, "output", None) or getattr(resp, "outputs", None) or []
                    if out:
                        content = getattr(out[0], "content", None) or []
                        for c in content:
                            if getattr(c, "type", None) in ("output_image", "image"):
                                gen_b64 = getattr(c, "image_base64", None) or getattr(c, "b64_json", None)
                                if gen_b64:
                                    break
                except Exception:
                    pass
            if not gen_b64:
                try:
                    gen_b64 = resp.output[0].content[0].image.base64  # type: ignore[attr-defined]
                except Exception:
                    pass
            if not gen_b64:
                # If the SDK returns a non-standard shape, surface a clear error to the UI
                raise RuntimeError("No image returned from model")

            # Convert to raw PNG bytes and collect
            images_out.append(base64.b64decode(gen_b64))

            # Update progress in 25% increments (after each of the 4 images)
            STORE[sid]["gen_progress"] = int(((i + 1) / 4) * 100)

        # On success, stage results for print flow and mark as done
        store["generated_images"] = images_out
        store["generated_mime"] = "image/png"
        store["approved_images"] = images_out  # auto-approve all for print page
        store["approved_mime"] = "image/png"
        store["gen_status"] = "done"
        store["ts"] = time.time()
    except Exception as e:
        # Any exception is surfaced to the polling endpoint so the UI can display it
        store["gen_status"] = "error"
        store["gen_error"] = str(e)

# -------------------
# API — start background generation & go to processing
# -------------------
@app.route("/api/generate-multi-start", methods=["POST"])
def api_generate_multi_start():
    """Kick off the background thread that sequentially produces 4 images.

    Expects JSON { prompts: [p0, p1, p2, p3] } where missing entries default to "".
    """
    sid = get_sid()
    store = STORE.get(sid, {})

    img = store.get("captured_image")
    mime = store.get("captured_mime", "image/png")
    style_key = store.get("selected_style")
    base_prompt = store.get("selected_prompt")

    payload = request.json or {}
    # Always normalize to exactly 4 entries so loop indices are consistent
    user_prompts = (payload.get("prompts") or []) + ["", "", "", ""]
    user_prompts = user_prompts[:4]

    if not img or not style_key or not base_prompt:
        return jsonify({"ok": False, "error": "Missing input"}), 400

    # Initialize status so the /processing page can immediately show a state
    store["gen_status"] = "queued"
    store["gen_progress"] = 0
    store["gen_error"] = None

    # Spawn the worker thread; daemon=True so it won't block shutdown in dev
    t = threading.Thread(
        target=_run_multi_generation,
        args=(sid, base_prompt, style_key, img, mime, user_prompts),
        daemon=True,
    )
    t.start()

    return jsonify({"ok": True, "redirect": url_for("processing")})

# -------------------
# API — poll status / cancel
# -------------------
@app.route("/api/gen-status")
def api_gen_status():
    """Return the current status/progress/error for the user's generation job."""
    sid = get_sid()
    s = STORE.get(sid, {})
    return jsonify({
        "ok": True,
        "status": s.get("gen_status", "idle"),
        "progress": int(s.get("gen_progress", 0)),
        "error": s.get("gen_error"),
    })

@app.route("/api/gen-cancel", methods=["POST"])
def api_gen_cancel():
    """Allow the user to cancel the current job; worker checks this flag between images."""
    sid = get_sid()
    s = STORE.get(sid, {})
    s["gen_status"] = "canceled"
    return jsonify({"ok": True, "redirect": url_for("style_select")})

# -------------------
# Routes — Processing (kept)
# -------------------
@app.route("/processing")
def processing():
    """Show a progress UI while the background thread runs; redirects if prerequisites missing."""
    sid = get_sid()
    store = STORE.get(sid, {})
    if not store.get("captured_image"):
        return redirect(url_for("camera"))
    if not store.get("selected_style"):
        return redirect(url_for("style_select"))
    style_key = store["selected_style"]
    style_label = STYLE_KEY_TO_HUMAN.get(style_key, style_key)
    return render_template("processing.html", style_label=style_label)

# -------------------
# Routes — Print Layout (expects 4 images)
# -------------------
@app.route("/print-layout")
def print_layout():
    """Final layout page; requires 4 approved images prepared by the background worker."""
    sid = get_sid()
    store = STORE.get(sid, {})
    if not store.get("approved_images"):
        if store.get("selected_style"):
            return redirect(url_for("multi_prompts"))
        return redirect(url_for("style_select"))
    return render_template("print.html")

@app.route("/api/approved-list")
def api_approved_list():
    """Return 4 data URLs for the approved images that the print page can render."""
    sid = get_sid()
    store = STORE.get(sid, {})
    imgs = store.get("approved_images")
    mime = store.get("approved_mime", "image/png")
    if not imgs or len(imgs) != 4:
        return jsonify({"ok": False, "error": "No approved images"}), 404

    data_urls: list[str] = []
    for b in imgs:
        b64 = base64.b64encode(b).decode("ascii")
        data_urls.append(f"data:{mime};base64,{b64}")
    return jsonify({"ok": True, "data_urls": data_urls})

# -------------------
# Printer utils (unchanged)
# -------------------
@app.route("/printer-info")
def printer_info():
    """Expose simple CUPS status so the UI can show which printers (if any) are usable."""
    info = {"available": False, "default": None, "raw": None}
    if shutil.which("lpstat"):
        try:
            out = subprocess.check_output(
                ["lpstat", "-p", "-d"], stderr=subprocess.STDOUT
            ).decode("utf-8", "ignore")
            info["raw"] = out
            default = None
            for line in out.splitlines():
                if "system default destination:" in line:
                    default = line.split(":", 1)[1].strip()
                    break
            info["default"] = default
            info["available"] = True
        except Exception as e:
            # Surface errors for debugging; UI treats this as non-fatal
            info["raw"] = str(e)
    return jsonify(info)

@app.route("/print-direct", methods=["POST"])
def print_direct():
    """Send a PNG sheet directly to the default CUPS printer using `lp`.

    This is optional—users can also use the browser's Print dialog on the print page.
    """
    if not shutil.which("lp"):
        return jsonify({"ok": False, "error": "Direct print not available (lp not found). Use browser Print."}), 400

    sid = get_sid()
    store = STORE.get(sid, {})
    data_url = (request.json or {}).get("sheet")
    if not data_url or not data_url.startswith("data:image/png;base64,"):
        return jsonify({"ok": False, "error": "Invalid sheet payload"}), 400

    b64 = data_url.split(",", 1)[1]
    try:
        png_bytes = base64.b64decode(b64)
    except Exception:
        return jsonify({"ok": False, "error": "Bad base64 payload"}), 400

    try:
        proc = subprocess.run(
            ["lp", "-o", "media=A4", "-o", "fit-to-page"],
            input=png_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        if proc.returncode != 0:
            return jsonify({"ok": False, "error": proc.stdout.decode("utf-8", "ignore")}), 500

        # On success, aggressively free memory for this session (no lingering PII/media)
        for key in [
            "captured_image", "generated_images", "approved_images",
            "selected_style", "selected_prompt", "generated_mime",
            "approved_mime", "ts", "gen_status", "gen_progress", "gen_error"
        ]:
            store.pop(key, None)
        return jsonify({"ok": True, "message": proc.stdout.decode("utf-8", "ignore")})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# -------------------
# Misc / Utils
# -------------------
@app.errorhandler(413)
def too_large(_):
    """Consistent JSON error shape for request bodies that exceed MAX_CONTENT_LENGTH."""
    return jsonify({"ok": False, "error": "Payload too large"}), 413

@app.route("/reset", methods=["POST"])
def reset():
    """Hard-reset the session store for this browser; use when starting over."""
    sid = session.get("sid")
    if sid and sid in STORE:
        del STORE[sid]
    session.clear()
    return jsonify({"ok": True, "redirect": url_for("camera")})

@app.route("/start-new", methods=["POST"])
def start_new():
    """Alias for /reset to make the intent clearer from the UI layer."""
    sid = session.get("sid")
    if sid and sid in STORE:
        del STORE[sid]
    session.clear()
    return jsonify({"ok": True, "redirect": url_for("camera")})

# -------------------
# Entrypoint
# -------------------
if __name__ == "__main__":
    # Debug only for local dev; in production, run behind a real WSGI server (gunicorn/uwsgi)
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", "5005")))
