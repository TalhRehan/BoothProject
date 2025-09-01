//// static/js/result.js
//(function () {
//  const regenBtn = document.getElementById('regenBtn');
//  const approveBtn = document.getElementById('approveBtn');
//
//  if (!regenBtn && !approveBtn) return;
//
//  let inflight = false;
//
//  async function postAndRedirect(endpoint, btn) {
//    if (inflight) return;
//    inflight = true;
//
//    const other = btn === regenBtn ? approveBtn : regenBtn;
//    const prevText = btn ? btn.textContent : '';
//
//    // Disable both buttons; show working label on the clicked one
//    if (btn) {
//      btn.disabled = true;
//      btn.textContent = endpoint === '/regenerate' ? 'Regenerating…' : 'Printing...';
//    }
//    if (other) other.disabled = true;
//
//    try {
//      const res = await fetch(endpoint, { method: 'POST' });
//      let json = null;
//      try { json = await res.json(); } catch (_) {}
//
//      if (json && json.ok && json.redirect) {
//        window.location.href = json.redirect;
//        return; // stop here; page will navigate
//      }
//
//      const err = (json && json.error) ? json.error : `HTTP ${res.status}`;
//      if (endpoint === '/regenerate') {
//        alert('Could not regenerate: ' + err);
//        // Safe fallback: return user to style selection
//        window.location.href = '/style';
//      } else {
//        alert('Could not approve: ' + err);
//      }
//    } catch (e) {
//      alert('Network error. Please try again.');
//    } finally {
//      // If we didn’t navigate, restore UI
//      inflight = false;
//      if (btn) {
//        btn.disabled = false;
//        btn.textContent = prevText;
//      }
//      if (other) other.disabled = false;
//    }
//  }
//
//  regenBtn?.addEventListener('click', () => postAndRedirect('/regenerate', regenBtn));
//  approveBtn?.addEventListener('click', () => postAndRedirect('/approve', approveBtn));
//})();
