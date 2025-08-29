// static/js/print.js — A4 compositor (2x2) + safe browser printing
(function () {
  const canvas = document.getElementById('a4Canvas');
  const preview = document.getElementById('a4Preview');
  const browserPrintBtn = document.getElementById('browserPrintBtn');
  const newSessionBtn = document.getElementById('newSessionBtn');

  if (!canvas || !preview) {
    console.error('Print: missing canvas or preview element.');
    return;
  }
  const ctx = canvas.getContext('2d');
  if (!ctx) {
    alert('Your browser does not support canvas printing.');
    return;
  }

  // A4 @ ≈300dpi
  const W = canvas.width;   // 2480
  const H = canvas.height;  // 3508

  // Useful conversions
  const PX_PER_MM_X = W / 210;   // ~11.81 px/mm
  const PX_PER_MM_Y = H / 297;   // ~11.81 px/mm

  // Layout (tweakable)
  const OUTER_MARGIN_MM = 15;      // white frame around content
  const INNER_MARGIN_MM = 6;       // sticker inset inside each cell
  const CUTLINE_WIDTH = 3;         // line thickness in px
  const CUT_TICK_MM = 6;           // edge tick length

  const OUTER_MARGIN_X = Math.round(OUTER_MARGIN_MM * PX_PER_MM_X);
  const OUTER_MARGIN_Y = Math.round(OUTER_MARGIN_MM * PX_PER_MM_Y);
  const INNER_MARGIN_X = Math.round(INNER_MARGIN_MM * PX_PER_MM_X);
  const INNER_MARGIN_Y = Math.round(INNER_MARGIN_MM * PX_PER_MM_Y);
  const TICK_X = Math.round(CUT_TICK_MM * PX_PER_MM_X);
  const TICK_Y = Math.round(CUT_TICK_MM * PX_PER_MM_Y);

  function drawDottedLine(x1, y1, x2, y2) {
    ctx.save();
    ctx.setLineDash([16, 14]);
    ctx.lineWidth = CUTLINE_WIDTH;
    ctx.strokeStyle = '#9aa6b2';
    ctx.beginPath();
    ctx.moveTo(x1 + 0.5, y1 + 0.5);
    ctx.lineTo(x2 + 0.5, y2 + 0.5);
    ctx.stroke();
    ctx.restore();
  }

  function drawCutTicks(midX, midY) {
    ctx.save();
    ctx.setLineDash([]);
    ctx.lineWidth = CUTLINE_WIDTH;
    ctx.strokeStyle = '#7f8a96';

    // Ticks at midpoints (top/bottom)
    ctx.beginPath();
    ctx.moveTo(midX + 0.5, 0);
    ctx.lineTo(midX + 0.5, TICK_Y);
    ctx.moveTo(midX + 0.5, H - TICK_Y);
    ctx.lineTo(midX + 0.5, H);
    ctx.stroke();

    // Ticks at midpoints (left/right)
    ctx.beginPath();
    ctx.moveTo(0, midY + 0.5);
    ctx.lineTo(TICK_X, midY + 0.5);
    ctx.moveTo(W - TICK_X, midY + 0.5);
    ctx.lineTo(W, midY + 0.5);
    ctx.stroke();
    ctx.restore();
  }

  function fitContain(imgW, imgH, boxW, boxH) {
    const r = Math.min(boxW / imgW, boxH / imgH);
    return { w: Math.round(imgW * r), h: Math.round(imgH * r) };
  }

  async function loadApprovedDataUrl() {
    const r = await fetch('/api/approved');
    const j = await r.json();
    if (!j.ok) throw new Error(j.error || 'missing approved image');
    return j.data_url;
  }

  async function compose() {
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';

    // Solid white sheet—prevents “black pages” on some drivers
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, W, H);

    // Load approved (works in both DEV & PROD flows)
    const dataUrl = await loadApprovedDataUrl();
    const img = new Image();
    img.src = dataUrl;
    await img.decode();

    // Inner printable region
    const innerW = W - 2 * OUTER_MARGIN_X;
    const innerH = H - 2 * OUTER_MARGIN_Y;

    // 2 × 2 cells, no gap
    const cellW = Math.floor(innerW / 2);
    const cellH = Math.floor(innerH / 2);

    const cells = [
      { x: OUTER_MARGIN_X,            y: OUTER_MARGIN_Y            },
      { x: OUTER_MARGIN_X + cellW,    y: OUTER_MARGIN_Y            },
      { x: OUTER_MARGIN_X,            y: OUTER_MARGIN_Y + cellH    },
      { x: OUTER_MARGIN_X + cellW,    y: OUTER_MARGIN_Y + cellH    },
    ];

    // Draw the four stickers, contained within each cell with inner padding
    cells.forEach(({ x, y }) => {
      const bx = x + INNER_MARGIN_X;
      const by = y + INNER_MARGIN_Y;
      const bw = cellW - 2 * INNER_MARGIN_X;
      const bh = cellH - 2 * INNER_MARGIN_Y;

      const { w, h } = fitContain(img.width, img.height, bw, bh);
      const ox = bx + Math.round((bw - w) / 2);
      const oy = by + Math.round((bh - h) / 2);
      ctx.drawImage(img, ox, oy, w, h);
    });

    // Dotted cut lines (full width/height)
    const midX = OUTER_MARGIN_X + cellW;
    const midY = OUTER_MARGIN_Y + cellH;
    drawDottedLine(midX, OUTER_MARGIN_Y, midX, OUTER_MARGIN_Y + 2 * cellH);
    drawDottedLine(OUTER_MARGIN_X, midY, OUTER_MARGIN_X + 2 * cellW, midY);

    // Edge ticks to help align the cut at sheet edges
    drawCutTicks(midX, midY);

    // Update preview (high-res PNG)
    const sheetUrl = canvas.toDataURL('image/png');
    preview.src = sheetUrl;
    return sheetUrl;
  }

  // Actions
  browserPrintBtn?.addEventListener('click', async () => {
    browserPrintBtn.disabled = true;
    try {
      if (!preview.src) await compose();
      window.print(); // print CSS shows only .print-sheet
    } catch (e) {
      alert('Print failed. Returning to result.');
      window.location.href = '/result';
    } finally {
      browserPrintBtn.disabled = false;
    }
  });

  newSessionBtn?.addEventListener('click', async () => {
    newSessionBtn.disabled = true;
    try {
      const res = await fetch('/start-new', { method: 'POST' });
      const json = await res.json();
      if (json.ok) window.location.href = json.redirect;
    } catch {
      newSessionBtn.disabled = false;
    }
  });

  // Re-compose right before printing (some browsers rasterize at print-time)
  window.addEventListener('beforeprint', async () => {
    try { await compose(); } catch {}
  });

  // Optional: clear session after printing
  window.addEventListener('afterprint', async () => {
    try { await fetch('/start-new', { method: 'POST' }); } catch {}
  });

  // Bootstrap
  (async function init() {
    try {
      await compose();
    } catch (e) {
      alert('No approved image found. Returning to result.');
      window.location.href = '/result';
    }
  })();
})();
