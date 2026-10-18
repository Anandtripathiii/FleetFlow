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
    if (window.Chart) Chart.defaults.animation = false;   // charts redraw in place, bars don't regrow
    dispatchEvent(new Event('themechange'));
  };

  const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;

  // Theme switch: a circle grows out of the toggle (View Transitions API).
  // No API: short colour cross-fade. Reduced motion: instant.
  function toggleTheme(btn) {
    const next = root.dataset.theme === 'dark' ? 'light' : 'dark';
    if (reduced()) return setTheme(next);
    if (!document.startViewTransition) {
      root.classList.add('theme-fade');
      setTheme(next);
      setTimeout(() => root.classList.remove('theme-fade'), 350);
      return;
    }
    const r = btn.getBoundingClientRect();
    const x = r.left + r.width / 2, y = r.top + r.height / 2;
    const end = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
    root.classList.add('vt-theme');
    const vt = document.startViewTransition(() => setTheme(next));
    vt.ready.then(() => root.animate(
      { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${end}px at ${x}px ${y}px)`] },
      { duration: 500, easing: 'cubic-bezier(.2,0,0,1)', pseudoElement: '::view-transition-new(root)' }
    )).catch(() => {});
    vt.finished.finally(() => root.classList.remove('vt-theme'));
  }

  document.addEventListener('click', e => {
    const btn = e.target.closest('[data-theme-toggle]');
    if (btn) toggleTheme(btn);
  });

  // Follow the system setting until the user picks a theme themselves.
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', e => {
    let saved = null;
    try { saved = localStorage.getItem('ff-theme'); } catch (err) {}
    if (!saved) { root.dataset.theme = e.matches ? 'dark' : 'light'; dispatchEvent(new Event('themechange')); }
  });
})();
