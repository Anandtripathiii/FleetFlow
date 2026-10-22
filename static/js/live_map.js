/* Manager live map: polls /api/fleet/live every 5 s, moves van markers, shows details, alerts and filters. */
(() => {
  const $ = id => document.getElementById(id);
  const POLL_MS = 5000;
  const LABEL = { live: 'Live', stale: 'Stale', offline: 'Offline', denied: 'No permission', unavailable: 'No signal', waiting: 'No fix yet' };
  const BADGE = { live: 'b-ok', stale: 'b-warn', offline: 'b-pending', denied: '', unavailable: '', waiting: '' };
  const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const ago = s => s == null ? 'never' : s < 60 ? s + ' s ago' : Math.floor(s / 60) + ' min ago';
  const hm = ts => ts ? ts.slice(11, 16) : '–';

  const map = L.map('map', { zoomControl: true }).setView([28.545, 77.24], 12);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' }).addTo(map);
  // the card animates in and the grid can reflow after load: re-measure so tiles cover the whole map
  new ResizeObserver(() => map.invalidateSize()).observe($('map'));
  addEventListener('load', () => map.invalidateSize());

  let data = { trips: [], alerts: [], geofences: [] }, selected = null, fenced = false, fitted = false, destLine = null;
  const markers = new Map();

  function drawFences() {
    for (const g of data.geofences) {
      const color = cssColor(g.kind === 'warehouse' ? '--ink' : '--info');
      L.circle([g.lat, g.lng], { radius: g.radius_m, color, weight: 1.5, fillOpacity: .12 }).addTo(map).bindTooltip(esc(g.name));
    }
    fenced = true;
  }

  function icon(t) {
    const label = esc((t.vehicle_code || t.code).replace(/^VAN-/, 'V'));
    return L.divIcon({ className: '', iconSize: [30, 30], iconAnchor: [15, 15],
      html: `<div class="veh ${t.signal}${t.id === selected ? ' sel' : ''}">${label}</div>` });
  }

  function glide(m, to) {                       // slide to the new fix instead of jumping
    const from = m.getLatLng();
    if (still || from.distanceTo(to) < 1) return m.setLatLng(to);
    const t0 = performance.now(), D = 900;
    const step = now => {
      const k = Math.min(1, (now - t0) / D), e = 1 - Math.pow(1 - k, 3);
      m.setLatLng([from.lat + (to[0] - from.lat) * e, from.lng + (to[1] - from.lng) * e]);
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  function visible(t) {
    const st = $('f-status').value, sg = $('f-signal').value, q = $('f-q').value.trim().toLowerCase();
    if (st && t.status !== st) return false;
    if (sg && t.signal !== sg) return false;
    if ($('f-alerts').checked && !t.open_alerts) return false;
    if (q && ![t.driver_name, t.code, t.vehicle_code, t.plate, t.to_name, t.transfer_code].join(' ').toLowerCase().includes(q)) return false;
    return true;
  }

  function render() {
    const shown = data.trips.filter(visible);
    const ids = new Set(shown.filter(t => t.last_lat != null).map(t => t.id));
    for (const [id, m] of markers) if (!ids.has(id)) { m.remove(); markers.delete(id); }
    for (const t of shown) {
      if (t.last_lat == null) continue;
      let m = markers.get(t.id);
      if (!m) {
        m = L.marker([t.last_lat, t.last_lng], { icon: icon(t), keyboard: true, title: `${t.code} · ${t.driver_name}` }).addTo(map);
        m.on('click', () => select(t.id));
        markers.set(t.id, m);
      } else {
        m.setIcon(icon(t));
        glide(m, [t.last_lat, t.last_lng]);
      }
    }
    if (!fitted && ids.size) {
      map.fitBounds(L.latLngBounds([...markers.values()].map(m => m.getLatLng())).pad(0.4), { maxZoom: 14 });
      fitted = true;
    }
    $('count').textContent = shown.length;
    $('trips').innerHTML = shown.length ? shown.map(t => `
      <div class="trip-row${t.id === selected ? ' sel' : ''}" data-id="${t.id}" tabindex="0">
        <b>${esc(t.vehicle_code || '–')}</b><span><b style="color:var(--ink)">${esc(t.code)}</b> ${esc(t.driver_name)} → ${esc(t.to_name)}
        <br><span class="badge ${BADGE[t.signal]}">${LABEL[t.signal]}</span> <span class="muted">${t.status === 'arrived' ? 'arrived' : 'on the way'} · seen ${ago(t.age_s)}${t.open_alerts ? ` · <span style="color:var(--bad)">${t.open_alerts} alert(s)</span>` : ''}</span></span></div>`).join('')
      : '<div class="muted" style="padding:14px 18px">No trips match.</div>';
    $('trips').querySelectorAll('.trip-row').forEach(r => {
      r.onclick = () => select(+r.dataset.id);
      r.onkeydown = e => e.key === 'Enter' && select(+r.dataset.id);
    });
    $('alert-count').textContent = data.alerts.length;
    $('alerts').innerHTML = data.alerts.length ? data.alerts.map(a => `
      <div><b>${hm(a.created_at)}</b><span><span class="badge ${a.level === 'danger' ? 'b-pending' : 'b-warn'}">${esc(a.trip_code)}</span> ${esc(a.message.replace(a.trip_code + ': ', ''))}</span></div>`).join('')
      : '<div class="muted" style="padding:14px 18px">No open alerts.</div>';
    details();
  }

  function details() {
    const t = data.trips.find(x => x.id === selected);
    if (destLine) { destLine.remove(); destLine = null; }
    if (!t) {
      $('d-title').textContent = 'Trip details'; $('d-signal').textContent = '–'; $('d-signal').className = 'badge plain';
      $('details').innerHTML = '<div class="muted">Click a van on the map or a trip in the list.</div>';
      return;
    }
    if (t.last_lat != null && t.dest_lat != null)
      destLine = L.polyline([[t.last_lat, t.last_lng], [t.dest_lat, t.dest_lng]], { color: cssColor('--accent'), weight: 2, dashArray: '6 6' }).addTo(map);
    $('d-title').textContent = `${t.code} · ${t.transfer_code}`;
    $('d-signal').textContent = LABEL[t.signal]; $('d-signal').className = 'badge ' + BADGE[t.signal];
    const speed = t.last_speed != null ? (t.last_speed * 3.6).toFixed(0) + ' km/h' : '–';
    const dist = t.dest_m != null ? (t.dest_m / 1000).toFixed(1) + ' km (straight line)' : '–';
    $('details').innerHTML = `
      <dl class="dl">
        <dt>Driver</dt><dd>${esc(t.driver_name)}</dd>
        <dt>Vehicle</dt><dd>${esc(t.vehicle_code || '–')} ${esc(t.plate || '')}</dd>
        <dt>To</dt><dd>${esc(t.to_name)}${t.priority === 'urgent' ? ' <span class="badge b-pending">urgent</span>' : ''}</dd>
        <dt>Status</dt><dd>${t.status === 'arrived' ? 'Driver says arrived · waiting for receipt' : 'On the way'}</dd>
        <dt>Last fix</dt><dd>${ago(t.age_s)}</dd>
        <dt>Speed</dt><dd>${speed}</dd>
        <dt>To shop</dt><dd>${dist}</dd>
        <dt>ETA</dt><dd>${hm(t.eta_at)}${t.eta_source === 'approximate' ? ' <span class="muted">(approximate)</span>' : ''}</dd>
      </dl>
      <div style="display:flex;gap:8px;margin-top:16px;flex-wrap:wrap">
        <button class="btn primary sm" id="confirm">Confirm receipt</button>
        <a class="btn sm" href="/tracking/trips/${t.id}">History</a>
      </div>
      <p class="muted" style="font-size:12.5px;margin:10px 0 0">Confirm only after the shop says the goods arrived. GPS alone never confirms receipt.</p>`;
    $('confirm').onclick = async () => {
      if (!confirm(`Confirm that ${t.to_name} received ${t.transfer_code}? This delivers the transfer.`)) return;
      const r = await fetch(`/api/trips/${t.id}/confirm`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) return alert(j.error || 'Could not confirm');
      selected = null; poll();
    };
  }

  function select(id) {
    selected = id;
    const m = markers.get(id);
    if (m) map.panTo(m.getLatLng());
    render();
  }

  async function poll() {
    try {
      const r = await fetch(window.LIVE_API, { headers: { Accept: 'application/json' } });
      if (!r.ok) throw new Error(r.status);
      data = await r.json();
      if (!fenced) drawFences();
      $('updated').textContent = 'Updated ' + data.server_time.slice(11);
      $('updated').className = 'badge plain';
      render();
    } catch (e) {
      $('updated').textContent = 'Connection lost · retrying';
      $('updated').className = 'badge b-warn';
    }
  }

  ['f-status', 'f-signal', 'f-alerts'].forEach(id => $(id).addEventListener('change', render));
  $('f-q').addEventListener('input', render);
  poll();
  setInterval(poll, POLL_MS);
})();
