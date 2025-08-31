// static/js/processing.js
(function () {
  const fill = document.getElementById('progressFill');
  const pctEl = document.getElementById('progressPct');
  const etaEl = document.getElementById('eta');
  const cancelBtn = document.getElementById('cancelBtn');

  let visualProgress = 0; // smooth client-side progress
  let targetProgress = 0; // server-reported progress
  let polling = true;

  function render() {
    const diff = targetProgress - visualProgress;
    visualProgress += Math.sign(diff) * Math.min(Math.abs(diff), 2); // 2% per frame
    if (fill) fill.style.width = visualProgress + '%';
    if (pctEl) pctEl.textContent = Math.round(visualProgress) + '%';

    if (etaEl) {
      const remaining = Math.max(0, 100 - visualProgress);
      const secs = Math.ceil((remaining / 100) * 40); // ~40s feel
      etaEl.textContent = `~${secs}s remaining`;
    }

    if (polling) requestAnimationFrame(render);
  }

  async function poll() {
    try {
      const res = await fetch('/api/gen-status');
      const json = await res.json();
      if (!json.ok) throw new Error('Bad status');

      targetProgress = Number(json.progress || 0);

      if (json.status === 'done') {
        targetProgress = 100;
        setTimeout(() => (window.location.href = '/print-layout'), 250);
        return;
      }
      if (json.status === 'error') {
        alert('Generation failed: ' + (json.error || 'Unknown error'));
        window.location.href = '/multi';
        return;
      }
      if (json.status === 'canceled') {
        window.location.href = '/style';
        return;
      }
    } catch (e) {
      console.error(e);
      // keep trying
    } finally {
      if (polling) setTimeout(poll, 1200);
    }
  }

  if (cancelBtn) {
    cancelBtn.addEventListener('click', async () => {
      try {
        const res = await fetch('/api/gen-cancel', { method: 'POST' });
        const json = await res.json();
        if (json.ok && json.redirect) window.location.href = json.redirect;
      } catch (e) {
        window.location.href = '/style';
      }
    });
  }

  requestAnimationFrame(render);
  poll();
})();
