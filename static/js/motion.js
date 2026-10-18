/* Scroll reveals: whole sections and cards fade up once as they enter.
   Only elements fully below the fold are hidden, and only after GSAP has loaded,
   so nothing is ever blank if a script fails or the page opens mid-way. */
(() => {
  if (!window.gsap || !window.ScrollTrigger) return;
  gsap.registerPlugin(ScrollTrigger);

  const targets = gsap.utils.toArray(
    '[data-reveal], .content > .card, .content > .grid > .card, .content > .sim-pair > .card'
  );
  if (!targets.length) return;

  const mm = gsap.matchMedia();
  mm.add('(prefers-reduced-motion: no-preference)', () => {
    const below = targets.filter(el => el.getBoundingClientRect().top >= innerHeight);
    if (!below.length) return;
    gsap.set(below, { autoAlpha: 0, y: 14 });

    const pending = new Set(below);
    const reveal = els => {
      els = els.filter(el => pending.has(el));
      if (!els.length) return;
      els.forEach(el => pending.delete(el));
      gsap.to(els, { autoAlpha: 1, y: 0, duration: 0.45, ease: 'power3.out', stagger: 0.06, overwrite: true,
        clearProps: 'transform,opacity,visibility' });
    };

    // reveal as soon as any part of the element enters the viewport
    ScrollTrigger.batch(below, { start: 'top bottom', once: true, onEnter: reveal });

    // Safety sweep for anchor jumps, restored scroll and very fast scrolling:
    // anything in view animates in, anything already passed is shown at once.
    const sweep = () => {
      if (!pending.size) return;
      const inView = [];
      pending.forEach(el => {
        const r = el.getBoundingClientRect();
        if (r.bottom <= 0) { pending.delete(el); gsap.set(el, { clearProps: 'transform,opacity,visibility' }); }
        else if (r.top < innerHeight) inView.push(el);
      });
      reveal(inView);
    };
    const events = ['scrollend', 'load', 'hashchange'];
    events.forEach(e => addEventListener(e, sweep));
    addEventListener('load', () => ScrollTrigger.refresh());
    requestAnimationFrame(sweep);
    return () => events.forEach(e => removeEventListener(e, sweep));
  });
})();
