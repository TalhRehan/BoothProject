// static/js/processing.js
(function () {
  const progressFill = document.getElementById('progressFill');
  const progressPct = document.getElementById('progressPct');
  const etaText = document.getElementById('eta');
  const cancelBtn = document.getElementById('cancelBtn');
  const progressBar = document.getElementById('progressBar'); // optional ARIA sync

  let pct = 0;
  let eta = 30; // seconds target
  let timer = null;
  let finished = false;

  // Allow cancel to abort the fetch
  const controller = new AbortController();

  function render() {
    if (progressFill) progressFill.style.width = pct + '%';
    if (progressPct) progressPct.textContent = Math.round(pct) + '%';
    if (etaText) etaText.textContent = '~' + Math.max(0, Math.ceil(eta)) + 's remaining';
    // Keep ARIA in sync if processing.html added role="progressbar"
    if (progressBar) progressBar.setAttribute('aria-valuenow', String(Math.round(pct)));
  }

  function startProgress() {
    // Smoothly progress to 90% while generation runs; jump to 100% on completion.
    timer = setInterval(() => {
      if (finished) return;
      const target = 90;
      const delta = Math.max(0.2, (target - pct) * 0.03); // ease towards 90
      pct = Math.min(target, pct + delta);
      eta = Math.max(0, eta - 0.5);
      render();
    }, 250);
  }

  async function runGeneration() {
    try {
      const res = await fetch('/generate', { method: 'POST', signal: controller.signal });
      const json = await res.json();
      finished = true;
      pct = 100;
      eta = 0;
      render();
      if (json.ok) {
        window.location.href = json.redirect || '/result';
      } else {
        alert('Generation failed: ' + (json.error || 'Unknown error'));
        window.location.href = '/style';
      }
    } catch (e) {
      finished = true;
      // If we aborted on purpose, just exit quietly
      if (e && (e.name === 'AbortError' || e.code === DOMException.ABORT_ERR)) {
        return;
      }
      alert('Network error while generating');
      window.location.href = '/style';
    } finally {
      clearInterval(timer);
    }
  }

  cancelBtn?.addEventListener('click', async () => {
    cancelBtn.disabled = true;
    try {
      controller.abort(); // stop the in-flight /generate
    } catch (_) {}
    try {
      await fetch('/cancel', { method: 'POST' });
    } catch (_) {}
    window.location.href = '/style';
  });

  // Abort if the user navigates away mid-request
  window.addEventListener('beforeunload', () => {
    try { controller.abort(); } catch (_) {}
  });

  render();
  startProgress();
  runGeneration();
})();
