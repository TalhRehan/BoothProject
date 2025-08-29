// static/js/style.js
(function () {
  const container = document.getElementById('styleCards');
  const cards = Array.from(document.querySelectorAll('.style-card'));
  const generateBtn = document.getElementById('generateBtn');

  if (!container || cards.length === 0 || !generateBtn) return;

  // A11y roles (if not already set in HTML)
  container.setAttribute('role', 'radiogroup');
  container.setAttribute('aria-label', 'Sticker style');

  let selected = window.__preselectedStyle || null;
  let posting = false;

  function indexOfSelected() {
    return cards.findIndex(c => c.dataset.style === selected);
  }

  function applySelection({ focus = false } = {}) {
    cards.forEach((c, i) => {
      const isSel = c.dataset.style === selected;
      c.classList.toggle('selected', isSel);
      c.setAttribute('role', 'radio');
      c.setAttribute('aria-checked', String(isSel));
      // Roving tabindex: only the selected (or first) card is tab-focusable
      const shouldTab = isSel || (selected == null && i === 0);
      c.tabIndex = shouldTab ? 0 : -1;
      if (focus && isSel) c.focus();
    });
    generateBtn.disabled = !selected || posting;
    generateBtn.setAttribute('aria-disabled', String(generateBtn.disabled));
  }

  function selectStyle(style, opts) {
    if (!style) return;
    selected = style;
    applySelection(opts);
  }

  // Mouse / touch selection
  cards.forEach(card => {
    card.addEventListener('click', () => {
      if (posting) return;
      selectStyle(card.dataset.style, { focus: false });
    });
    // Keyboard: Space/Enter selects; Enter can also submit
    card.addEventListener('keydown', (e) => {
      if (posting) return;
      if (e.key === ' ' || e.key === 'Spacebar') {
        e.preventDefault();
        selectStyle(card.dataset.style, { focus: false });
      } else if (e.key === 'Enter') {
        e.preventDefault();
        selectStyle(card.dataset.style, { focus: false });
        if (!generateBtn.disabled) doPost();
      }
    });
  });

  // Arrow-key navigation across the cards
  container.addEventListener('keydown', (e) => {
    if (posting) return;
    const keys = ['ArrowLeft', 'ArrowUp', 'ArrowRight', 'ArrowDown'];
    if (!keys.includes(e.key)) return;

    e.preventDefault();
    const dir = (e.key === 'ArrowLeft' || e.key === 'ArrowUp') ? -1 : 1;
    let idx = indexOfSelected();
    if (idx < 0) {
      // nothing selected yet → start at first/last based on direction
      idx = (dir > 0) ? 0 : cards.length - 1;
    } else {
      idx = (idx + dir + cards.length) % cards.length;
    }
    const next = cards[idx];
    if (next) selectStyle(next.dataset.style, { focus: true });
  });

  async function doPost() {
    if (!selected || posting) return;
    posting = true;
    applySelection(); // disables button
    const prevLabel = generateBtn.textContent;
    generateBtn.textContent = 'Preparing…';

    try {
      const res = await fetch('/style', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ style: selected })
      });
      const json = await res.json();
      if (json.ok && json.redirect) {
        window.location.href = json.redirect;
      } else {
        alert('Failed to save style: ' + (json.error || 'Unknown error'));
      }
    } catch (e) {
      console.error(e);
      alert('Network error while saving style');
    } finally {
      // If we didn’t navigate, restore UI
      posting = false;
      generateBtn.textContent = prevLabel;
      applySelection();
    }
  }

  generateBtn.addEventListener('click', doPost);

  // Initial render (preselect if provided)
  applySelection();
})();
