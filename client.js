/* Fugleramme for Tesserae: the page is drawn on the server by Fugleramme's own renderer at the
   cell's pixel size (server.py, /plugins/fugleramme/page.png) and shown here as one picture, with
   the credit for the plates on a line beneath it. render() waits for the picture to decode, so the
   panel's screenshot never catches the page half loaded. */

const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

const PANEL_PAPER = '#e9e1ca';
const INK = '#1e1e1e';

const CSS = `
:host { display: block; width: 100%; height: 100%; font-family: inherit; }
.fr { position: relative; width: 100%; height: 100%; overflow: hidden; display: flex; flex-direction: column; color: ${INK}; }
.pg { display: block; width: 100%; flex: 1 1 auto; min-height: 0; object-fit: fill; }
.cr { flex: 0 0 auto; display: flex; align-items: center; justify-content: flex-end; padding: 0 0.6em; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; font-style: italic; line-height: 1; }
.cr span { overflow: hidden; text-overflow: ellipsis; }
.msg { flex: 1 1 auto; display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; padding: 8%; gap: 0.5em; }
.msg-t { font-size: clamp(14px, 4.2cqmin, 30px); font-style: italic; font-weight: 600; }
.msg-b { font-size: clamp(11px, 2.8cqmin, 18px); max-width: 32em; }
`;

function wrap(paper, body) {
  return `<style>${CSS}</style><div class="fr" style="background:${esc(paper || PANEL_PAPER)};container-type:size">${body}</div>`;
}

function credit(d, px) {
  const size = Math.max(9, Math.round(px * 0.62));
  return `<div class="cr" style="height:${px}px;font-size:${size}px"><span>${esc(d.credit || 'Plates from Fugleramme, CC BY-SA 4.0')}</span></div>`;
}

/* The picture's bitmap, ready before render() returns, or given up on after a while. */
function decoded(img, ms) {
  const wait = new Promise((resolve) => setTimeout(resolve, ms));
  const ready = typeof img.decode === 'function'
    ? img.decode().catch(() => {})
    : new Promise((resolve) => { img.onload = resolve; img.onerror = resolve; });
  return Promise.race([ready, wait]);
}

export default async function render(shadow, ctx) {
  const d = (ctx && ctx.data) || {};
  if (d.error || !d.job) {
    const body = d.error || 'Nothing to show yet.';
    shadow.innerHTML = wrap(d.paper, `<div class="msg"><div class="msg-t">Fugleramme</div><div class="msg-b">${esc(body)}</div></div>`);
    return;
  }
  const w = Math.max(16, Math.round(d.cell_w || (ctx.cell && ctx.cell.w) || 800));
  const h = Math.max(16, Math.round(d.cell_h || (ctx.cell && ctx.cell.h) || 480));
  /* The plates are CC BY-SA: their credit line is always under the page. */
  const strip = d.strip || Math.max(12, Math.min(22, Math.round(Math.min(w, h) * 0.026)));
  const url = new URL('./page.png', import.meta.url);
  url.searchParams.set('job', d.job);
  /* Drawn at the screen's own pixels, so a dense display gets a sharp page rather than a
     stretched one; a panel renders at one pixel per pixel and gets exactly its size. */
  const dpr = Math.max(1, Math.min(2, Number(globalThis.devicePixelRatio) || 1));
  url.searchParams.set('w', String(Math.round(w * dpr)));
  url.searchParams.set('h', String(Math.round(Math.max(16, h - strip) * dpr)));
  shadow.innerHTML = wrap(d.paper, `<img class="pg" alt="" src="${esc(url.href)}">${credit(d, strip)}`);
  /* A narrow cell keeps the licence in view: the artists give way first. */
  const line = shadow.querySelector('.cr span');
  if (line && line.scrollWidth > line.clientWidth + 1) line.textContent = d.credit_short || 'Plates from Fugleramme, CC BY-SA 4.0';
  const img = shadow.querySelector('img');
  if (img) await decoded(img, 30000);
}
