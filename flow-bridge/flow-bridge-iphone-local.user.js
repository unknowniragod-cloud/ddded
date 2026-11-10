// ==UserScript==
// @name         Flow Bridge iPhone — Local Only
// @namespace    https://github.com/unknowniragod-cloud/ddded
// @version      1.1.0
// @description  Private on-device prompt queue for Google Flow in iPhone Safari. No server, account, or API token.
// @match        https://labs.google/fx/tools/flow/*
// @match        https://flow.google.com/*
// @run-at       document-idle
// @grant        none
// ==/UserScript==

(() => {
  'use strict';
  if (window.top !== window.self || document.getElementById('fb-local')) return;

  // This queue is stored in localStorage on the current Google Flow origin.
  // It is local to this browser/device and is not synced to a cloud server.
  const STORE = 'flowBridgeLocalQueueV1';
  const ACTIVE = 'flowBridgeLocalActiveV1';
  const HISTORY = 'flowBridgeLocalHistoryV1';
  const read = (key, fallback) => {
    try { const value = JSON.parse(localStorage.getItem(key)); return value ?? fallback; }
    catch (_) { return fallback; }
  };
  const write = (key, value) => localStorage.setItem(key, JSON.stringify(value));
  let queue = read(STORE, []);
  let active = read(ACTIVE, null);
  let history = read(HISTORY, []);
  let paused = true;
  let collapsed = false;
  let autoGenerate = read('flowBridgeAutoGenerateV1', true);
  let observer = null;

  const id = () => 'local-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 7);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const persist = () => { write(STORE, queue); write(ACTIVE, active); write(HISTORY, history.slice(0, 50)); render(); };
  const style = document.createElement('style');
  style.textContent = `
    #fb-local{position:fixed;z-index:2147483647;right:8px;bottom:calc(10px + env(safe-area-inset-bottom));width:min(360px,calc(100vw - 16px));max-height:78dvh;overflow:hidden;background:#151b27;color:#f4f6fb;border:1px solid #46536a;border-radius:15px;box-shadow:0 10px 34px #0009;font:13px -apple-system,BlinkMacSystemFont,sans-serif}
    #fb-local *{box-sizing:border-box}#fbl-head{display:flex;justify-content:space-between;align-items:center;padding:11px;background:#222d40;font-weight:700}#fbl-head small{display:block;font-size:10px;color:#b5c2d7;font-weight:400}
    #fbl-body{padding:10px;display:grid;gap:8px;max-height:calc(78dvh - 45px);overflow:auto;-webkit-overflow-scrolling:touch}#fb-local.mini #fbl-body{display:none}
    #fb-local textarea,#fb-local input,#fb-local select{width:100%;border:1px solid #4b5a73;border-radius:8px;background:#0d1420;color:white;padding:9px;font:12px -apple-system,BlinkMacSystemFont,sans-serif}
    #fb-local textarea{min-height:85px;resize:vertical}#fb-local button{border:0;border-radius:8px;background:#426ce0;color:white;padding:10px 8px;min-height:38px;font-weight:650;font-size:12px}#fb-local button.alt{background:#354258}#fb-local button.red{background:#71414c}#fbl-actions{display:grid;grid-template-columns:1fr 1fr;gap:6px}#fbl-auto{display:flex;align-items:center;gap:8px;padding:7px 9px;background:#222d40;border-radius:8px;font-size:11px}#fbl-auto input{width:auto;min-height:auto}
    #fbl-state{padding:8px;background:#222d40;border-radius:8px;line-height:1.5;font-size:11px;overflow-wrap:anywhere}#fbl-list{display:grid;gap:5px}#fbl-list article{padding:7px;background:#0d1420;border:1px solid #354258;border-radius:8px;font-size:10px;line-height:1.4;overflow-wrap:anywhere}
    #fbl-log{white-space:pre-wrap;background:#0b111b;color:#b9c7dc;padding:8px;border-radius:8px;max-height:76px;overflow:auto;font:10px/1.45 ui-monospace,monospace}
    #fb-local .hint{font-size:10px;color:#aab7cc;line-height:1.4}
  `;
  document.documentElement.appendChild(style);
  const root = document.createElement('section');
  root.id = 'fb-local';
  root.innerHTML = `
    <div id="fbl-head"><div>Flow Bridge · Local <small id="fbl-sub">On-device queue · no cloud</small></div><button id="fbl-min" class="alt">−</button></div>
    <div id="fbl-body">
      <div id="fbl-state">Ready · ${queue.length} queued</div>
      <textarea id="fbl-prompt" placeholder="Ilagay ang prompt dito."></textarea>
      <div id="fbl-actions"><button id="fbl-add">Add to queue</button><button id="fbl-next" class="alt">Insert next prompt</button></div>
      <label id="fbl-auto"><input id="fbl-auto-check" type="checkbox"> Auto-click Generate after inserting prompt</label>
      <div id="fbl-actions"><button id="fbl-start">Start</button><button id="fbl-pause" class="alt">Pause</button><button id="fbl-done" class="alt">Mark done</button><button id="fbl-skip" class="red">Skip active</button></div>
      <div class="hint">Local-only: naka-save ang queue sa browser storage ng device na ito. Auto-click clicks only a visible, enabled Generate/Create button. Keep Safari open and monitor output.</div>
      <div id="fbl-list"></div>
      <div id="fbl-actions"><button id="fbl-export" class="alt">Export backup</button><button id="fbl-import" class="alt">Import backup</button></div>
      <input id="fbl-file" type="file" accept="application/json" style="display:none">
      <div id="fbl-log"></div>
    </div>`;
  document.body.appendChild(root);
  const $ = (s) => root.querySelector(s);
  let logs = [];
  const log = (message) => { logs.unshift(new Date().toLocaleTimeString() + ' ' + message); logs = logs.slice(0, 12); $('#fbl-log').textContent = logs.join('\n'); };
  function render() {
    const activeText = active ? 'Active: ' + active.prompt.slice(0, 90) : 'No active prompt';
    $('#fbl-state').textContent = (paused ? 'Paused' : 'Ready') + ' · ' + queue.length + ' queued · ' + activeText;
    $('#fbl-list').innerHTML = queue.slice(0, 8).map((job, index) => '<article><b>' + (index + 1) + '. ' + esc(job.prompt.slice(0, 135)) + '</b></article>').join('') || '<div class="hint">Wala pang queued prompts.</div>';
    $('#fbl-pause').textContent = paused ? 'Resume' : 'Pause';
    $('#fbl-auto-check').checked = autoGenerate;
    $('#fbl-done').disabled = !active;
    $('#fbl-skip').disabled = !active;
  }
  function findComposer() {
    const selectors = ['textarea[placeholder*="Generate"]','textarea[placeholder*="Describe"]','textarea[aria-label*="prompt" i]','textarea[placeholder*="prompt" i]','[contenteditable="true"][role="textbox"]','textarea'];
    for (const selector of selectors) {
      const el = document.querySelector(selector);
      if (el && el.getClientRects().length && !root.contains(el)) return el;
    }
    return null;
  }
  function insertPrompt(prompt) {
    const el = findComposer();
    if (!el) throw new Error('Hindi makita ang Flow prompt box. Buksan ang Flow project at piliin ang prompt field.');
    el.focus();
    if (el.isContentEditable) {
      el.textContent = prompt;
      el.dispatchEvent(new InputEvent('input', {bubbles:true,inputType:'insertText',data:prompt}));
    } else {
      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
      if (setter) setter.call(el, prompt); else el.value = prompt;
      el.dispatchEvent(new Event('input', {bubbles:true}));
      el.dispatchEvent(new Event('change', {bubbles:true}));
    }
    return el;
  }
  function clickGenerate() {
    if (!autoGenerate) { log('Auto-click off: prompt inserted; tap Generate manually when ready.'); return false; }
    const candidates = [...document.querySelectorAll('button,[role="button"]')].filter(el => {
      if (!el.getClientRects().length || el.disabled || el.getAttribute('aria-disabled') === 'true' || root.contains(el)) return false;
      const label = [el.innerText, el.getAttribute('aria-label'), el.getAttribute('data-testid'), el.getAttribute('title')].filter(Boolean).join(' ').trim();
      return /^(generate|create|generate video|create video)$/i.test(label);
    });
    const button = candidates.find(el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
    if (!button) { log('Prompt inserted, but no enabled Generate/Create button was found. Check Flow UI and click manually if needed.'); return false; }
    button.click();
    log('Clicked Generate/Create. Monitor the generation in Flow.');
    return true;
  }
  function activateNext() {
    if (active) { log('May active prompt pa. Mark done o Skip active muna.'); return; }
    if (!queue.length) { log('Empty ang queue. Magdagdag muna ng prompt.'); return; }
    const job = queue.shift();
    try {
      insertPrompt(job.prompt);
      active = {...job, startedAt:new Date().toISOString(), status:'active'};
      persist();
      log('Inserted prompt ' + job.id + '.');
      setTimeout(clickGenerate, 900);
    } catch (error) {
      queue.unshift(job);
      persist();
      log(error.message);
    }
  }
  $('#fbl-add').onclick = () => {
    const prompt = $('#fbl-prompt').value.trim();
    if (!prompt) { log('Maglagay muna ng prompt.'); return; }
    queue.push({id:id(),prompt,createdAt:new Date().toISOString(),status:'queued'});
    $('#fbl-prompt').value = '';
    persist();
    log('Added to local queue.');
  };
  $('#fbl-next').onclick = activateNext;
  $('#fbl-auto-check').onchange = (event) => { autoGenerate = event.target.checked; write('flowBridgeAutoGenerateV1', autoGenerate); render(); log(autoGenerate ? 'Auto-click enabled.' : 'Auto-click disabled.'); };
  $('#fbl-start').onclick = () => { paused = false; render(); activateNext(); log('Local queue started.'); };
  $('#fbl-pause').onclick = () => { paused = !paused; render(); log(paused ? 'Paused.' : 'Resumed.'); if (!paused && !active) activateNext(); };
  $('#fbl-done').onclick = () => {
    if (!active) return;
    history.unshift({...active,status:'completed',finishedAt:new Date().toISOString()});
    active = null; persist(); log('Marked completed.'); if (!paused) activateNext();
  };
  $('#fbl-skip').onclick = () => {
    if (!active) return;
    history.unshift({...active,status:'skipped',finishedAt:new Date().toISOString()});
    active = null; persist(); log('Active prompt skipped.'); if (!paused) activateNext();
  };
  $('#fbl-min').onclick = () => { collapsed = !collapsed; root.classList.toggle('mini',collapsed); };
  $('#fbl-export').onclick = () => {
    const blob = new Blob([JSON.stringify({version:1,queue,active,history},null,2)],{type:'application/json'});
    const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href=url; a.download='flow-bridge-local-backup.json'; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1500);
  };
  $('#fbl-import').onclick = () => $('#fbl-file').click();
  $('#fbl-file').onchange = async (event) => {
    const file = event.target.files?.[0]; if (!file) return;
    try {
      const data = JSON.parse(await file.text());
      if (!Array.isArray(data.queue)) throw new Error('Hindi valid ang backup file.');
      queue = data.queue.filter(x => x && typeof x.prompt === 'string').map(x => ({id:x.id||id(),prompt:x.prompt,createdAt:x.createdAt||new Date().toISOString(),status:'queued'}));
      if (active && data.active && typeof data.active.prompt === 'string') active = data.active;
      history = Array.isArray(data.history) ? data.history.slice(0,50) : history;
      persist(); log('Backup imported.');
    } catch (error) { log('Import failed: ' + error.message); }
    event.target.value = '';
  };
  window.addEventListener('storage', (event) => {
    if (event.key === STORE) { queue = read(STORE, []); render(); }
  });
  render();
  log('Local-only panel loaded. Add prompts to begin.');
})();