// static/js/multi.js
(function () {
  const form = document.getElementById('multiForm');
  const btn = document.getElementById('generateMultiBtn');
  if (!form || !btn) return;

  async function onSubmit(e) {
    e.preventDefault();
    const prompts = [
      document.getElementById('p1')?.value || '',
      document.getElementById('p2')?.value || '',
      document.getElementById('p3')?.value || '',
      document.getElementById('p4')?.value || '',
    ];

    const prev = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Preparing…';

    try {
      const res = await fetch('/api/generate-multi-start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompts })
      });
      const json = await res.json();
      if (json.ok && json.redirect) {
        window.location.href = json.redirect; // -> /processing
      } else {
        alert(json.error || 'Could not start generation.');
      }
    } catch (err) {
      console.error(err);
      alert('Network error while starting generation.');
    } finally {
      // If we didn’t navigate, restore UI
      btn.disabled = false;
      btn.textContent = prev;
    }
  }

  form.addEventListener('submit', onSubmit);
})();
