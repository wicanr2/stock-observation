/* 股票監控儀表板：只讀 data/*.json，不呼叫第三方 API。
 * 所有外部字串（新聞標題、機構名稱等）一律以 textContent 寫入，不使用 innerHTML。 */
(() => {
  'use strict';

  const REFRESH_MS = 5 * 60 * 1000;
  const TZ = 'Asia/Taipei';
  const NA = '本次未取得';
  const RANGES = [
    { key: '1M', label: '1 個月', days: 21 },
    { key: '3M', label: '3 個月', days: 63 },
    { key: '6M', label: '6 個月', days: 126 },
    { key: '1Y', label: '1 年', days: 252 },
  ];
  const state = { dash: null, live: null, reports: null, sel: null, range: '6M', checkedAt: null, error: null };

  // ------------------------------------------------------------------ DOM 工具
  const SVGNS = 'http://www.w3.org/2000/svg';
  function el(tag, props, ...kids) {
    const n = document.createElement(tag);
    setProps(n, props);
    append(n, kids);
    return n;
  }
  function sv(tag, props, ...kids) {
    const n = document.createElementNS(SVGNS, tag);
    setProps(n, props);
    append(n, kids);
    return n;
  }
  function setProps(n, props) {
    for (const [k, v] of Object.entries(props || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') n.setAttribute('class', v);
      else if (k === 'text') n.textContent = v;
      else if (k.startsWith('on') && typeof v === 'function') n.addEventListener(k.slice(2), v);
      else if (k === 'style' && typeof v === 'object') Object.assign(n.style, v);
      else n.setAttribute(k, v === true ? '' : String(v));
    }
  }
  function append(n, kids) {
    for (const c of kids.flat(Infinity)) {
      if (c === null || c === undefined || c === false) continue;
      n.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
  }
  function safeUrl(u) {
    if (!u || typeof u !== 'string') return null;
    try {
      const url = new URL(u, location.href);
      return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : null;
    } catch (_) { return null; }
  }
  function link(text, url) {
    const href = safeUrl(url);
    return href ? el('a', { href, target: '_blank', rel: 'noopener noreferrer' }, text) : el('span', {}, text);
  }

  // ------------------------------------------------------------------ 格式
  const nf0 = new Intl.NumberFormat('zh-TW', { maximumFractionDigits: 0 });
  const nf2 = new Intl.NumberFormat('zh-TW', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
  const fInt = (v) => (isNum(v) ? nf0.format(Math.round(v)) : '—');
  const fSigned = (v) => (isNum(v) ? (v > 0 ? '+' : v < 0 ? '−' : '') + nf0.format(Math.abs(Math.round(v))) : '—');
  const fPrice = (v) => (isNum(v) ? nf2.format(v) : '—');
  function fPct(v, digits = 1, signed = true) {
    if (!isNum(v)) return '—';
    const s = (Math.abs(v) * 100).toFixed(digits) + '%';
    if (!signed) return (v < 0 ? '−' : '') + s;
    return (v > 0 ? '+' : v < 0 ? '−' : '±') + s;
  }
  function arrow(v) { return !isNum(v) ? '' : v > 0 ? '▲ ' : v < 0 ? '▼ ' : '■ '; }
  function compact(v) {
    if (!isNum(v)) return '—';
    const a = Math.abs(v);
    if (a >= 1e8) return (v / 1e8).toFixed(a >= 1e9 ? 0 : 1) + ' 億';
    if (a >= 1e4) return (v / 1e4).toFixed(a >= 1e5 ? 0 : 1) + ' 萬';
    return nf0.format(v);
  }
  function fDate(s) { return s ? String(s).slice(0, 10) : '—'; }
  function fDateTime(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso);
    return new Intl.DateTimeFormat('zh-TW', { timeZone: TZ, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(d);
  }
  const currencyLabel = (s) => (s.currency === 'TWD' ? '新台幣' : '美元');

  // ------------------------------------------------------------------ 資料載入
  async function getJson(path, optional) {
    const res = await fetch(`${path}?t=${Date.now()}`, { cache: 'no-store' });
    if (!res.ok) {
      if (optional) return null;
      throw new Error(`${path} HTTP ${res.status}`);
    }
    return res.json();
  }

  async function load() {
    const app = document.getElementById('app');
    app.classList.add('is-loading');
    try {
      const [dash, live, reports] = await Promise.all([
        getJson('data/dashboard.json', false),
        getJson('data/live.json', true).catch(() => null),
        getJson('data/reports.json', true).catch(() => null),
      ]);
      state.dash = dash; state.live = live; state.reports = reports; state.error = null;
    } catch (err) {
      state.error = err;
    } finally {
      state.checkedAt = new Date();
      app.classList.remove('is-loading');
      render();
    }
  }

  function stocks() { return (state.dash && state.dash.stocks) || []; }
  function liveFor(id) { return state.live && state.live.stocks ? state.live.stocks[id] : null; }
  function selected() {
    const list = stocks();
    return list.find((s) => s.id === state.sel) || list[0] || null;
  }
  function repoUrl(path) {
    if (!path) return null;
    const repo = (state.reports && state.reports.repo) || (state.dash && state.dash.repo) || 'wicanr2/stock-observation';
    const branch = (state.reports && state.reports.branch) || 'main';
    return `https://github.com/${repo}/blob/${branch}/${path}`;
  }

  // ------------------------------------------------------------------ 圖表：共用
  function niceTicks(min, max, count = 4) {
    if (!isNum(min) || !isNum(max)) return [];
    if (min === max) { const pad = Math.abs(min) * 0.05 || 1; min -= pad; max += pad; }
    const span = max - min;
    const step0 = Math.pow(10, Math.floor(Math.log10(span / count)));
    const err = (count / span) * step0;
    const step = step0 * (err <= 0.15 ? 10 : err <= 0.35 ? 5 : err <= 0.75 ? 2 : 1);
    const ticks = [];
    for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) ticks.push(+v.toFixed(10));
    return ticks;
  }

  function responsive(wrap, draw) {
    let last = 0;
    const ro = new ResizeObserver((entries) => {
      if (!wrap.isConnected) { ro.disconnect(); return; }
      const w = Math.round(entries[0].contentRect.width);
      if (w > 0 && w !== last) { last = w; draw(w); }
    });
    ro.observe(wrap);
  }

  function tooltip(wrap) {
    const tt = el('div', { class: 'tooltip', hidden: true, role: 'presentation' });
    wrap.append(tt);
    return {
      show(x, y, title, rows) {
        tt.replaceChildren(el('div', { class: 'tt-title', text: title }),
          ...rows.map((r) => el('div', { class: 'tt-row' },
            r.color ? el('span', { class: 'swatch-line', style: { color: r.color } }) : null,
            el('strong', { text: r.value }), el('span', { class: 'name', text: r.name }))));
        tt.hidden = false;
        const ww = wrap.clientWidth;
        const tw = tt.offsetWidth;
        const left = x + 14 + tw > ww ? Math.max(0, x - tw - 14) : x + 14;
        tt.style.left = `${left}px`;
        tt.style.top = `${Math.max(0, y - 10)}px`;
      },
      hide() { tt.hidden = true; },
    };
  }

  function figure({ title, sub, legend, chart, table }) {
    const tableBox = el('div', { class: 'table-wrap', hidden: true }, table);
    const toggle = el('button', {
      type: 'button', class: 'table-toggle', 'aria-expanded': 'false',
      onclick: () => {
        const show = tableBox.hidden;
        tableBox.hidden = !show;
        chart.hidden = show;
        toggle.textContent = show ? '以圖表檢視' : '以表格檢視';
        toggle.setAttribute('aria-expanded', String(show));
      },
    }, '以表格檢視');
    return el('figure', { class: 'figure' },
      el('div', { class: 'figure-head' },
        el('div', {}, el('div', { class: 'figure-title', text: title }), sub ? el('div', { class: 'figure-sub', text: sub }) : null),
        table ? toggle : null),
      legend || null, chart, table ? tableBox : null);
  }

  function legendLine(items) {
    if (items.length < 2) return null;
    return el('div', { class: 'legend' }, items.map((it) =>
      el('span', { class: 'key' },
        it.kind === 'rect'
          ? el('span', { class: 'swatch-rect', style: { background: it.color } })
          : el('span', { class: 'swatch-line', style: { color: it.color } }),
        it.name)));
  }

  function simpleTable(headers, rows, numCols = []) {
    return el('table', {},
      el('thead', {}, el('tr', {}, headers.map((h, i) => el('th', { class: numCols.includes(i) ? 'num' : null, scope: 'col', text: h })))),
      el('tbody', {}, rows.map((r) => el('tr', {}, r.map((c, i) => el('td', { class: numCols.includes(i) ? 'num' : null }, c))))));
  }

  // ------------------------------------------------------------------ 圖表：折線（以交易日序為 X）
  function lineChart({ points, series, height = 240, yFmt = fPrice, xFmt = (l) => l.slice(5).replace('-', '/'), ariaLabel, markers = [] }) {
    // points: [{label}]；series: [{name, color, width, values: number[] (與 points 對齊), main}]
    const wrap = el('div', { class: 'chart' });
    const tt = tooltip(wrap);
    const svg = sv('svg', { role: 'img', 'aria-label': ariaLabel, tabindex: '0', height });
    wrap.prepend(svg);
    let geom = null;
    let active = -1;

    function draw(width) {
      const m = { l: 52, r: 64, t: 10, b: 24 };
      const W = Math.max(240, width);
      const iw = W - m.l - m.r;
      const ih = height - m.t - m.b;
      svg.setAttribute('viewBox', `0 0 ${W} ${height}`);
      svg.replaceChildren();
      const all = series.flatMap((s) => s.values.filter(isNum));
      if (!all.length || points.length < 2) {
        svg.append(sv('text', { x: m.l, y: height / 2, class: 'axis-label' }, '資料不足，無法繪圖'));
        return;
      }
      let lo = Math.min(...all), hi = Math.max(...all);
      const pad = (hi - lo) * 0.06 || Math.abs(hi) * 0.02 || 1;
      lo -= pad; hi += pad;
      const ticks = niceTicks(lo, hi, 4).filter((t) => t >= lo && t <= hi);
      const n = points.length;
      const x = (i) => m.l + (n === 1 ? iw / 2 : (i / (n - 1)) * iw);
      const y = (v) => m.t + ih - ((v - lo) / (hi - lo)) * ih;
      geom = { x, y, m, iw, ih, W, n };

      const g = sv('g');
      for (const t of ticks) {
        g.append(sv('line', { class: 'gridline', x1: m.l, x2: m.l + iw, y1: y(t), y2: y(t) }));
        g.append(sv('text', { class: 'axis-label', x: m.l - 8, y: y(t) + 4, 'text-anchor': 'end' }, yFmt(t)));
      }
      g.append(sv('line', { class: 'baseline', x1: m.l, x2: m.l + iw, y1: m.t + ih, y2: m.t + ih }));
      // X 軸：依寬度決定標籤數（約每 80px 一個），首尾貼齊邊界避免溢出
      const maxLabels = Math.max(2, Math.floor(iw / 80));
      const step = Math.max(1, Math.ceil((n - 1) / (maxLabels - 1)));
      const xs = [];
      for (let i = 0; i < n; i += step) xs.push(i);
      if (xs[xs.length - 1] !== n - 1) {
        if (n - 1 - xs[xs.length - 1] < step * 0.6 && xs.length > 1) xs.pop();
        xs.push(n - 1);
      }
      for (const i of xs) {
        const anchor = i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle';
        g.append(sv('text', { class: 'axis-label', x: x(i), y: height - 6, 'text-anchor': anchor }, xFmt(points[i].label)));
      }
      for (const mk of markers) {
        if (mk.index < 0 || mk.index >= n) continue;
        const mx = x(mk.index);
        const right = mx > m.l + iw * 0.55;
        g.append(sv('line', { class: 'crosshair', x1: mx, x2: mx, y1: m.t, y2: m.t + ih, opacity: '0.5' }));
        g.append(sv('text', { class: 'marker-label', x: right ? mx - 4 : mx + 4, y: m.t + 12, 'text-anchor': right ? 'end' : 'start' }, mk.label));
      }
      const pathOf = (vals) => {
        let d = '';
        vals.forEach((v, i) => { if (isNum(v)) d += `${d && isNum(vals[i - 1]) ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`; });
        return d;
      };
      // 主序列的淡色面積先畫，線條在上；主序列最後畫，位於最上層
      const main = series.find((s) => s.main) || series[0];
      const mainPath = pathOf(main.values);
      const first = main.values.findIndex(isNum);
      const lastIdx = main.values.length - 1 - main.values.slice().reverse().findIndex(isNum);
      if (first >= 0 && mainPath) {
        g.append(sv('path', { d: `${mainPath}L${x(lastIdx).toFixed(1)},${(m.t + ih).toFixed(1)}L${x(first).toFixed(1)},${(m.t + ih).toFixed(1)}Z`, fill: 'var(--series-1-wash)', stroke: 'none' }));
      }
      for (const s of series.filter((x0) => x0 !== main).reverse().concat([main])) {
        g.append(sv('path', { d: s === main ? mainPath : pathOf(s.values), fill: 'none', stroke: s.color, 'stroke-width': s.width || 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
      }
      // 主序列端點與數值標籤
      const lastI = main.values.length - 1;
      if (isNum(main.values[lastI])) {
        g.append(sv('circle', { cx: x(lastI), cy: y(main.values[lastI]), r: 4, fill: main.color, stroke: 'var(--surface)', 'stroke-width': 2 }));
        g.append(sv('text', { class: 'end-label', x: x(lastI) + 8, y: y(main.values[lastI]) + 4 }, yFmt(main.values[lastI])));
      }
      const cross = sv('line', { class: 'crosshair', y1: m.t, y2: m.t + ih, visibility: 'hidden' });
      const dots = series.map((s) => sv('circle', { r: 4, fill: s.color, stroke: 'var(--surface)', 'stroke-width': 2, visibility: 'hidden' }));
      g.append(cross, ...dots);
      geom.cross = cross; geom.dots = dots;
      svg.append(g);
      if (active >= 0) show(active);
    }

    function show(i) {
      if (!geom) return;
      active = Math.max(0, Math.min(geom.n - 1, i));
      const px = geom.x(active);
      geom.cross.setAttribute('x1', px); geom.cross.setAttribute('x2', px);
      geom.cross.setAttribute('visibility', 'visible');
      series.forEach((s, k) => {
        const v = s.values[active];
        if (isNum(v)) {
          geom.dots[k].setAttribute('cx', px); geom.dots[k].setAttribute('cy', geom.y(v));
          geom.dots[k].setAttribute('visibility', 'visible');
        } else geom.dots[k].setAttribute('visibility', 'hidden');
      });
      const scale = wrap.clientWidth / geom.W;
      const mainV = series[0].values[active];
      tt.show(px * scale, (isNum(mainV) ? geom.y(mainV) : geom.m.t) * scale, points[active].label,
        series.map((s) => ({ name: s.name, value: yFmt(s.values[active]), color: s.color })));
    }
    function hide() {
      active = -1; tt.hide();
      if (geom && geom.cross) { geom.cross.setAttribute('visibility', 'hidden'); geom.dots.forEach((d) => d.setAttribute('visibility', 'hidden')); }
    }
    svg.addEventListener('pointermove', (e) => {
      if (!geom) return;
      const r = svg.getBoundingClientRect();
      const px = ((e.clientX - r.left) / r.width) * geom.W;
      show(Math.round(((px - geom.m.l) / geom.iw) * (geom.n - 1)));
    });
    svg.addEventListener('pointerleave', hide);
    svg.addEventListener('blur', hide);
    svg.addEventListener('keydown', (e) => {
      if (!geom) return;
      if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
        e.preventDefault();
        show((active < 0 ? geom.n - 1 : active) + (e.key === 'ArrowRight' ? 1 : -1));
      } else if (e.key === 'Escape') hide();
    });
    responsive(wrap, draw);
    return wrap;
  }

  // ------------------------------------------------------------------ 圖表：正負柱（買超／賣超）
  function divergingBars({ items, height = 150, ariaLabel }) {
    // items: [{label, value}]；正值＝買超（類別色 1），負值＝賣超（類別色 2）
    const wrap = el('div', { class: 'chart' });
    const tt = tooltip(wrap);
    const svg = sv('svg', { role: 'img', 'aria-label': ariaLabel, height });
    wrap.prepend(svg);

    function barPath(x, y0, y1, w, r) {
      const top = Math.min(y0, y1), bottom = Math.max(y0, y1), h = bottom - top;
      if (h < 0.5) return `M${x},${y0}h${w}`;
      const rr = Math.min(r, h, w / 2);
      if (y1 < y0) { // 往上長：圓角在上
        return `M${x},${bottom}V${top + rr}Q${x},${top} ${x + rr},${top}H${x + w - rr}Q${x + w},${top} ${x + w},${top + rr}V${bottom}Z`;
      }
      return `M${x},${top}V${bottom - rr}Q${x},${bottom} ${x + rr},${bottom}H${x + w - rr}Q${x + w},${bottom} ${x + w},${bottom - rr}V${top}Z`;
    }

    function draw(width) {
      const m = { l: 52, r: 8, t: 8, b: 22 };
      const W = Math.max(220, width);
      const iw = W - m.l - m.r;
      const ih = height - m.t - m.b;
      svg.setAttribute('viewBox', `0 0 ${W} ${height}`);
      svg.replaceChildren();
      const vals = items.map((d) => d.value).filter(isNum);
      if (!vals.length) {
        svg.append(sv('text', { x: m.l, y: height / 2, class: 'axis-label' }, '尚無資料'));
        return;
      }
      const maxAbs = Math.max(...vals.map(Math.abs)) || 1;
      const lo = Math.min(0, ...vals), hi = Math.max(0, ...vals);
      const span = (hi - lo) || maxAbs;
      const y = (v) => m.t + ih - ((v - lo) / span) * ih;
      const ticks = niceTicks(lo, hi, 3).filter((t) => t >= lo && t <= hi);
      for (const t of ticks) {
        svg.append(sv('line', { class: 'gridline', x1: m.l, x2: m.l + iw, y1: y(t), y2: y(t) }));
        svg.append(sv('text', { class: 'axis-label', x: m.l - 6, y: y(t) + 4, 'text-anchor': 'end' }, compact(t)));
      }
      const band = iw / items.length;
      const bw = Math.max(1, Math.min(24, band - 2));
      const bars = [];
      items.forEach((d, i) => {
        if (!isNum(d.value)) return;
        const x0 = m.l + i * band + (band - bw) / 2;
        const color = d.value >= 0 ? 'var(--series-1)' : 'var(--series-2)';
        const p = sv('path', { class: 'bar', d: barPath(x0, y(0), y(d.value), bw, 4), fill: color });
        const hit = sv('rect', { x: m.l + i * band, y: m.t, width: band, height: ih, fill: 'transparent', tabindex: '0',
          'aria-label': `${d.label}：${d.value >= 0 ? '買超' : '賣超'} ${fInt(Math.abs(d.value))} 股` });
        const on = () => {
          bars.forEach((b) => b.classList.toggle('dim', b !== p));
          const scale = wrap.clientWidth / W;
          tt.show((x0 + bw) * scale, y(Math.max(d.value, 0)) * scale, d.label,
            [{ name: d.value >= 0 ? '買超（股）' : '賣超（股）', value: fSigned(d.value), color }]);
        };
        const off = () => { bars.forEach((b) => b.classList.remove('dim')); tt.hide(); };
        hit.addEventListener('pointerenter', on); hit.addEventListener('focus', on);
        hit.addEventListener('pointerleave', off); hit.addEventListener('blur', off);
        bars.push(p);
        svg.append(p, hit);
      });
      svg.append(sv('line', { class: 'baseline', x1: m.l, x2: m.l + iw, y1: y(0), y2: y(0) }));
      const lab = (i, anchor) => svg.append(sv('text', { class: 'axis-label', x: m.l + i * band + band / 2, y: height - 6, 'text-anchor': anchor }, items[i].label.slice(5)));
      lab(0, 'start');
      if (items.length > 1) lab(items.length - 1, 'end');
    }
    responsive(wrap, draw);
    return wrap;
  }

  function sparkline(values) {
    const vals = values.filter(isNum);
    const W = 120, H = 34;
    const svg = sv('svg', { width: W, height: H, viewBox: `0 0 ${W} ${H}`, 'aria-hidden': 'true' });
    if (vals.length < 2) return svg;
    const lo = Math.min(...vals), hi = Math.max(...vals);
    const x = (i) => 2 + (i / (vals.length - 1)) * (W - 8);
    const y = (v) => 3 + (H - 6) * (1 - (v - lo) / ((hi - lo) || 1));
    svg.append(sv('path', { d: vals.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(''), fill: 'none', stroke: 'var(--context-2)', 'stroke-width': 1.5, 'stroke-linejoin': 'round' }));
    svg.append(sv('circle', { cx: x(vals.length - 1), cy: y(vals[vals.length - 1]), r: 3.5, fill: 'var(--series-1)', stroke: 'var(--surface)', 'stroke-width': 2 }));
    return svg;
  }

  function movingAverage(vals, n) {
    const out = new Array(vals.length).fill(null);
    let sum = 0;
    for (let i = 0; i < vals.length; i++) {
      sum += vals[i];
      if (i >= n) sum -= vals[i - n];
      if (i >= n - 1) out[i] = sum / n;
    }
    return out;
  }

  // ------------------------------------------------------------------ 共用小元件
  function tile(label, value, sub) {
    const long = String(value).length > 9;
    return el('div', { class: 'tile' }, el('div', { class: 'tile-label', text: label }), el('div', { class: long ? 'tile-value long' : 'tile-value', text: value }), sub ? el('div', { class: 'tile-sub' }, sub) : null);
  }
  function srcLine(src, prefix) {
    if (!src) return null;
    const kind = src.secondary ? '次級' : '一手';
    return el('p', { class: 'note' }, prefix ? `${prefix}：` : '', link(src.source || '來源', src.url), `（${kind}）`, src.ok ? '' : `；${NA}：${src.error || ''}`, src.ok && src.note ? `；${src.note}` : '');
  }
  function stateBadge(ok) {
    return el('span', { class: `state ${ok ? 'ok' : 'fail'}` }, el('span', { class: 'txt', text: ok ? '成功' : '失敗' }));
  }
  function card(title, lead, ...kids) {
    return el('section', { class: 'card' }, el('h2', { text: title }), lead ? el('p', { class: 'lead', text: lead }) : null, kids);
  }

  // ------------------------------------------------------------------ 總覽
  function quoteOf(s) {
    const lv = liveFor(s.id);
    const q = lv && lv.quote;
    const dsc = (s.price && s.price.describe) || null;
    if (q && isNum(q.price)) return { price: q.price, delta: q.change_pct, when: `盤中 ${fDateTime(q.as_of)}（延遲）` };
    if (dsc) return { price: dsc.last_close, delta: dsc.ret_1d, when: `收盤 ${fDate(dsc.last_date)}` };
    return null;
  }

  function instSummary(s) {
    if (s.us) {
      const tf = s.us.thirteen_f;
      if (!tf || !tf.ok) return `13F ${NA}`;
      const pos = tf.positions || [];
      if (!pos.length) return '期間內無含本股的新 13F';
      const a = pos.filter((p) => p.class.A).length, b = pos.filter((p) => p.class.B).length;
      return `新 13F ${pos.length} 份（多方腿 ${a}、Put ${b}）`;
    }
    if (s.tw) {
      const h = (s.tw.history || []).slice(-5);
      if (!h.length) return `三大法人 ${NA}`;
      return `三大法人近 ${h.length} 日 ${fSigned(h.reduce((a, r) => a + (r.total_net || 0), 0))} 股`;
    }
    return '—';
  }

  function predSummary(s) {
    const p = s.prediction;
    if (!p || !p.ok) return `— ／ ${(p && p.reason) || NA}`;
    return `${Math.round(p.prob_up * 100)}% ／ ${p.signal}`;
  }

  function renderOverview() {
    const box = document.getElementById('overview');
    const cur = selected();
    box.replaceChildren(...stocks().map((s) => {
      const q = quoteOf(s);
      const series = ((s.price && s.price.series) || []).slice(-60).map((r) => r.c);
      return el('button', {
        type: 'button', class: 'ov', 'aria-current': cur && cur.id === s.id ? 'true' : 'false',
        onclick: () => select(s.id),
      },
        el('div', { class: 'ov-head' }, el('span', { class: 'ov-name', text: s.market === 'TW' ? s.name : `${s.id} ${s.name}` }), el('span', { class: 'ov-code', text: `${s.exchange} · ${s.yahoo}` })),
        el('div', { class: 'ov-price' },
          el('div', {},
            el('div', { class: 'ov-value', text: q ? fPrice(q.price) : NA }),
            el('div', { class: 'ov-delta', text: q ? `${arrow(q.delta)}${fPct(q.delta, 2)} · ${q.when}` : '' })),
          sparkline(series)),
        el('dl', { class: 'ov-meta' },
          el('dt', { text: '統計模型' }), el('dd', { text: predSummary(s) }),
          el('dt', { text: 'LLM' }), el('dd', { text: s.dsa ? `${s.dsa.signal}／${s.dsa.trend}` : '未啟用' }),
          el('dt', { text: '機構' }), el('dd', { text: instSummary(s) })));
    }));
  }

  function renderTabs() {
    const nav = document.getElementById('tabs');
    const cur = selected();
    nav.replaceChildren(...stocks().map((s) => el('button', {
      type: 'button', class: 'tab', 'aria-selected': cur && cur.id === s.id ? 'true' : 'false',
      onclick: () => select(s.id),
    }, s.market === 'TW' ? `${s.name}（${s.id}）` : s.id)));
  }

  function select(id) {
    state.sel = id;
    if (location.hash !== `#${id}`) history.replaceState(null, '', `#${encodeURIComponent(id)}`);
    render();
    document.getElementById('detail').focus({ preventScroll: true });
  }

  // ------------------------------------------------------------------ 個股：價格
  function priceSection(s) {
    const pr = s.price || {};
    const d = pr.describe;
    if (!pr.ok || !d) return card('價格走勢', null, el('p', { class: 'empty', text: `價格資料${NA}（${pr.error || ''}）` }));
    const lv = liveFor(s.id);
    const q = lv && lv.quote;
    const tiles = el('div', { class: 'tiles' },
      tile('最後收盤', `${fPrice(d.last_close)}`, `${currencyLabel(s)}｜${fDate(d.last_date)}`),
      q ? tile('盤中報價（延遲）', fPrice(q.price), `${arrow(q.change_pct)}${fPct(q.change_pct, 2)}｜${fDateTime(q.as_of)}`) : null,
      tile('1 日', `${arrow(d.ret_1d)}${fPct(d.ret_1d)}`),
      tile('5 日', `${arrow(d.ret_5d)}${fPct(d.ret_5d)}`),
      tile('20 日', `${arrow(d.ret_20d)}${fPct(d.ret_20d)}`),
      tile('相對 MA20／MA60', `${fPct(d.ma20_gap)}／${fPct(d.ma60_gap)}`),
      tile('RSI14', isNum(d.rsi14) ? d.rsi14.toFixed(1) : '—'));

    const rangeRow = el('div', { class: 'filters', role: 'group', 'aria-label': '期間' }, el('span', { class: 'label', text: '期間' }),
      RANGES.map((r) => el('button', { type: 'button', class: 'btn', 'aria-pressed': state.range === r.key ? 'true' : 'false', onclick: () => { state.range = r.key; render(); } }, r.label)));

    const all = pr.series || [];
    const closes = all.map((r) => r.c);
    const ma20 = movingAverage(closes, 20), ma60 = movingAverage(closes, 60);
    const days = (RANGES.find((r) => r.key === state.range) || RANGES[2]).days;
    const start = Math.max(0, all.length - days);
    const pts = all.slice(start).map((r) => ({ label: r.d }));
    const markers = [];
    if (pr.spliced_at) {
      const idx = all.slice(start).findIndex((r) => r.d >= fDate(pr.spliced_at));
      if (idx > 0) markers.push({ index: idx, label: `${s.id} 掛牌（之前為舊代碼）` });
    }
    const series = [
      { name: '收盤', color: 'var(--series-1)', width: 2, values: closes.slice(start), main: true },
      { name: 'MA20', color: 'var(--context-1)', width: 1.5, values: ma20.slice(start) },
      { name: 'MA60', color: 'var(--context-2)', width: 1.5, values: ma60.slice(start) },
    ];
    const tableRows = all.slice(start).map((r, i) => [r.d, fPrice(r.c), fPrice(series[1].values[i]), fPrice(series[2].values[i])]).reverse();
    const chart = lineChart({ points: pts, series, ariaLabel: `${s.id} 收盤價走勢（${state.range}），最新 ${fPrice(d.last_close)}`, markers });

    const kids = [tiles, rangeRow, figure({
      title: `收盤價（${currencyLabel(s)}，已含除權息調整）`,
      sub: 'Yahoo Finance（次級來源）；滑鼠移動或方向鍵可查看各日數值',
      legend: legendLine(series.map((x) => ({ name: x.name, color: x.color }))),
      chart,
      table: simpleTable(['日期', '收盤', 'MA20', 'MA60'], tableRows, [1, 2, 3]),
    })];
    if (pr.note) kids.push(el('p', { class: 'note', text: `歷史拼接：${pr.note}` }));
    if (q && (q.intraday || []).length > 1) {
      const bars = q.intraday;
      kids.push(figure({
        title: '今日盤中（Yahoo，可能延遲 15 分鐘以上）',
        sub: `更新於 ${fDateTime(state.live.fetched_at)}`,
        chart: lineChart({ points: bars.map((b) => ({ label: fDateTime(b.t) })), xFmt: (l) => l.slice(-5), series: [{ name: '價格', color: 'var(--series-1)', width: 2, values: bars.map((b) => b.p), main: true }], height: 160, ariaLabel: `${s.id} 今日盤中走勢` }),
        table: simpleTable(['時間', '價格'], bars.map((b) => [fDateTime(b.t), fPrice(b.p)]).reverse(), [1]),
      }));
    }
    kids.push(srcLine({ ...pr, note: null }));
    return card('價格走勢', '描述過去，不是預測。', kids);
  }

  // ------------------------------------------------------------------ 個股：預測
  function predictionSection(s) {
    const p = s.prediction || {};
    const h = (state.dash && state.dash.horizon_days) || p.horizon || 5;
    let model;
    if (!p.ok) {
      model = el('div', {}, el('h3', { text: '統計基準模型' }), el('p', { class: 'empty', text: `未產生機率：${p.reason || NA}` }));
    } else {
      const pct = Math.round(p.prob_up * 1000) / 10;
      model = el('div', {},
        el('h3', { text: `統計基準模型（未來 ${h} 個交易日）` }),
        el('div', { class: 'tiles' },
          tile('上漲機率', `${pct.toFixed(1)}%`, `截至 ${fDate(p.asof)} 收盤`),
          tile('判讀', p.signal, p.beats_baseline ? '樣本外優於天真基準' : '樣本外未優於天真基準')),
        el('div', { class: 'meter', role: 'meter', 'aria-valuemin': '0', 'aria-valuemax': '100', 'aria-valuenow': String(pct), 'aria-label': '上漲機率' },
          el('div', { class: 'meter-fill', style: { width: `${pct}%` } }), el('div', { class: 'meter-mid' })),
        el('div', { class: 'meter-scale' }, el('span', { text: '0%' }), el('span', { text: '50%' }), el('span', { text: '100%' })),
        el('div', { class: 'table-wrap' }, simpleTable(['樣本外指標', '數值'], [
          ['期間', `${fDate(p.oos_start)}～${fDate(p.oos_end)}（${fInt(p.n_oos)} 筆）`],
          ['模型命中率', fPct(p.acc_model, 1, false)],
          ['天真基準命中率', fPct(p.acc_naive, 1, false)],
          ['期間實際上漲比例', fPct(p.up_rate_oos, 1, false)],
          ['Brier 技巧分數', isNum(p.bss) ? p.bss.toFixed(3) : '—'],
          ['AUC', isNum(p.auc) ? p.auc.toFixed(3) : '—'],
        ], [1])),
        el('p', { class: 'note', text: '命中率需比天真基準高 2 個百分點以上且 Brier 技巧分數 > 0，才解讀為偏多／偏空；否則一律「無明確訊號」。每日預測期間互相重疊，有效樣本少於筆數。' }));
    }
    const llmPath = state.dash && state.dash.llm_report_path;
    const llm = el('div', {}, el('h3', { text: '上游 LLM 判斷（daily_stock_analysis）' }),
      s.dsa
        ? el('div', {},
          el('div', { class: 'tiles' }, tile('操作建議', s.dsa.signal), tile('評分', isNum(s.dsa.score) ? String(s.dsa.score) : '—'), tile('趨勢', s.dsa.trend)),
          el('p', { class: 'note' }, 'LLM 綜合行情與新聞的意見，未經核實，可能出錯或幻覺。', llmPath ? link('完整內容', repoUrl(llmPath)) : null))
        : el('p', { class: 'empty', text: '本次未啟用或未取得（需在 GitHub Secrets 設定任一 LLM 金鑰）。' }));

    const names = { logit_v1: '統計基準模型', dsa_llm: 'LLM（DSA）' };
    const track = Object.entries(s.track || {});
    const trackBox = el('div', {}, el('h3', { text: '事後命中追蹤' }),
      track.length
        ? el('div', { class: 'table-wrap' }, simpleTable(['方法', '已記錄', '有方向', '已到期', '命中', '命中率', '同期上漲比例'],
          track.map(([k, v]) => [names[k] || k, fInt(v.logged), fInt(v.directional), fInt(v.matured), fInt(v.hits), fPct(v.hit_rate, 1, false), fPct(v.up_rate, 1, false)]), [1, 2, 3, 4, 5, 6]))
        : el('p', { class: 'empty', text: '尚無紀錄。' }),
      el('p', { class: 'note', text: '「同期上漲比例」是同一批到期窗口中實際上漲的比例，可當作永遠猜漲的對照。' }));
    return card('漲跌預測', '兩種方法並列，都以到期實際收盤檢驗。', el('div', { class: 'grid-2' }, model, llm), trackBox);
  }

  // ------------------------------------------------------------------ 個股：機構持股（美股）
  function legsText(l) { return l ? `${fInt(l.shares)}／${fInt(l.call)}／${fInt(l.put)}` : '前期未取得'; }
  function changeText(cur, prev, known) {
    if (!known) return '前期未取得';
    prev = prev || 0;
    if (prev === 0 && cur > 0) return '新出現；前期未列';
    if (cur === 0 && prev > 0) return '本期未列';
    if (cur === 0 && prev === 0) return '—';
    const diff = cur - prev;
    return `${fSigned(diff)}（${fPct(diff / prev)}）`;
  }

  function usInstitutional(s) {
    const us = s.us || {};
    const tf = us.thirteen_f;
    const kids = [];
    if (!tf || !tf.ok) {
      kids.push(el('p', { class: 'empty' }, `近期 13F 新申報：${NA}（${(tf && tf.error) || ''}）。未取得不代表沒有新申報。`));
    } else {
      const pos = tf.positions || [];
      kids.push(el('p', { class: 'note' }, `檢索申報日 ${fDate(tf.window && tf.window[0])}～${fDate(tf.window && tf.window[1])} 含 CUSIP ${s.cusip || ''} 的 13F（`, link('SEC EDGAR 全文檢索', tf.url), '，一手）。13F 是季末持股，不是本週交易。', tf.note ? ` ${tf.note}。` : ''));
      if (!pos.length) {
        kids.push(el('p', { class: 'empty', text: '期間內沒有解析到含本股的 13F 申報（可能非申報季或索引延遲；已出清者不會被 CUSIP 檢索找到）。' }));
      } else {
        const filingLinks = (p) => el('span', {}, link('本期', p.url), '／', p.prior_url ? link('前期', p.prior_url) : '前期—');
        const meta = (p) => `${p.period || '—'}／${p.file_date || '—'}（${p.form}）`;
        const aRows = [], bRows = [], cRows = [];
        for (const p of pos) {
          const known = !!p.prior;
          const pr = p.prior || { shares: 0, call: 0, put: 0 };
          for (const [label, cur, prev, val] of [['股票', p.current.shares, pr.shares, p.current.value_shares], ['Call', p.current.call, pr.call, p.current.value_call]]) {
            if (cur > 0) aRows.push([p.filer, label, `${fInt(cur)}／${known ? fInt(prev) : '—'}`, fInt(val), changeText(cur, prev, known), meta(p), filingLinks(p)]);
          }
          if (p.class.B) bRows.push([p.filer, 'Put', `${fInt(p.current.put)}／${known ? fInt(pr.put) : '—'}`, fInt(p.current.value_put), changeText(p.current.put, pr.put, known), meta(p), filingLinks(p)]);
          if (p.class.C) cRows.push([p.filer, legsText(p.current), legsText(p.prior), '多腿並存，不能推得淨方向', filingLinks(p)]);
        }
        const head = ['機構', '部位', '本期／前期數量', 'value 欄（原值）', '期對期變化', '季度／申報日', 'SEC 原件'];
        const block = (title, small, rows, headers, nums) => [
          el('div', { class: 'cls' }, title, ' ', el('small', { text: small })),
          rows.length ? el('div', { class: 'table-wrap' }, simpleTable(headers, rows, nums)) : el('p', { class: 'empty', text: '本次解析的申報中無。' })];
        kids.push(
          block('A. 多方證據', '只證明已揭露多方曝險，不代表整體淨多', aRows, head, [2, 3]),
          block('B. 空方／避險線索', 'Put 可能是保護性避險，不等於裸做空；13F 不揭露一般空頭股數', bRows, head, [2, 3]),
          block('C. 方向不明', '多腿並存', cRows, ['機構', '本期 股票／Call／Put', '前期 股票／Call／Put', '判讀', 'SEC 原件'], [1, 2]));
      }
    }
    const holders = (s.filings && s.filings.holders) || [];
    kids.push(el('h3', { text: '≥5% 持有人申報（13G/13D）' }),
      holders.length
        ? el('ul', { class: 'news' }, holders.map((f) => el('li', {}, el('div', { class: 'meta', text: `${fDate(f.filing_date)}｜${f.form}` }), link(f.description, f.url))))
        : el('p', { class: 'empty', text: (s.filings && s.filings.status && !s.filings.status.ok) ? `${NA}（${s.filings.status.error || ''}）` : '公告掃描期間內沒有 13G/13D。' }));

    const yh = us.yahoo || {};
    kids.push(el('h3', { text: 'Yahoo 機構持股聚合（次級，只作交叉檢查）' }));
    if (!yh.ok || !yh.data) {
      kids.push(el('p', { class: 'empty', text: `${NA}（${yh.error || ''}）` }));
    } else {
      const mj = yh.data.major || {};
      const sh = yh.data.short || {};
      kids.push(el('div', { class: 'tiles' },
        tile('機構持股比例', fPct(mj.institutionsPercentHeld, 2, false), 'Yahoo 口徑；各機構申報期不同'),
        tile('機構家數', fInt(mj.institutionsCount)),
        tile('市場總 short interest', isNum(sh.shares_short) ? compact(sh.shares_short) + ' 股' : '—', `結算日 ${fDate(sh.settlement_date)}；前期 ${isNum(sh.shares_short_prior) ? compact(sh.shares_short_prior) : '—'}；無法歸戶`)));
      const top = (yh.data.institutional || []).slice(0, 5);
      if (top.length) {
        kids.push(el('div', { class: 'table-wrap' }, simpleTable(['機構（Yahoo 前五）', '申報期', '股數', '占比', 'Yahoo 標示變化'],
          top.map((r) => [r.holder, fDate(r.date_reported), fInt(r.shares), fPct(r.pct_held, 2, false), fPct(r.pct_change)]), [2, 3, 4])));
      }
      kids.push(srcLine(yh));
    }
    return card('機構持股（美股）', '13F 普通股／Call 只證明已揭露的多方曝險；Put 與 short interest 只是空方或避險線索。', kids);
  }

  // ------------------------------------------------------------------ 個股：機構籌碼（台股）
  function twInstitutional(s) {
    const tw = s.tw || {};
    const kids = [];
    const hist = (tw.history || []).slice().sort((a, b) => (a.date < b.date ? -1 : 1));
    const lv = liveFor(s.id);
    const latest = tw.latest && tw.latest.ok ? tw.latest.data : null;
    const fresh = lv && lv.tw_3insti && (!hist.length || lv.tw_3insti.date > hist[hist.length - 1].date) ? lv.tw_3insti : null;
    const show = fresh || latest;
    if (show) {
      kids.push(el('div', { class: 'tiles' },
        tile('外資及陸資', fSigned(show.foreign_net), '不含外資自營商；股'),
        tile('投信', fSigned(show.trust_net), '股'),
        tile('自營商', fSigned(show.dealer_net), '含避險；股'),
        tile('三大法人合計', fSigned(show.total_net), `${show.date}${fresh ? '｜盤後刷新，尚未併入歷史' : ''}`)));
      kids.push(el('p', { class: 'note' }, `最新交易日 ${show.date}（`, link('櫃買中心 OpenAPI', (tw.latest && tw.latest.url) || 'https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading'), '，一手）。', tw.crosscheck ? `交叉檢查：${tw.crosscheck}。` : ''));
    } else {
      kids.push(el('p', { class: 'empty', text: `最新交易日三大法人${NA}（${(tw.latest && tw.latest.error) || ''}）。` }));
    }

    if (hist.length) {
      const recent = hist.slice(-60);
      const cats = [['foreign_net', '外資及陸資'], ['trust_net', '投信'], ['dealer_net', '自營商（含避險）']];
      kids.push(figure({
        title: `三大法人每日淨買賣超（近 ${recent.length} 個交易日，單位：股）`,
        sub: '各小圖 Y 軸刻度不同；由本 repo 每日累積，來源標記見表格',
        legend: legendLine([{ name: '買超', color: 'var(--series-1)', kind: 'rect' }, { name: '賣超', color: 'var(--series-2)', kind: 'rect' }]),
        chart: el('div', { class: 'small-multiples' }, cats.map(([k, name]) => el('div', {},
          el('div', { class: 'figure-sub', text: name }),
          divergingBars({ items: recent.map((r) => ({ label: r.date, value: isNum(r[k]) ? r[k] : null })), ariaLabel: `${name}每日淨買賣超` })))),
        table: simpleTable(['日期', '外資及陸資', '投信', '自營商', '合計', '來源'], recent.slice().reverse().map((r) => [r.date, fSigned(r.foreign_net), fSigned(r.trust_net), fSigned(r.dealer_net), fSigned(r.total_net), r.source || '']), [1, 2, 3, 4]),
      }));
      const sum = (rows, k) => rows.reduce((a, r) => a + (isNum(r[k]) ? r[k] : 0), 0);
      const streak = (k) => {
        const vals = hist.map((r) => r[k]).filter(isNum);
        if (!vals.length) return null;
        const last = vals[vals.length - 1];
        if (last === 0) return 0;
        let c = 0;
        for (let i = vals.length - 1; i >= 0 && Math.sign(vals[i]) === Math.sign(last); i--) c++;
        return Math.sign(last) * c;
      };
      const w5 = hist.slice(-5), w20 = hist.slice(-20);
      kids.push(el('div', { class: 'table-wrap' }, simpleTable(['類別', `近 ${w5.length} 日`, `近 ${w20.length} 日`, '連續天數（+買／−賣）', '判讀類別'],
        cats.map(([k, name]) => {
          const s5 = sum(w5, k), s20 = sum(w20, k);
          const cat = s5 * s20 < 0 ? 'C 方向不明（近期轉向）' : s20 > 0 ? 'A 多方動作（淨買超）' : s20 < 0 ? 'B 減碼／空方線索（淨賣超≠放空）' : '—';
          return [name, fSigned(s5), fSigned(s20), fSigned(streak(k)), cat];
        }), [1, 2, 3])));
    }
    kids.push(el('p', { class: 'note', text: '三大法人是類別合計，無法歸戶到個別機構；淨賣超可能是減碼、換股或避險，不等於放空。這是盤後實際買賣，與美股 13F 季末持股性質不同。' }));

    const extra = [];
    const sh = tw.shareholding || {};
    if (sh.ok && (sh.data || []).length) {
      const last = sh.data[sh.data.length - 1], first = sh.data[0];
      extra.push(tile('外資持股比率', isNum(last.foreign_ratio) ? `${last.foreign_ratio}%` : '—', `${last.date}；${first.date} 為 ${isNum(first.foreign_ratio) ? first.foreign_ratio + '%' : '—'}；FinMind（次級）`));
    } else extra.push(tile('外資持股比率', '—', `${NA}（${sh.error || ''}）`));
    const mg = tw.margin || {};
    if (mg.ok && (mg.data || []).length) {
      const last = mg.data[mg.data.length - 1], prev = mg.data[Math.max(0, mg.data.length - 6)];
      extra.push(tile('融券餘額', fInt(last.short_balance), `${last.date}；${prev.date} 為 ${fInt(prev.short_balance)}；單位沿用來源；無法歸戶`));
    } else extra.push(tile('融券餘額', '—', `${NA}（${mg.error || ''}）`));
    const td = tw.tdcc || {};
    const tdh = (tw.tdcc_history || []).slice().sort((a, b) => (a.date < b.date ? -1 : 1));
    if (td.ok && td.data) {
      const r = td.data;
      const prev = tdh.length >= 2 ? tdh[tdh.length - 2] : null;
      const wow = prev && isNum(r.big1000_ratio) && isNum(prev.big1000_ratio) ? `較 ${prev.date} ${(r.big1000_ratio - prev.big1000_ratio >= 0 ? '+' : '−')}${Math.abs(r.big1000_ratio - prev.big1000_ratio).toFixed(2)} 個百分點` : '前一週資料尚未累積';
      extra.push(tile('千張以上大戶', isNum(r.big1000_ratio) ? `${r.big1000_ratio}%` : '—', `${r.date}；${fInt(r.big1000_holders)} 人；${wow}`));
      extra.push(tile('400 張以上／50 張以下', `${r.big400_ratio ?? '—'}%／${r.small50_ratio ?? '—'}%`, `集保總人數 ${fInt(r.holders_total)}`));
    } else extra.push(tile('集保股權分散', '—', `${NA}（${td.error || ''}）`));
    kids.push(el('h3', { text: '外資持股、融資融券、集保股權分散' }), el('div', { class: 'tiles' }, extra));
    if (tdh.length >= 2) {
      kids.push(figure({
        title: '千張以上大戶持股比例（%，每週）',
        sub: '集保結算所開放資料（一手）；集保大戶可能是法人、董監或自然人，不等於機構持股',
        chart: lineChart({ points: tdh.map((r) => ({ label: r.date })), series: [{ name: '千張大戶 %', color: 'var(--series-1)', width: 2, values: tdh.map((r) => r.big1000_ratio), main: true }], height: 180, yFmt: (v) => (isNum(v) ? v.toFixed(1) + '%' : '—'), ariaLabel: '千張以上大戶持股比例走勢' }),
        table: simpleTable(['日期', '千張大戶 %', '400 張以上 %', '總人數'], tdh.slice().reverse().map((r) => [r.date, String(r.big1000_ratio ?? '—'), String(r.big400_ratio ?? '—'), fInt(r.holders_total)]), [1, 2, 3]),
      }));
    } else {
      kids.push(el('p', { class: 'note', text: '集保大戶可能是法人、董監或自然人，不等於機構持股。週變化需累積兩週以上資料。' }));
    }
    if (td.url) kids.push(el('p', { class: 'note' }, link('集保結算所開放資料', td.url), '；', link('FinMind 外資持股', sh.url), '；', link('FinMind 融資融券', mg.url)));
    return card('機構籌碼（台股）', null, kids);
  }

  // ------------------------------------------------------------------ 個股：新聞
  function newsSection(s) {
    const lv = liveFor(s.id);
    const useLive = lv && Array.isArray(lv.news) && lv.news.length > 0 && new Date(state.live.fetched_at) > new Date(state.dash.generated_at || 0);
    const items = useLive ? lv.news : (s.news || []);
    const kids = [];
    const company = (s.filings && s.filings.company) || [];
    if (s.market === 'US') {
      kids.push(el('h3', { text: '公司與內部人申報（SEC EDGAR，一手）' }),
        company.length
          ? el('ul', { class: 'news' }, company.slice(0, 8).map((f) => el('li', {}, el('div', { class: 'meta', text: `${fDate(f.filing_date)}｜${f.form}` }), link(f.description, f.url))))
          : el('p', { class: 'empty', text: (s.filings && s.filings.status && !s.filings.status.ok) ? `${NA}（${s.filings.status.error || ''}）` : '公告掃描期間內沒有新的公司申報。' }));
    } else {
      kids.push(el('p', { class: 'note' }, '台股重大訊息請以', link('公開資訊觀測站', 'https://mops.twse.com.tw/'), '原文為準。'));
    }
    kids.push(el('h3', { text: `新聞標題${useLive ? `（${fDateTime(state.live.fetched_at)} 更新）` : ''}` }),
      items.length
        ? el('ul', { class: 'news' }, items.map((n) => el('li', {}, el('div', { class: 'meta', text: `${n.published ? fDateTime(n.published) : '日期不明'}｜${n.source || ''}` }), link(n.title, n.link))))
        : el('p', { class: 'empty', text: '期間內沒有符合的標題，或來源本次未取得。' }),
      el('p', { class: 'note', text: 'Google News／Yahoo 聚合（次級）；只列標題，未逐篇核實內容，也不由程式判斷利多利空。' }));
    return card('新聞與公告', null, kids);
  }

  // ------------------------------------------------------------------ 其他區塊
  function renderDetail() {
    const box = document.getElementById('detail');
    const s = selected();
    if (!s) { box.replaceChildren(); return; }
    box.replaceChildren(
      el('h2', { class: 'section-title', text: s.market === 'TW' ? `${s.name}（${s.id}）` : `${s.id} — ${s.name}` }),
      el('p', { class: 'note', text: `${s.exchange}｜${s.security_note || ''}` }),
      priceSection(s),
      predictionSection(s),
      s.market === 'US' ? usInstitutional(s) : twInstitutional(s),
      newsSection(s));
  }

  function renderSources() {
    const box = document.getElementById('sources');
    const rows = [...((state.dash && state.dash.sources) || []).map((x) => ({ ...x, scope: '每日' })),
      ...((state.live && state.live.sources) || []).map((x) => ({ ...x, scope: '盤中' }))];
    const fails = rows.filter((r) => !r.ok).length;
    box.replaceChildren(el('details', {},
      el('summary', { text: `資料來源狀態（${rows.length} 項，失敗 ${fails} 項）` }),
      el('div', { class: 'table-wrap' }, simpleTable(['來源', '批次', '狀態', '一手／次級', '說明'],
        rows.map((r) => [link(r.source || '—', r.url), r.scope, stateBadge(r.ok), r.secondary ? '次級' : '一手', r.note || r.error || ''])))));
  }

  function renderReports() {
    const box = document.getElementById('reports');
    const rp = state.reports;
    const latest = state.dash && state.dash.report_path;
    const list = (rows) => el('ul', {}, rows.slice(0, 6).map((r) => el('li', {}, link(`${r.date}${r.kind.endsWith('-llm') ? '（LLM）' : ''}`, r.url))));
    const daily = rp && rp.daily ? rp.daily.filter((r) => r.kind === 'daily') : [];
    const weekly = rp && rp.weekly ? rp.weekly : [];
    const kids = [
      latest ? el('p', {}, '今日完整 Markdown 報告：', link(latest, repoUrl(latest))) : null,
      daily.length ? el('div', {}, '每日監控報告', list(daily)) : null,
      weekly.length ? el('div', {}, '機構持股週報', list(weekly)) : null,
    ];
    box.replaceChildren(...kids.filter(Boolean));
  }

  function renderStatus() {
    const box = document.getElementById('status');
    const parts = [];
    if (state.error && !state.dash) {
      box.replaceChildren(el('span', { class: 'warn', text: `資料載入失敗：${state.error.message}。若是剛啟用，請先執行「每日監控」workflow。` }));
      return;
    }
    if (state.dash) parts.push(`每日資料 ${fDateTime(state.dash.generated_at)}`);
    if (state.live) parts.push(`盤中刷新 ${fDateTime(state.live.fetched_at)}`);
    if (state.checkedAt) parts.push(`上次檢查 ${fDateTime(state.checkedAt.toISOString())}，每 5 分鐘自動檢查`);
    if (state.error) parts.push(`（最近一次檢查失敗：${state.error.message}，顯示前一次資料）`);
    box.textContent = parts.join('｜');
  }

  function render() {
    renderStatus();
    if (!state.dash) {
      document.getElementById('overview').replaceChildren();
      document.getElementById('tabs').replaceChildren();
      document.getElementById('detail').replaceChildren(el('div', { class: 'card banner' },
        el('h2', { text: '尚無每日資料' }),
        el('p', { text: '儀表板讀取 data/dashboard.json；請先在 GitHub Actions 執行「每日監控（BB、NOK、中光電投控）」，完成後再執行「監控儀表板」部署。' })));
      document.getElementById('sources').replaceChildren();
      return;
    }
    renderOverview();
    renderTabs();
    renderDetail();
    renderSources();
    renderReports();
  }

  // ------------------------------------------------------------------ 主題與啟動
  function applyTheme(t) {
    if (t) document.documentElement.setAttribute('data-theme', t);
    else document.documentElement.removeAttribute('data-theme');
    const dark = t ? t === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches;
    const btn = document.getElementById('theme');
    btn.textContent = dark ? '淺色' : '深色';
  }
  function initTheme() {
    let saved = null;
    try { saved = localStorage.getItem('so-theme'); } catch (_) { saved = null; }
    applyTheme(saved);
    document.getElementById('theme').addEventListener('click', () => {
      const dark = document.documentElement.getAttribute('data-theme')
        ? document.documentElement.getAttribute('data-theme') === 'dark'
        : window.matchMedia('(prefers-color-scheme: dark)').matches;
      const next = dark ? 'light' : 'dark';
      try { localStorage.setItem('so-theme', next); } catch (_) { /* 私密模式等情況不保存 */ }
      applyTheme(next);
    });
  }

  function init() {
    initTheme();
    const h = decodeURIComponent(location.hash.replace(/^#/, ''));
    if (h) state.sel = h;
    window.addEventListener('hashchange', () => { state.sel = decodeURIComponent(location.hash.replace(/^#/, '')); render(); });
    document.getElementById('refresh').addEventListener('click', load);
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden && (!state.checkedAt || Date.now() - state.checkedAt.getTime() > REFRESH_MS)) load();
    });
    setInterval(() => { if (!document.hidden) load(); }, REFRESH_MS);
    load();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
