# main.py — Sticker Booth (multi-image, sequential gen, with processing)
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import base64, uuid, time, os, subprocess, shutil, threading
from datetime import datetime

# Optional: load .env if python-dotenv is installed
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# -------------------
# App & Config
# -------------------
app = Flask(__name__)
app.secret_key = os.environ.get("APP_SECRET_KEY", "change-me-please")

# Security & limits
app.config.update(
    MAX_CONTENT_LENGTH=5 * 1024 * 1024,  # limit request bodies to ~5MB
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=(os.environ.get("SESSION_COOKIE_SECURE", "0") == "1"),
)

# In-memory store: per-session state (no disk writes)
STORE = {}

# Idle session TTL (seconds). Default 10 minutes.
SESSION_TTL_SECONDS = int(os.environ.get("SESSION_TTL_SECONDS", "600"))

def purge_expired():
    """Remove idle sessions to free memory."""
    now = time.time()
    for sid, s in list(STORE.items()):
        ts = s.get("ts", now)
        if now - ts > SESSION_TTL_SECONDS:
            STORE.pop(sid, None)

@app.before_request
def _touch_and_purge():
    """Refresh last-activity and purge idle sessions on every request."""
    purge_expired()
    sid = session.get("sid")
    if sid in STORE:
        STORE[sid]["ts"] = time.time()

# -------------------
# OpenAI Client (REQUIRED)
# -------------------
from openai import OpenAI
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is required. Set it in the environment before starting the app.")
client = OpenAI(api_key=OPENAI_API_KEY)

# -------------------
# Style prompts / labels
# -------------------
STYLE_PROMPTS = {
    "realistic_cutout": (
        "Remove background cleanly, preserve subject edges, add a 10–12px white sticker border. "
        "No color shifts, keep original realism. Center the subject. Output 1024x1024 PNG."
    ),
    "cartoonize": (
        "Convert photo to a high-quality cartoon/illustration style with smooth shading, clean line art, "
        "vibrant but balanced colors. Keep subject identity and pose. Output 1024x1024 PNG with transparent background."
    ),
    "text_icons": (
        "Keep original photo, add playful overlays: text ('Super Girl', 'Queen') and icons (crown, sparkles). "
        "Compose tastefully, avoid covering faces. Output 1024x1024 PNG with transparent background."
    ),
}
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
    return {
        "current_year": datetime.utcnow().year,
        "GEN_SIZE": os.environ.get("GEN_SIZE", "1024x1024"),
    }

# -------------------
# Helpers
# -------------------
def get_sid():
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
    _ = get_sid()
    return render_template("camera.html")

@app.route("/capture", methods=["POST"])
def capture():
    sid = get_sid()
    data = (request.json or {}).get("imageData")
    if not data or not data.startswith("data:image/"):
        return jsonify({"ok": False, "error": "Invalid image data"}), 400
    header, b64 = data.split(",", 1)
    try:
        img_bytes = base64.b64decode(b64)
    except Exception:
        return jsonify({"ok": False, "error": "Bad base64 payload"}), 400
    STORE[sid]["captured_image"] = img_bytes
    STORE[sid]["captured_mime"] = header.split(";")[0].split(":", 1)[1]  # e.g., image/png
    STORE[sid]["ts"] = time.time()
    return jsonify({"ok": True, "redirect": url_for("review")})

# -------------------
# Routes — Page 2: Review
# -------------------
@app.route("/review")
def review():
    sid = get_sid()
    img = STORE.get(sid, {}).get("captured_image")
    mime = STORE.get(sid, {}).get("captured_mime", "image/png")
    if not img:
        return redirect(url_for("camera"))
    b64 = base64.b64encode(img).decode("ascii")
    data_url = f"data:{mime};base64,{b64}"
    return render_template("review.html", image_data=data_url)

# -------------------
# Routes — Page 3: Style Selection
# -------------------
@app.route("/style", methods=["GET", "POST"])
def style_select():
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
        store["selected_style"] = selected
        store["selected_prompt"] = STYLE_PROMPTS[selected]
        return jsonify({"ok": True, "redirect": url_for("multi_prompts")})  # → four prompts page

    # GET
    b64 = base64.b64encode(img).decode("ascii")
    data_url = f"data:{mime};base64,{b64}"
    return render_template("style.html", image_data=data_url, preselected=store.get("selected_style"))

