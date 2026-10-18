/* Page loading bar: starts when you open another page in the app (link or form),
   then finishes on the next page. Pages that load fast never show it. */
(() => {
  const root = document.documentElement;
  const KEY = 'ff-nav';
  const bar = document.createElement('div');
  bar.className = 'ff-progress';
  bar.setAttribute('aria-hidden', 'true');
  bar.innerHTML = '<i></i>';
  document.body.appendChild(bar);

  let timer = 0, safety = 0;
  const reset = () => {
    clearTimeout(timer); clearTimeout(safety);
    bar.classList.remove('run', 'done');
    root.classList.remove('ff-busy');
  };

  function start() {
    try { sessionStorage.setItem(KEY, '1'); } catch (e) {}
    clearTimeout(timer);
    timer = setTimeout(() => {               // fast navigations finish before this fires
      bar.classList.remove('done');
      bar.offsetWidth;                       // restart the grow transition
      bar.classList.add('run');
      root.classList.add('ff-busy');
    }, 90);
    safety = setTimeout(reset, 20000);       // navigation never happened (e.g. 204)
  }

  // On arrival: if we came from an in-app navigation, fill the bar and fade it out.
  let came = null;
  try { came = sessionStorage.getItem(KEY); sessionStorage.removeItem(KEY); } catch (e) {}
  if (came) {
    const i = bar.firstChild;
    i.style.transition = 'none';
    bar.classList.add('run');
    bar.offsetWidth;
    i.style.transition = '';
    requestAnimationFrame(() => bar.classList.add('done'));
  }

  document.addEventListener('click', e => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const a = e.target.closest('a[href]');
    if (!a || (a.target && a.target !== '_self') || a.hasAttribute('download')) return;
    const url = new URL(a.href, location.href);
    if (url.origin !== location.origin || !/^https?:$/.test(url.protocol)) return;
    if (url.pathname.endsWith('.csv')) return;                                   // file download, page stays
    if (url.pathname === location.pathname && url.search === location.search && url.hash) return;  // same-page jump
    start();
  });

  document.addEventListener('submit', e => {
    const f = e.target;
    if (e.defaultPrevented || (f.target && f.target !== '_self')) return;       // cancelled confirm, new tab
    start();
  });

  // Back/forward from the browser cache: clear any bar left running.
  addEventListener('pageshow', e => { if (e.persisted) reset(); });
})();
