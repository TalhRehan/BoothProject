# main.py — Sticker Booth (clean, hardened, end-to-end)
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import base64, uuid, time, os, subprocess, shutil
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
    # Set this to "1" in production (HTTPS). Default "0" keeps dev working over http.
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
        return jsonify({"ok": True, "redirect": url_for("processing")})

    # GET
    b64 = base64.b64encode(img).decode("ascii")
    data_url = f"data:{mime};base64,{b64}"
    return render_template("style.html", image_data=data_url, preselected=store.get("selected_style"))

# -------------------
# Routes — Page 4: Processing
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

@app.route("/cancel", methods=["POST"])
def cancel_processing():
    sid = get_sid()
    STORE.get(sid, {}).pop("generation_inflight", None)
    return jsonify({"ok": True, "redirect": url_for("style_select")})

@app.route("/generate", methods=["POST"])
def generate():
    """
    Generate a styled image using the captured input + selected style prompt.
    Stores the result in-memory and returns redirect URL to /result.
    """
    sid = get_sid()
    store = STORE.get(sid, {})

    img = store.get("captured_image")
    mime = store.get("captured_mime", "image/png")
    prompt = store.get("selected_prompt")
    style_key = store.get("selected_style")

    if not img or not prompt or not style_key:
        return jsonify({"ok": False, "error": "Missing input"}), 400

    store["generation_inflight"] = True

    # Prepare input image as a data URL for multimodal request
    b64 = base64.b64encode(img).decode("ascii")
    data_url = f"data:{mime};base64,{b64}"

    try:
        # Build rich, style-specific instruction (size/background can be part of prompt)
        sys_prefix = (
            "You are an expert sticker-maker. Produce a single high-quality PNG suitable for printing stickers. "
            "Prefer transparent backgrounds when applicable. Keep the subject centered and sharp."
        )
        full_prompt = (
            f"{sys_prefix}\n"
            f"Style: {STYLE_KEY_TO_HUMAN.get(style_key, style_key)}\n"
            f"Instructions: {prompt}"
        )

        # Minimal request for SDK compatibility (no 'modalities'/'image' kwargs)
        resp = client.images.edit(
            model="gpt-image-1",
            image=[("input.png", img)],  # bytes from captured image
            prompt=full_prompt,
            size="1024x1024"
        )
        gen_b64 = resp.data[0].b64_json
        gen_bytes = base64.b64decode(gen_b64)

        # Extract base64 image robustly across SDK shapes

        try:
            out = getattr(resp, "output", None) or getattr(resp, "outputs", None) or []
            if out:
                content = getattr(out[0], "content", None) or []
                for c in content:
                    ctype = getattr(c, "type", None)
                    if ctype in ("output_image", "image"):
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
            raise RuntimeError("No image returned from model")

        gen_bytes = base64.b64decode(gen_b64)
        store["generated_image"] = gen_bytes
        store["generated_mime"] = "image/png"

        store.pop("generation_inflight", None)
        return jsonify({"ok": True, "redirect": url_for("result")})
    except Exception as e:
        store.pop("generation_inflight", None)
        # Clear, UI-friendly error for toast
        return jsonify({"ok": False, "error": f"Generation error: {str(e)}"}), 502

# -------------------
# Routes — Page 5: Result Review
# -------------------
@app.route("/result")
def result():
    sid = get_sid()
    store = STORE.get(sid, {})
    if not store.get("captured_image"):
        return redirect(url_for("camera"))
    if not store.get("generated_image"):
        # If user landed here without generation, route appropriately
        if store.get("selected_style"):
            return redirect(url_for("processing"))
        return redirect(url_for("style_select"))

    # Build data URLs for display
    orig_b64 = base64.b64encode(store["captured_image"]).decode("ascii")
    orig_mime = store.get("captured_mime", "image/png")
    gen_b64 = base64.b64encode(store["generated_image"]).decode("ascii")
    gen_mime = store.get("generated_mime", "image/png")

    style_key = store.get("selected_style")
    style_label = STYLE_KEY_TO_HUMAN.get(style_key, style_key)

    return render_template(
        "result.html",
        original_data=f"data:{orig_mime};base64,{orig_b64}",
        generated_data=f"data:{gen_mime};base64,{gen_b64}",
        style_label=style_label,
    )

@app.route("/regenerate", methods=["POST"])
def regenerate():
    sid = get_sid()
    store = STORE.get(sid, {})
    # Clear output + selection so user must pick again
    store.pop("generated_image", None)
    store.pop("generated_mime", None)
    store.pop("selected_style", None)
    store.pop("selected_prompt", None)
    return jsonify({"ok": True, "redirect": url_for("style_select")})

@app.route("/approve", methods=["POST"])
def approve():
    sid = get_sid()
    store = STORE.get(sid, {})
    gen = store.get("generated_image")
    if not gen:
        return jsonify({"ok": False, "error": "Nothing to approve"}), 400
    store["approved_image"] = gen
    store["approved_mime"] = store.get("generated_mime", "image/png")
    return jsonify({"ok": True, "redirect": url_for("print_layout")})

# -------------------
# Routes — Page 6: Print Layout
# -------------------
@app.route("/print-layout")
def print_layout():
    sid = get_sid()
    store = STORE.get(sid, {})
    if not store.get("approved_image"):
        return redirect(url_for("result"))
    return render_template("print.html")

@app.route("/printer-info")
def printer_info():
    """Detect default printer via CUPS (lpstat) if available."""
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
    """
    Accepts composed A4 PNG (base64 data URL) and sends to default CUPS printer.
    No disk writes; bytes are piped to `lp`.
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

        # On success, free memory for this session
        for key in [
            "captured_image", "generated_image", "approved_image",
            "selected_style", "selected_prompt", "generated_mime",
            "approved_mime", "ts"
        ]:
            store.pop(key, None)
        return jsonify({"ok": True, "message": proc.stdout.decode("utf-8", "ignore")})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# -------------------
# Misc / Utils
# -------------------
@app.route("/api/approved")
def api_approved():
    """Return approved image as a data URL for the print composer."""
    sid = get_sid()
    store = STORE.get(sid, {})
    img = store.get("approved_image")
    mime = store.get("approved_mime", "image/png")
    if not img:
        return jsonify({"ok": False, "error": "No approved image"}), 404
    b64 = base64.b64encode(img).decode("ascii")
    return jsonify({"ok": True, "data_url": f"data:{mime};base64,{b64}"})

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
    # Bind to 0.0.0.0 so you can access from another device on the LAN if needed.
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", "5005")))
