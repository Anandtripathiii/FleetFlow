/* Simulation lab: plays back rules vs RL vs the policy arbiter on three canvases. */
(() => {
  const TRAVEL = 40;
  // colours come from the CSS tokens so the floor follows the light/dark theme
  const ZONE_TOKEN = { A: '--ok', B: '--info', C: '--warn', D: '--plum' };
  let P = {};
  function palette() {
    const zc = {};
    for (const [z, t] of Object.entries(ZONE_TOKEN)) zc[z] = [cssColor(t, .08), cssColor(t)];
    P = { zc, ink: cssColor('--ink'), muted: cssColor('--muted'), bg: cssColor('--bg'), line: cssColor('--line'),
      warn: cssColor('--warn'), warnBg: cssColor('--warn', .12), warnLine: cssColor('--warn', .5), bad: cssColor('--bad'),
      ok: cssColor('--ok'), queue: cssColor('--muted', .45), track: cssColor('--ink', .12),
      robot: { 0: cssColor('--ok'), 1: cssColor('--info'), 2: cssColor('--warn'), 3: cssColor('--warn'), 4: cssColor('--bad') } };
  }
  const $ = id => document.getElementById(id);
  let data = null, frame = 0, playing = false, last = 0, acc = 0, chart = null;
  const prevZone = { rules: [], rl: [], arbiter: [] };

  // drawing happens in a 640 x 400 space, scaled to the real canvas size for sharp text
  const W = 640, H = 400;
  const RECT = [[10, 10, 304, 184], [326, 10, 304, 184], [10, 206, 304, 184], [326, 206, 304, 184]];
  const center = z => [RECT[z][0] + RECT[z][2] / 2, RECT[z][1] + RECT[z][3] / 2 + 14];
  const slot = (z, k) => [RECT[z][0] + 26 + (k % 5) * 36, RECT[z][1] + 82 + Math.floor(k / 5) * 40];

  function ctx(side) {
    const cv = $('cv-' + side), dpr = window.devicePixelRatio || 1;
    const w = Math.round(cv.clientWidth * dpr), h = Math.round(cv.clientWidth * dpr * H / W);
    if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
    const g = cv.getContext('2d');
    g.setTransform(w / W, 0, 0, w / W, 0, 0);
    return g;
  }

  function draw(side, snap, zones, names) {
    const g = ctx(side);
    g.clearRect(0, 0, W, H);
    zones.forEach((code, z) => {
      const [x, y, w, h] = RECT[z], [bg, fg] = P.zc[code];
      const q = snap.queues[z], hot = q > 25;
      g.fillStyle = hot ? P.warnBg : bg; roundRect(g, x, y, w, h, 0); g.fill();
      g.strokeStyle = hot ? P.warnLine : P.line; g.lineWidth = 1; g.stroke();
      g.fillStyle = fg; g.font = '700 17px Figtree, system-ui'; g.fillText('Zone ' + code, x + 14, y + 26);
      g.font = '500 12.5px Figtree, system-ui'; g.fillStyle = P.muted;
      g.fillText(names[code] + ' · ' + snap.rate[z].toFixed(0) + ' orders/min', x + 14, y + 45);
      if (snap.signal[z] > 1.05) {
        g.fillStyle = P.warnBg; roundRect(g, x + w - 82, y + 10, 70, 22, 2); g.fill();
        g.fillStyle = P.warn; g.font = '700 12px Figtree'; g.textAlign = 'center';
        g.fillText('web x' + snap.signal[z].toFixed(1), x + w - 47, y + 25); g.textAlign = 'left';
      }
      g.textAlign = 'right'; g.font = '700 15px Figtree'; g.fillStyle = hot ? P.bad : P.ink;
      g.fillText(q + ' waiting', x + w - 14, y + (snap.signal[z] > 1.05 ? 52 : 28)); g.textAlign = 'left';
      const shown = Math.min(q, 30);
      for (let i = 0; i < shown; i++) {
        const cx = x + w - 24 - (i % 5) * 12, cy = y + h - 22 - Math.floor(i / 5) * 12;
        g.fillStyle = hot ? P.warn : P.queue; roundRect(g, cx, cy, 9, 9, 1); g.fill();
      }
      if (q > 30) { g.fillStyle = P.bad; g.font = '700 11px Figtree'; g.textAlign = 'right'; g.fillText('+' + (q - 30), x + w - 14, y + h - 98); g.textAlign = 'left'; }
    });
    const count = [0, 0, 0, 0];
    snap.zone.forEach((z, i) => {
      const st = snap.status[i];
      let px, py;
      if (st === 2) {
        const from = prevZone[side][i] ?? z;
        const p = Math.max(0, Math.min(1, 1 - snap.timer[i] / TRAVEL));
        const [ax, ay] = center(from), [bx, by] = center(z);
        px = ax + (bx - ax) * p; py = ay + (by - ay) * p;
        g.setLineDash([4, 5]); g.strokeStyle = P.warn; g.lineWidth = 1.5;
        g.beginPath(); g.moveTo(ax, ay); g.lineTo(bx, by); g.stroke(); g.setLineDash([]);
      } else {
        [px, py] = slot(z, count[z]++);
        prevZone[side][i] = z;
      }
      g.beginPath(); g.arc(px, py, 14, 0, Math.PI * 2);
      g.fillStyle = P.robot[st]; g.fill(); g.lineWidth = 2; g.strokeStyle = P.bg; g.stroke();
      g.fillStyle = P.bg; g.font = '700 11px Figtree'; g.textAlign = 'center';
      g.fillText(st === 4 ? '×' : 'R' + (i + 1), px, py + 4); g.textAlign = 'left';
      const b = snap.battery[i] / 100;
      g.fillStyle = P.track; g.fillRect(px - 12, py + 18, 24, 3);
      g.fillStyle = b < .25 ? P.bad : P.ok; g.fillRect(px - 12, py + 18, 24 * b, 3);
    });
  }

  function roundRect(g, x, y, w, h, r) {
    g.beginPath(); g.moveTo(x + r, y); g.arcTo(x + w, y, x + w, y + h, r); g.arcTo(x + w, y + h, x, y + h, r);
    g.arcTo(x, y + h, x, y, r); g.arcTo(x, y, x + w, y, r); g.closePath();
  }

  const fmt = s => String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0');

  function render() {
    if (!data) return;
    const a = data.rules.history[frame], b = data.rl.history[frame], c = data.arbiter.history[frame];
    draw('rules', a, data.zones, data.zone_names);
    draw('rl', b, data.zones, data.zone_names);
    draw('arbiter', c, data.zones, data.zone_names);
    $('clock').textContent = fmt(a.t);
    const peak = Math.max(...a.rate.map((r, i) => r / [6, 6, 2, 2][i]));
    $('phase').textContent = peak > 1.5 ? 'demand shock in progress' : (a.t < 60 ? 'warming up' : 'normal flow');
    for (const [side, s] of [['rules', a], ['rl', b], ['arbiter', c]]) {
      $(side + '-avg').textContent = s.avg_wait.toFixed(0);
      $(side + '-served').textContent = s.served;
      $(side + '-moves').textContent = s.moves + ' moves';
    }
    $('scrub').value = frame;
    const done = frame >= data.rules.history.length - 1;
    if (done) finish();
  }

  function finish() {
    const ra = data.rules.metrics, rb = data.rl.metrics, rc = data.arbiter.metrics;
    for (const [side, m] of [['rules', ra], ['rl', rb], ['arbiter', rc]]) {
      $(side + '-avg').textContent = m.avg_wait_s.toFixed(0);
      $(side + '-p90').textContent = m.p90_wait_s.toFixed(0);
      $(side + '-served').textContent = m.served;
      $(side + '-idle').textContent = m.idle_pct.toFixed(0);
    }
    const better = (1 - rb.avg_wait_s / ra.avg_wait_s) * 100;
    const served = (rb.served / ra.served - 1) * 100;
    const p90 = (1 - rb.p90_wait_s / ra.p90_wait_s) * 100;
    const cls = v => v >= 0 ? 'win' : 'lose';
    $('verdict-card').hidden = false;
    $('verdict').innerHTML = `<b>Result:</b> the RL agent changed average wait by <span class="${cls(better)}">${better >= 0 ? '−' : '+'}${Math.abs(better).toFixed(0)}%</span>,
      worst-case (P90) wait by <span class="${cls(p90)}">${p90 >= 0 ? '−' : '+'}${Math.abs(p90).toFixed(0)}%</span>
      and served <span class="${cls(served)}">${served >= 0 ? '+' : ''}${served.toFixed(0)}%</span> orders, using ${rb.moves} robot moves.
      Robots sat idle ${rb.idle_pct.toFixed(0)}% of the time vs ${ra.idle_pct.toFixed(0)}% with fixed rules.
      <br><b>Policy arbiter:</b> average wait ${rc.avg_wait_s.toFixed(0)} s vs RL ${rb.avg_wait_s.toFixed(0)} s and rules ${ra.avg_wait_s.toFixed(0)} s,
      using ${rc.moves} robot moves.`;
  }

  function buildChart() {
    const H = data.rules.history;
    const labels = H.map(s => fmt(s.t));
    const demand = H.map(s => s.rate.reduce((a, b) => a + b, 0));
    if (chart) chart.destroy();
    chart = new Chart($('chart'), {
      data: { labels, datasets: [
        { type: 'line', label: 'Rules', data: H.map(s => s.avg_wait), borderColor: P.muted, borderWidth: 2.5, pointRadius: 0, tension: .25 },
        { type: 'line', label: 'RL agent', data: data.rl.history.map(s => s.avg_wait), borderColor: cssColor('--accent'), borderWidth: 2.5, pointRadius: 0, tension: .25 },
        { type: 'line', label: 'Arbiter', data: data.arbiter.history.map(s => s.avg_wait), borderColor: cssColor('--info'), borderWidth: 2.5, pointRadius: 0, tension: .25 },
        { type: 'bar', label: 'Demand', data: demand, backgroundColor: cssColor('--chart-2', .6), yAxisID: 'y2', barPercentage: 1, categoryPercentage: 1 },
      ] },
      options: { maintainAspectRatio: false, animation: false, interaction: { mode: 'index', intersect: false },
        plugins: { legend: { display: false }, tooltip: { filter: i => i.datasetIndex < 4 } },
        scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 10 } },
          y: { title: { display: true, text: 'avg wait (s)' }, grid: { color: cssColor('--chart-grid') }, beginAtZero: true },
          y2: { position: 'right', grid: { display: false }, beginAtZero: true, title: { display: true, text: 'orders/min' } } } }
    });
  }

  function showLog() {
    const L = data.rl.log;
    $('log').innerHTML = L.length ? L.map(l => `<div><b>${fmt(l.t)}</b><span><b style="color:var(--ink)">${l.robot}</b> ${l.to === '-' ? '' : l.from + ' → ' + l.to}<br><span class="muted">${l.why}</span></span></div>`).join('')
      : '<div class="muted">The agent kept every robot where it was.</div>';
    const D = data.arbiter.decisions, n = data.arbiter.counts, tag = { rl: 'b-info', rules: 'b-warn', none: '' };
    $('arb-counts').textContent = `rl ${n.rl} · rules ${n.rules} · none ${n.none}`;
    $('arb-log').innerHTML = D.map(d => `<div><b>${fmt(d.t)}</b><span><span class="badge ${tag[d.source]}">${d.source}</span> ${d.move}<br><span class="muted">${d.reason}</span></span></div>`).join('');
  }

  function tick(ts) {
    if (!playing) return;
    const dt = ts - last; last = ts;
    acc += dt * (+$('speed').value) / 400;
    while (acc >= 1 && frame < data.rules.history.length - 1) { frame++; acc -= 1; }
    render();
    if (frame >= data.rules.history.length - 1) { playing = false; $('play').textContent = 'Replay'; return; }
    requestAnimationFrame(tick);
  }

  async function run() {
    $('run').disabled = true; $('run').textContent = 'Running…';
    try {
      const r = await fetch(`${window.SIM_API}?scenario=${$('sc').value}&seed=${$('seed').value || 1}`);
      data = await r.json();
      frame = 0; acc = 0; prevZone.rules = []; prevZone.rl = []; prevZone.arbiter = [];
      ['p90', 'idle'].forEach(k => ['rules', 'rl', 'arbiter'].forEach(side => { $(side + '-' + k).textContent = '–'; }));
      $('verdict-card').hidden = true;
      $('scrub').max = data.rules.history.length - 1;
      buildChart(); showLog();
      playing = true; $('play').disabled = false; $('play').textContent = 'Pause';
      last = performance.now(); requestAnimationFrame(tick);
    } finally { $('run').disabled = false; $('run').innerHTML = 'Run all'; }
  }

  window.addEventListener('resize', () => data && render());
  $('run').onclick = run;
  $('play').onclick = () => {
    if (!data) return;
    if (frame >= data.rules.history.length - 1) { frame = 0; prevZone.rules = []; prevZone.rl = []; prevZone.arbiter = []; }
    playing = !playing; $('play').textContent = playing ? 'Pause' : 'Play';
    if (playing) { last = performance.now(); requestAnimationFrame(tick); }
  };
  $('scrub').oninput = e => { if (!data) return; frame = +e.target.value; playing = false; $('play').textContent = 'Play'; render(); };
  Chart.defaults.font.family = 'Figtree, system-ui, sans-serif';
  onTheme(() => { palette(); Chart.defaults.color = P.muted; if (data) { buildChart(); render(); } });
  run();
})();
