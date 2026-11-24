/* NEXUS provenance probe — paste into the browser console, send one message,
 * then run NEXUS_PROBE.report().
 *
 * WHY THIS ONE IS DIFFERENT FROM THE LAST FOUR
 *     Every previous round instrumented a PAGE FUNCTION (`handleFrame`,
 *     `sendMessage`) and concluded from silence. Silence has three causes and
 *     only one of them is about the logic:
 *
 *       1. the page in the browser is not the file in the checkout;
 *       2. the call site does not resolve through the binding that was
 *          replaced — `send.onclick = sendMessage` (nexus_ui.html:1757)
 *          captures the VALUE, so a later `window.sendMessage = wrapper`
 *          can never fire from the Send button;
 *       3. the code ran and did nothing.
 *
 *     A wrapper that does not fire cannot distinguish them. This probe hooks
 *     BROWSER APIs — `window.fetch` and `MutationObserver` — which are the
 *     same surfaces the page itself already re-wraps successfully at
 *     nexus_ui.html:11, so the mechanism is proven to work in this page. It
 *     assumes nothing about page function identity or scope.
 *
 * WHAT IT ANSWERS, IN ONE TURN
 *     A. WHICH PAGE is loaded, by marker strings and by /healthz's digest of
 *        the file the server would serve.
 *     B. WHAT THE SERVER SENT, read off a tee of the /chat response body
 *        before the page ever sees it.
 *     C. WHAT THE DOM DID, from a MutationObserver on #chat-log — every node
 *        added or removed, whatever code added or removed it.
 *
 *     If B contains a `done` frame with a lineage_id and C never shows a
 *     `.prov-toggle` added, the handler is the problem. If C shows one added
 *     and then removed, something else removes it. If A says the markers are
 *     absent, every previous round measured a page that does not contain the
 *     code under discussion, and that is the whole answer.
 */
