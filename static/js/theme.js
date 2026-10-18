/* FleetFlow theme helpers: colour tokens for canvas/Chart.js, theme switching. */
(() => {
  const root = document.documentElement;
  let probe = null;

  // Resolve a CSS colour token (e.g. '--accent') to an rgb() string the canvas understands.
  // Tokens use light-dark(), so they must be resolved through a real element.
  window.cssColor = (name, alpha) => {
    if (!probe) {
      probe = document.createElement('i');
      probe.style.cssText = 'position:absolute;width:0;height:0;visibility:hidden';
      document.body.appendChild(probe);
    }
    probe.style.color = alpha == null ? `var(${name})` : `color-mix(in srgb, var(${name}) ${alpha * 100}%, transparent)`;
    return getComputedStyle(probe).color;
  };

  // Run fn now and again whenever the theme changes (used by charts).
  window.onTheme = fn => { fn(); addEventListener('themechange', fn); };

  window.setTheme = t => {
    root.dataset.theme = t;
    try { localStorage.setItem('ff-theme', t); } catch (e) {}
    dispatchEvent(new Event('themechange'));
  };

  // Follow the system setting until the user picks a theme themselves.
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', e => {
    let saved = null;
    try { saved = localStorage.getItem('ff-theme'); } catch (err) {}
    if (!saved) { root.dataset.theme = e.matches ? 'dark' : 'light'; dispatchEvent(new Event('themechange')); }
  });
})();