# -------------------
# Routes — Page 4: Multi-Prompt page
# -------------------
@app.route("/multi")
def multi_prompts():
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
def _run_multi_generation(sid, base_prompt, style_key, img, mime, user_prompts):
    store = STORE.get(sid, {})
    try:
        store["gen_status"] = "running"
        store["gen_progress"] = 0
        store["gen_error"] = None

        sys_prefix = (
            "You are an expert sticker-maker. Produce one high-quality PNG suitable for printing stickers. "
            "Prefer transparent backgrounds when applicable. Keep the subject centered and sharp."
        )

        images_out = []
        for i in range(4):
            # Early cancel check
            if STORE.get(sid, {}).get("gen_status") == "canceled":
                return

            merged_prompt = (
                f"{sys_prefix}\n"
                f"Style: {STYLE_KEY_TO_HUMAN.get(style_key, style_key)}\n"
                f"Instructions: {base_prompt}\n\n"
                f"Additional requirement: {user_prompts[i].strip() or 'No additional requirement.'}"
            )

            resp = client.images.edit(
                model="gpt-image-1",
                image=[("input.png", img)],  # use captured image bytes
                prompt=merged_prompt,
                size=os.environ.get("GEN_SIZE", "1024x1024"),
            )

            # Extract base64 robustly across SDK shapes
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
                    gen_b64 = resp.output[0].content[0].image.base64  # type: ignore
                except Exception:
                    pass
            if not gen_b64:
                raise RuntimeError("No image returned from model")

            images_out.append(base64.b64decode(gen_b64))

            # update progress after each image (25, 50, 75, 100)
            STORE[sid]["gen_progress"] = int(((i + 1) / 4) * 100)

        store["generated_images"] = images_out
        store["generated_mime"] = "image/png"
        store["approved_images"] = images_out  # mark approved for print flow
        store["approved_mime"] = "image/png"
        store["gen_status"] = "done"
        store["ts"] = time.time()
    except Exception as e:
        store["gen_status"] = "error"
        store["gen_error"] = str(e)

# -------------------
# API — start background generation & go to processing
# -------------------
@app.route("/api/generate-multi-start", methods=["POST"])
def api_generate_multi_start():
    sid = get_sid()
    store = STORE.get(sid, {})

    img = store.get("captured_image")
    mime = store.get("captured_mime", "image/png")
    style_key = store.get("selected_style")
    base_prompt = store.get("selected_prompt")

    payload = request.json or {}
    user_prompts = (payload.get("prompts") or []) + ["", "", "", ""]
    user_prompts = user_prompts[:4]

    if not img or not style_key or not base_prompt:
        return jsonify({"ok": False, "error": "Missing input"}), 400

    # initialize status
    store["gen_status"] = "queued"
    store["gen_progress"] = 0
    store["gen_error"] = None

    # spawn background thread — sequential generation
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
    sid = get_sid()
    s = STORE.get(sid, {})
    s["gen_status"] = "canceled"
    return jsonify({"ok": True, "redirect": url_for("style_select")})

# -------------------
# Routes — Processing (kept)
# -------------------
@app.route("/processing")
def processing():
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
    sid = get_sid()
    store = STORE.get(sid, {})
    if not store.get("approved_images"):
        if store.get("selected_style"):
            return redirect(url_for("multi_prompts"))
        return redirect(url_for("style_select"))
    return render_template("print.html")

@app.route("/api/approved-list")
def api_approved_list():
    sid = get_sid()
    store = STORE.get(sid, {})
    imgs = store.get("approved_images")
    mime = store.get("approved_mime", "image/png")
    if not imgs or len(imgs) != 4:
        return jsonify({"ok": False, "error": "No approved images"}), 404
    data_urls = []
    for b in imgs:
        b64 = base64.b64encode(b).decode("ascii")
        data_urls.append(f"data:{mime};base64,{b64}")
    return jsonify({"ok": True, "data_urls": data_urls})

# -------------------
# Printer utils (unchanged)
# -------------------
@app.route("/printer-info")
def printer_info():
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
            info["raw"] = str(e)
    return jsonify(info)

@app.route("/print-direct", methods=["POST"])
def print_direct():
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

        # On success, free memory for this session
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
    return jsonify({"ok": False, "error": "Payload too large"}), 413

@app.route("/reset", methods=["POST"])
def reset():
    sid = session.get("sid")
    if sid and sid in STORE:
        del STORE[sid]
    session.clear()
    return jsonify({"ok": True, "redirect": url_for("camera")})

@app.route("/start-new", methods=["POST"])
def start_new():
    sid = session.get("sid")
    if sid and sid in STORE:
        del STORE[sid]
    session.clear()
    return jsonify({"ok": True, "redirect": url_for("camera")})

# -------------------
# Entrypoint
# -------------------
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", "5005")))