(() => {
  const P = {
    identity: {}, frames: [], doneFrames: [], mutations: [],
    started: new Date().toISOString(), errors: [],
  };
  const oops = (where, e) => P.errors.push(where + ': ' + (e && e.message || e));

  // ---- A. identity -------------------------------------------------------
  // Marker strings, not a hash: the browser cannot hash the file on disk, and
  // the served response has a token injected into <head> so its hash never
  // matches a checkout anyway (that is why /healthz hashes the FILE).
  try {
    const html = document.documentElement.innerHTML;
    P.identity.url = location.href;
    P.identity.scripts = document.scripts.length;
    P.identity.markers = {
      // the provenance feature itself — if this is false the page predates it
      attachProvenance: html.indexOf('attachProvenance') !== -1,
      prov_toggle_css:  html.indexOf('prov-toggle') !== -1,
      // the SSE stream-resilience guard, added after the provenance work
      frame_guard:      html.indexOf('ONE BAD FRAME MUST NOT COST THE TURN') !== -1,
      // the /healthz frontend declaration
      healthz_decl:     html.indexOf("fetch('/healthz')") !== -1,
    };
    // Diagnostic only. Not load-bearing: see the header note on why a page
    // function's identity cannot decide anything here.
    P.identity.bindings = {
      handleFrame:      typeof window.handleFrame,
      attachProvenance: typeof window.attachProvenance,
      sendMessage:      typeof window.sendMessage,
      // THE TRAP FROM LAST ROUND, made visible: if this is false, a wrapper
      // on window.sendMessage cannot fire from the Send button no matter
      // what else is true.
      send_onclick_is_global_sendMessage: (() => {
        try {
          const b = document.querySelector('#send');
          return !!b && b.onclick === window.sendMessage;
        } catch (e) { return 'n/a'; }
      })(),
    };
  } catch (e) { oops('identity', e); }

  // ---- B. what the server sent ------------------------------------------
  // Tee the /chat body. The page calls the global `fetch` identifier at call
  // time (nexus_ui.html:1595), so wrapping window.fetch is seen — and unlike
  // a page function this is a browser API the page has already proven it
  // resolves globally.
  try {
    const prev = window.fetch;
    window.fetch = async function (input, init) {
      const url = (typeof input === 'string') ? input : (input && input.url) || '';
      const resp = await prev.call(this, input, init);
      if (url.indexOf('/chat') === -1 || !resp.body) return resp;
      const [a, b] = resp.body.tee();
      (async () => {
        try {
          const rd = b.getReader(), dec = new TextDecoder();
          let buf = '';
          for (;;) {
            const { value, done } = await rd.read();
            if (done) break;
            buf += dec.decode(value, { stream: true });
            let i;
            while ((i = buf.indexOf('\n\n')) !== -1) {
              const frame = buf.slice(0, i); buf = buf.slice(i + 2);
              const ev = (frame.split('\n').find(l => l.indexOf('event: ') === 0) || '').slice(7);
              P.frames.push(ev || '(unnamed)');
              if (ev === 'done') P.doneFrames.push(frame);
            }
          }
        } catch (e) { oops('tee', e); }
      })();
      return new Response(a, {
        status: resp.status, statusText: resp.statusText, headers: resp.headers,
      });
    };
  } catch (e) { oops('fetch-hook', e); }

  // ---- C. what the DOM did ----------------------------------------------
  try {
    const target = document.querySelector('#chat-log');
    if (!target) { oops('observer', 'no #chat-log in this page'); }
    else {
      const name = n => {
        if (!n) return '?';
        if (n.nodeType === 3) return '#text';
        return (n.tagName || '?').toLowerCase() + (n.className ? '.' + String(n.className).trim().split(/\s+/).join('.') : '');
      };
      new MutationObserver(muts => {
        for (const m of muts) {
          for (const n of m.addedNodes)
            P.mutations.push({ t: Math.round(performance.now()), op: 'add', node: name(n) });
          for (const n of m.removedNodes)
            P.mutations.push({ t: Math.round(performance.now()), op: 'remove', node: name(n) });
        }
      }).observe(target, { childList: true, subtree: true });
    }
  } catch (e) { oops('observer', e); }

  // ---- report ------------------------------------------------------------
  window.NEXUS_PROBE = {
    _: P,
    async report() {
      let hz = null;
      try { hz = (await (await fetch('/healthz')).json()).frontend; } catch (e) { hz = String(e); }
      const provAdds = P.mutations.filter(m => m.node.indexOf('prov-toggle') !== -1);
      const counts = P.frames.reduce((a, e) => (a[e] = (a[e] || 0) + 1, a), {});
      const out = [
        '===== NEXUS PROVENANCE PROBE =====',
        'A. PAGE IDENTITY',
        '  url            : ' + P.identity.url,
        '  inline scripts : ' + P.identity.scripts,
        '  markers        : ' + JSON.stringify(P.identity.markers),
        '  bindings       : ' + JSON.stringify(P.identity.bindings),
        '  /healthz file  : ' + JSON.stringify(hz),
        '',
        'B. WHAT THE SERVER SENT (tee, before the page saw it)',
        '  frames         : ' + (P.frames.length ? JSON.stringify(counts) : 'NONE — no /chat stream was captured'),
        '  done payload   : ' + (P.doneFrames.length ? P.doneFrames[P.doneFrames.length - 1] : 'NO done FRAME'),
        '',
        'C. WHAT THE DOM DID (#chat-log)',
        '  mutations      : ' + P.mutations.length,
        '  prov-toggle    : ' + (provAdds.length ? JSON.stringify(provAdds) : 'NEVER ADDED OR REMOVED'),
        '  last 12        : ' + JSON.stringify(P.mutations.slice(-12)),
        '',
        '  errors         : ' + (P.errors.length ? JSON.stringify(P.errors) : 'none'),
        '=================================',
      ].join('\n');
      console.log(out);
      return out;
    },
  };
  console.log('[NEXUS_PROBE] armed. Send ONE message, then run: NEXUS_PROBE.report()');
})();
