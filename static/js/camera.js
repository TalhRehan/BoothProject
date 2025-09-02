//camera.js
(function () {
  // --- DOM refs ---
  const video         = document.getElementById('video');
  const countdownEl   = document.getElementById('countdown');
  const overlay       = document.getElementById('overlay');
  const captureBtn    = document.getElementById('captureBtn');
  const camStatus     = document.getElementById('camStatus');
  const resolutionEl  = document.getElementById('resolution');

  // Settings UI (may or may not exist on this page)
  const settingsBtn   = document.getElementById('settingsBtn');
  const settingsModal = document.getElementById('settingsModal');
  const cameraSelect  = document.getElementById('cameraSelect');
  const applySettings = document.getElementById('applySettings');

  const canvas = document.getElementById('captureCanvas');
  // If core camera elements are missing, exit early—safe on non-camera pages.
  if (!canvas || !video) {
    // Not an error in multi-page apps; simply means this page doesn't host the camera UI.
    return;
  }
  const ctx = canvas.getContext('2d');

  // --- State ---
  let mediaStream = null;
  let currentDeviceId = null;
  let isCountingDown = false;
  let countdownTimer = null;

  const PREF_KEY = 'preferredCameraId';

  // --- Helpers ---
  function setStatus(connected) {
    if (!camStatus) return;
    camStatus.textContent = connected ? 'Connected' : 'Disconnected';
    camStatus.style.color = connected ? '#2e7d32' : '#c62828';
  }

  function hdLabel(width, height) {
    if (width >= 1920 || height >= 1080) return `${width}×${height} (FHD)`;
    if (width >= 1280 || height >= 720)  return `${width}×${height} (HD)`;
    return `${width}×${height} (SD)`;
  }

  async function listCameras() {
    // Guard: not all browsers/devices expose enumerateDevices, or permissions not granted yet.
    if (!navigator.mediaDevices?.enumerateDevices) return;
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      const videoInputs = devices.filter(d => d.kind === 'videoinput');
      if (!cameraSelect) return; // Defensive: settings modal might not be in DOM or on this page.

      cameraSelect.innerHTML = '';
      videoInputs.forEach((d, i) => {
        const opt = document.createElement('option');
        opt.value = d.deviceId;
        opt.textContent = d.label || `Camera ${i + 1}`;
        cameraSelect.appendChild(opt);
      });

      const preferred = (() => {
        try { return localStorage.getItem(PREF_KEY); } catch { return null; }
      })();

      const pick = currentDeviceId || preferred || (videoInputs[0] && videoInputs[0].deviceId);
      if (pick) cameraSelect.value = pick;
    } catch (e) {
      // Noisy errors (e.g., permissions) shouldn’t break the flow.
      console.warn('enumerateDevices failed', e);
    }
  }

  function getActiveVideoDeviceId(stream) {
    try {
      const track = stream.getVideoTracks()[0];
      const settings = track?.getSettings ? track.getSettings() : {};
      return settings.deviceId || null;
    } catch {
      return null;
    }
  }

  function drawCoverCoords(sourceW, sourceH, targetW, targetH) {
    const sourceAR = sourceW / sourceH;
    const targetAR = targetW / targetH;
    let sx, sy, sw, sh;
    if (sourceAR > targetAR) {
      sh = sourceH;
      sw = Math.round(sh * targetAR);
      sx = Math.round((sourceW - sw) / 2);
      sy = 0;
    } else {
      sw = sourceW;
      sh = Math.round(sw / targetAR);
      sx = 0;
      sy = Math.round((sourceH - sh) / 2);
    }
    return { sx, sy, sw, sh };
  }

  async function stopCamera() {
    try {
      if (mediaStream) mediaStream.getTracks().forEach(t => t.stop());
    } catch { /* no-op */ }
    mediaStream = null;
    setStatus(false);
  }

  async function startCamera(deviceId = null) {
    if (!navigator.mediaDevices?.getUserMedia) {
      alert('This browser does not support camera access.');
      return;
    }

    try {
      await stopCamera();
      const constraints = {
        video: {
          deviceId: deviceId ? { exact: deviceId } : undefined,
          width: { ideal: 1280 },
          aspectRatio: 4 / 3
        },
        audio: false
      };
      mediaStream = await navigator.mediaDevices.getUserMedia(constraints);
      currentDeviceId = deviceId || null;
      video.srcObject = mediaStream;
      await video.play();
      setStatus(true);

      // Wait for dimensions
      await new Promise(resolve => {
        if (video.readyState >= 2) return resolve();
        video.onloadedmetadata = () => resolve();
      });

      if (resolutionEl) {
        resolutionEl.textContent = hdLabel(video.videoWidth, video.videoHeight);
      }

      await listCameras();

      const realDeviceId = getActiveVideoDeviceId(mediaStream) || deviceId;
      if (realDeviceId) {
        currentDeviceId = realDeviceId;
        try { localStorage.setItem(PREF_KEY, realDeviceId); } catch {}
        if (cameraSelect) cameraSelect.value = realDeviceId;
      }
    } catch (err) {
      console.error('getUserMedia error', err);
      setStatus(false);
      alert('Unable to access camera. Please grant permission or connect a webcam.');
    }
  }

  function startCountdown() {
    if (!overlay || !countdownEl || isCountingDown) return;

    isCountingDown = true;
    captureBtn?.setAttribute('disabled', 'true');

    clearInterval(countdownTimer);
    let n = 5; // 5 → 4 → 3 → 2 → 1
    overlay.style.display = 'flex';
    countdownEl.textContent = String(n);

    countdownTimer = setInterval(() => {
      n -= 1;
      if (n >= 1) {
        countdownEl.textContent = String(n);
      } else {
        clearInterval(countdownTimer);
        setTimeout(async () => {
          overlay.style.display = 'none';
          try {
            await captureFrame();
          } finally {
            isCountingDown = false;
            captureBtn?.removeAttribute('disabled');
          }
        }, 120);
      }
    }, 1000);
  }

  async function captureFrame() {
    if (!video || !canvas || !ctx) return;
    const vw = video.videoWidth  || 1280;
    const vh = video.videoHeight || 960;
    const cw = canvas.width;
    const ch = canvas.height;
    const { sx, sy, sw, sh } = drawCoverCoords(vw, vh, cw, ch);

    ctx.clearRect(0, 0, cw, ch);
    ctx.drawImage(video, sx, sy, sw, sh, 0, 0, cw, ch);
    const dataUrl = canvas.toDataURL('image/png');

    try {
      const res = await fetch('/capture', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ imageData: dataUrl })
      });
      const json = await res.json();
      if (json.ok) {
        await stopCamera();
        window.location.href = json.redirect;
      } else {
        alert('Failed to capture: ' + (json.error || 'Unknown error'));
      }
    } catch (e) {
      console.error(e);
      alert('Network error while sending capture.');
    }
  }

  // --- UI wiring ---
  captureBtn?.addEventListener('click', (e) => {
    // Require a user gesture to start countdown
    if (e && e.isTrusted !== false) startCountdown();
  });

  // Keyboard shortcut: press "c" to start countdown
  window.addEventListener('keydown', (e) => {
    if (e.key && e.key.toLowerCase() === 'c') startCountdown();
  });

  // Settings modal is now globally wired in base.html, but keeping these is safe (no-ops if absent)
  settingsBtn?.addEventListener('click', () => {
    try { settingsModal?.showModal?.(); } catch { /* no-op */ }
  });

  applySettings?.addEventListener('click', async (e) => {
    e.preventDefault();
    try { settingsModal?.close?.(); } catch { /* no-op */ }
    const newId = cameraSelect?.value || null; // ✅ Guarded: only read if it exists
    await startCamera(newId);
  });

  // Update camera list on USB plug/unplug
  if (navigator.mediaDevices?.addEventListener) {
    navigator.mediaDevices.addEventListener('devicechange', async () => {
      await listCameras();
      const options = Array.from(cameraSelect?.options || []).map(o => o.value);
      if (currentDeviceId && !options.includes(currentDeviceId)) {
        const fallback = options[0] || null;
        if (fallback) startCamera(fallback);
      }
    });
  }

  // Clean up stream when leaving the page
  window.addEventListener('pagehide', stopCamera);
  window.addEventListener('beforeunload', stopCamera);

  // --- Bootstrap on load ---
  window.addEventListener('load', async () => {
    if (!navigator.mediaDevices?.getUserMedia) {
      alert('This browser does not support camera access.');
      return;
    }

    // 1) Ensure overlay hidden initially
    if (overlay) overlay.style.display = 'none';

    // 2) Nuke any stale retake flags (from earlier versions)
    try { sessionStorage.removeItem('autoRetake'); } catch { /* no-op */ }

    // 3) Strip any ?auto=... param so reloads don't retrigger behavior
    try {
      const url = new URL(window.location.href);
      if (url.searchParams.has('auto')) {
        url.searchParams.delete('auto');
        history.replaceState({}, '', url.pathname + (url.search ? '?' + url.searchParams.toString() : '') + url.hash);
      }
    } catch { /* no-op */ }

    const preferred = (() => {
      try { return localStorage.getItem(PREF_KEY); } catch { return null; }
    })();

    await startCamera(preferred || null);
    // IMPORTANT: No auto-start of capture—user must click CAPTURE (or press "c").
  });
})();
