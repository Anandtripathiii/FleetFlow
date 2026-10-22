/* Trip history map: the GPS track as a line, start and last point, and the shop geofence. */
(() => {
  const { track, fence } = window.TRIP;
  const map = L.map('map');
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' }).addTo(map);
  new ResizeObserver(() => map.invalidateSize()).observe(document.getElementById('map'));
  const pts = track.map(p => [p[0], p[1]]);
  const line = L.polyline(pts, { color: cssColor('--accent'), weight: 3 }).addTo(map);
  const dot = (p, token, label) => L.circleMarker(p, { radius: 7, color: cssColor('--bg'), weight: 2, fillColor: cssColor(token), fillOpacity: 1 })
    .addTo(map).bindTooltip(label);
  dot(pts[0], '--muted', 'Start · ' + track[0][2].slice(11, 19));
  dot(pts[pts.length - 1], '--ok', 'Last point · ' + track[track.length - 1][2].slice(11, 19));
  const bounds = line.getBounds();
  if (fence) {
    const c = L.circle([fence.lat, fence.lng], { radius: fence.radius_m, color: cssColor('--info'), weight: 1.5, fillOpacity: .12 })
      .addTo(map).bindTooltip(fence.name);
    bounds.extend(c.getLatLng());
  }
  map.fitBounds(bounds.pad(0.2), { maxZoom: 16 });
})();
