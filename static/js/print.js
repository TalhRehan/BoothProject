// static/js/print.js
(async function () {
  const canvas = document.getElementById('a4Canvas');
  const preview = document.getElementById('a4Preview');
  const browserPrintBtn = document.getElementById('browserPrintBtn');
  const newSessionBtn = document.getElementById('newSessionBtn');

  if (!canvas || !preview) return;
  const ctx = canvas.getContext('2d');

  // Fetch the 4 approved images
  let dataUrls = [];
  try {
    const res = await fetch('/api/approved-list');
    const json = await res.json();
    if (!json.ok) throw new Error(json.error || 'Failed to load images.');
    dataUrls = json.data_urls || [];
    if (dataUrls.length !== 4) throw new Error('Expected 4 images.');
  } catch (err) {
    console.error(err);
    alert('Could not load images for printing. Returning to prompts.');
    window.location.href = '/multi';
    return;
  }

  // Compose: 2×2 grid with margins
  // A4 @ 300dpi: 2480 × 3508
  const W = canvas.width;  // 2480
  const H = canvas.height; // 3508

  // Safe margins ~5% of each dimension
  const marginX = Math.round(W * 0.05); // ~124
  const marginY = Math.round(H * 0.05); // ~175

  // gutter between cells
  const gutterX = Math.round(W * 0.04); // ~99
  const gutterY = Math.round(H * 0.04); // ~140

  // Cell size (2 columns, 2 rows)
  const cellW = Math.floor((W - (2 * marginX) - gutterX) / 2);
  const cellH = Math.floor((H - (2 * marginY) - gutterY) / 2);

  // White background
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(0, 0, W, H);

  // Load all images
  const imgs = await Promise.all(
    dataUrls.map(
      (src) =>
        new Promise((resolve, reject) => {
          const im = new Image();
          im.onload = () => resolve(im);
          im.onerror = reject;
          im.src = src;
        })
    )
  );

  // Draw preserving aspect ratio
  const positions = [
    [marginX, marginY],                                    // top-left
    [marginX + cellW + gutterX, marginY],                  // top-right
    [marginX, marginY + cellH + gutterY],                  // bottom-left
    [marginX + cellW + gutterX, marginY + cellH + gutterY] // bottom-right
  ];

  imgs.forEach((im, i) => {
    const [x, y] = positions[i];
    const scale = Math.min(cellW / im.width, cellH / im.height);
    const drawW = Math.round(im.width * scale);
    const drawH = Math.round(im.height * scale);
    const offsetX = x + Math.floor((cellW - drawW) / 2);
    const offsetY = y + Math.floor((cellH - drawH) / 2);
    ctx.drawImage(im, offsetX, offsetY, drawW, drawH);
  });

  // Update the screen preview
  const pngUrl = canvas.toDataURL('image/png');
  preview.src = pngUrl;

  // Print in browser
  if (browserPrintBtn) {
    browserPrintBtn.addEventListener('click', () => {
      window.print();
    });
  }

  // Start new session
  if (newSessionBtn) {
    newSessionBtn.addEventListener('click', async () => {
      try {
        const res = await fetch('/start-new', { method: 'POST' });
        const json = await res.json();
        if (json.ok && json.redirect) {
          window.location.href = json.redirect;
        } else {
          window.location.href = '/';
        }
      } catch {
        window.location.href = '/';
      }
    });
  }
})();
