/* Driver page: share real GPS fixes, report issues, mark arrived.
   No fake positions: without permission or signal we tell the server and the driver, and send nothing. */
(() => {
  const $ = id => document.getElementById(id);
  const card = $('trip'), tripId = card && card.dataset.id;
  const SEND_EVERY_MS = 5000, MAX_QUEUE = 500, BATCH = 100;
  let watch = null, queue = [], lastSend = 0, lastFix = null, lastError = null, wakeLock = null, sending = false;

  function toast(msg, kind = 'info') {
    const box = document.querySelector('.flashes'), el = document.createElement('div');
    el.className = 'flash ' + kind;
    el.innerHTML = '<span></span><button aria-label="Close">×</button>';
    el.querySelector('span').textContent = msg;
    el.querySelector('button').onclick = () => el.remove();
    box.appendChild(el);
    setTimeout(() => el.remove(), 6000);
  }

  async function api(url, body) {
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) { const e = new Error(data.error || 'Request failed'); e.status = r.status; throw e; }
    return data;
  }

  function show(badge, cls, text) {
    const b = $('gps-badge');
    b.textContent = badge; b.className = 'badge ' + cls;
    $('gps-text').textContent = text;
  }

  // ---------------------------------------------------------------- GPS
  async function reportError(kind) {            // tell the manager once per change, never invent a point
    if (lastError === kind) return;
    lastError = kind;
    try { await api(`/api/trips/${tripId}/locations`, { error: kind }); } catch (e) { /* shown on screen already */ }
  }

  function onFix(pos) {
    const c = pos.coords, num = v => (v === null || Number.isNaN(v) ? null : v);
    lastFix = c; lastError = null;
    queue.push({ lat: c.latitude, lng: c.longitude, accuracy: num(c.accuracy), speed: num(c.speed),
      heading: num(c.heading), timestamp: pos.timestamp });
    if (queue.length > MAX_QUEUE) queue = queue.slice(-MAX_QUEUE);
    $('gps-last').textContent = new Date(pos.timestamp).toLocaleTimeString();
    $('gps-acc').textContent = Math.round(c.accuracy) + ' m' + (c.accuracy > 100 ? ' (weak signal)' : '');
    show('Sharing', 'b-ok', 'Your location is being shared with the manager.');
    if (Date.now() - lastSend >= SEND_EVERY_MS) flush();
    $('gps-queue').textContent = queue.length + ' points';
  }

  function onError(err) {
    if (err.code === 1) {
      stop();
      show('Blocked', 'b-warn', 'Location permission is off. Allow location for this site in your browser settings, then tap "Share my location" again. The manager sees "no permission", not a guessed position.');
      reportError('permission_denied');
    } else {
      show('No signal', 'b-warn', 'Your phone cannot get a GPS fix right now. Move to open sky. We keep trying and send nothing until there is a real fix.');
      reportError(err.code === 3 ? 'timeout' : 'position_unavailable');
    }
  }

  async function flush() {
    if (sending || !queue.length) return;
    sending = true;
    const batch = queue.slice(0, BATCH);
    try {
      await api(`/api/trips/${tripId}/locations`, { points: batch, source: 'browser' });
      queue = queue.slice(batch.length);
      lastSend = Date.now();
    } catch (e) {
      if (e.status === 409) { stop(); location.reload(); return; }   // trip closed elsewhere
      show('Offline', 'b-warn', 'No connection. Points are kept on this phone and sent when the network is back.');
    } finally {
      sending = false;
      $('gps-queue').textContent = queue.length + ' points';
    }
  }

  async function keepAwake() {
    try { if ('wakeLock' in navigator) wakeLock = await navigator.wakeLock.request('screen'); } catch (e) { /* not allowed: fine */ }
  }

  function start() {
    if (!('geolocation' in navigator)) {
      show('Not supported', 'b-warn', 'This browser cannot share location. Use Chrome or Safari on your phone.');
      return reportError('position_unavailable');
    }
    if (!window.isSecureContext) {
      show('Needs HTTPS', 'b-warn', 'Browsers only share location on a secure (https) page. Open the site with https.');
      return reportError('position_unavailable');
    }
    if (watch !== null) return;
    show('Starting', '', 'Waiting for the first GPS fix… allow location if your browser asks.');
    watch = navigator.geolocation.watchPosition(onFix, onError, { enableHighAccuracy: true, maximumAge: 5000, timeout: 20000 });
    $('share').textContent = 'Stop sharing';
    keepAwake();
  }

  function stop() {
    if (watch !== null) navigator.geolocation.clearWatch(watch);
    watch = null;
    if ($('share')) $('share').textContent = 'Share my location';
    if (wakeLock) { wakeLock.release().catch(() => {}); wakeLock = null; }
  }

  // ---------------------------------------------------------------- buttons
  if ($('start')) $('start').onclick = async () => {
    $('start').disabled = true;
    try {
      await api('/api/trips', { transfer_id: +$('transfer').value, vehicle_id: +$('vehicle').value });
      location.reload();
    } catch (e) { toast(e.message, 'error'); $('start').disabled = false; }
  };

  if ($('share')) {
    $('share').onclick = () => (watch === null ? start() : (stop(), show('Off', '', 'Location sharing is off. The manager will see you as offline.')));
    // already allowed before: start straight away
    if (navigator.permissions) navigator.permissions.query({ name: 'geolocation' }).then(p => p.state === 'granted' && start()).catch(() => {});
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible' && watch !== null) { keepAwake(); flush(); }
    });
    setInterval(flush, SEND_EVERY_MS);
  }

  if ($('arrived')) $('arrived').onclick = async () => {
    if (!confirm('Mark this delivery as arrived at the shop?')) return;
    await flush();
    try { await api(`/api/trips/${tripId}/arrived`); stop(); location.reload(); } catch (e) { toast(e.message, 'error'); }
  };

  if ($('issue')) $('issue').onclick = async () => {
    const body = { kind: $('issue-kind').value, note: $('issue-note').value };
    if (lastFix) { body.lat = lastFix.latitude; body.lng = lastFix.longitude; }
    try { await api(`/api/trips/${tripId}/issues`, body); $('issue-note').value = ''; toast('Sent to the manager.', 'success'); }
    catch (e) { toast(e.message, 'error'); }
  };
})();
