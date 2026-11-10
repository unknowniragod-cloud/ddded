// ==UserScript==
// @name         Flow Bridge iPhone Connector
// @namespace    https://github.com/unknowniragod-cloud/ddded
// @version      1.0.0
// @description  iPhone Safari cloud queue connector for an already signed-in Google Flow tab.
// @match        https://labs.google/fx/tools/flow/*
// @match        https://flow.google.com/*
// @run-at       document-idle
// @grant        GM_getValue
// @grant        GM_setValue
// ==/UserScript==

(() => {
  'use strict';
  if (window.top !== window.self || document.getElementById('fb-mobile')) return;

  const KEY = 'flowBridgeMobileSettings';
  const defaults = { apiUrl: '', token: '', workerId: 'iphone-' + Math.random().toString(36).slice(2, 10), enabled: false, mode:'auto', model:'', ratio:'auto', resolution:'720p', duration:'8s' };
  let stored = {};
  try { stored = GM_getValue ? GM_getValue(KEY, {}) : JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (_) {}
  let cfg = { ...defaults, ...(stored || {}) };
  let current = null, paused = false, timer = null, heartbeatTimer = null, logLines = [];
  const save = () => { try { if (GM_setValue) GM_setValue(KEY, cfg); else localStorage.setItem(KEY, JSON.stringify(cfg)); } catch (_) {} };
  const log = (s) => { logLines.unshift(new Date().toLocaleTimeString() + ' ' + s); logLines = logLines.slice(0, 35); const el = document.querySelector('#fb-log'); if (el) el.textContent = logLines.join('\n'); };
  const api = async (path, method='GET', body=null) => {
    if (!cfg.apiUrl || !cfg.token) throw new Error('Set API URL and Bearer token in Settings');
    const res = await fetch(cfg.apiUrl.replace(/\/$/, '') + path, {
      method, headers: { 'Authorization': 'Bearer ' + cfg.token, 'Content-Type': 'application/json', 'X-Worker-ID': cfg.workerId },
      body: body ? JSON.stringify(body) : undefined
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || ('HTTP ' + res.status));
    return data;
  };
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const style = document.createElement('style');
  style.textContent = `
    #fb-mobile{position:fixed;z-index:2147483647;right:8px;bottom:calc(10px + env(safe-area-inset-bottom));width:min(355px,calc(100vw - 16px));max-height:78dvh;overflow:hidden;background:#151b27;color:#f4f6fb;border:1px solid #46536a;border-radius:15px;box-shadow:0 10px 34px #0009;font:13px -apple-system,BlinkMacSystemFont,sans-serif}
    #fb-mobile *{box-sizing:border-box}#fb-head{display:flex;justify-content:space-between;align-items:center;padding:11px;background:#222d40;font-weight:700}#fb-head small{display:block;font-size:10px;color:#b5c2d7;font-weight:400}
    #fb-body{padding:10px;display:grid;gap:8px;max-height:calc(78dvh - 45px);overflow:auto;-webkit-overflow-scrolling:touch}#fb-mobile.mini #fb-body{display:none}
    #fb-mobile textarea,#fb-mobile input{width:100%;border:1px solid #4b5a73;border-radius:8px;background:#0d1420;color:white;padding:9px;font:12px -apple-system,BlinkMacSystemFont,sans-serif}
    #fb-mobile textarea{min-height:85px;resize:vertical}#fb-mobile button{border:0;border-radius:8px;background:#426ce0;color:white;padding:10px 8px;min-height:37px;font-weight:650;font-size:12px}
    #fb-mobile button.alt{background:#354258}#fb-mobile button.red{background:#71414c}#fb-actions{display:grid;grid-template-columns:1fr 1fr;gap:6px}
    #fb-state{padding:8px;background:#222d40;border-radius:8px;line-height:1.5;font-size:11px;overflow-wrap:anywhere}#fb-log{white-space:pre-wrap;background:#0b111b;color:#b9c7dc;padding:8px;border-radius:8px;max-height:100px;overflow:auto;font:10px/1.45 ui-monospace,monospace}
    #fb-mobile .hint{font-size:10px;color:#aab7cc;line-height:1.4}#fb-mobile label{font-size:10px;color:#bdc9dc;display:grid;gap:4px}
  `;
  document.documentElement.appendChild(style);
  const root = document.createElement('section'); root.id='fb-mobile';
  root.innerHTML = `
    <div id="fb-head"><div>Flow Bridge <small id="fb-connection">Not connected</small></div><button id="fb-min" class="alt">−</button></div>
    <div id="fb-body">
      <div id="fb-state">No active job. Configure the API connection to begin.</div>
      <textarea id="fb-prompt" placeholder="Prompt — one prompt per job"></textarea>
      <div id="fb-actions">
        <label>Mode<select id="fb-mode"><option value="auto">Auto</option><option value="image">Image</option><option value="video">Video</option><option value="text_to_image">Text to image</option><option value="image_to_video">Image to video</option><option value="frames_to_video">Start/end frames</option><option value="ingredients_to_video">Ingredients</option></select></label>
        <label>Model<input id="fb-model" placeholder="Flow model (optional)"></label>
        <label>Aspect ratio<select id="fb-ratio"><option value="auto">Flow default</option><option>16:9</option><option>9:16</option><option>1:1</option><option>4:3</option><option>3:4</option></select></label>
        <label>Resolution<select id="fb-resolution"><option>720p</option><option>1080p</option><option>2K</option><option>4K</option></select></label>
        <label>Duration<select id="fb-duration"><option>5s</option><option>8s</option><option>10s</option></select></label>
      </div>
      <textarea id="fb-references" placeholder="Optional reference image URLs (one HTTPS URL per line; metadata only, not auto-attached)"></textarea>
      <input id="fb-result-url" type="url" placeholder="Optional verified result URL">
      <div id="fb-actions"><button id="fb-submit">Add to cloud queue</button><button id="fb-poll" class="alt">Check queue</button></div>
      <div id="fb-actions"><button id="fb-start">Start worker</button><button id="fb-pause" class="alt">Pause</button><button id="fb-stop" class="red">Stop worker</button><button id="fb-complete" class="alt" disabled>Mark completed</button></div>
      <div class="hint">Generation stays in this signed-in Flow tab. Review the prompt, tap Flow's own Generate button yourself, then confirm completion here. This script does not create trusted taps or bypass CAPTCHA.</div>
      <details><summary>Connection settings</summary><label>Bridge API URL<input id="fb-url" placeholder="https://your-service.onrender.com"></label><label>Bearer token<input id="fb-token" type="password" autocomplete="off" placeholder="Paste Render secret"></label><button id="fb-save" class="alt">Save settings</button><div id="fb-actions"><button id="fb-export" class="alt">Export settings</button><button id="fb-import" class="alt">Import settings</button></div><input id="fb-import-file" type="file" accept="application/json" style="display:none"></details>
      <div id="fb-log"></div>
    </div>`;
  document.body.appendChild(root);
  const $ = (s) => root.querySelector(s);
  $('#fb-url').value=cfg.apiUrl; $('#fb-token').value=cfg.token; $('#fb-mode').value=cfg.mode; $('#fb-model').value=cfg.model; $('#fb-ratio').value=cfg.ratio; $('#fb-resolution').value=cfg.resolution; $('#fb-duration').value=cfg.duration;
  $('#fb-min').onclick=()=>root.classList.toggle('mini');
  $('#fb-save').onclick=()=>{cfg.apiUrl=$('#fb-url').value.trim();cfg.token=$('#fb-token').value.trim();save();log('Settings saved on this device.');checkHealth();};
  const stateText=(s)=>$('#fb-state').textContent=s;
  async function refreshQueue(){try{const q=await api('/api/jobs?limit=1');const c=q.counts||{};$('#fb-connection').textContent='Connected · queue '+(c.queued||0)+' · running '+(c.running||0); }catch(_){}}
  async function checkHealth(){try{const h=await api('/api/health');$('#fb-connection').textContent='Connected · '+h.version;refreshQueue();return true;}catch(e){$('#fb-connection').textContent='Disconnected';log('Connection: '+e.message);return false;}}
  async function submit(){
    const prompt=$('#fb-prompt').value.trim(); if(!prompt) return log('Enter a prompt first.');
    try{cfg.mode=$('#fb-mode').value;cfg.model=$('#fb-model').value.trim();cfg.ratio=$('#fb-ratio').value;cfg.resolution=$('#fb-resolution').value;cfg.duration=$('#fb-duration').value;save();
      const references=$('#fb-references').value.split(/\\n+/).map(x=>x.trim()).filter(Boolean).map(url=>({url,kind:'image'}));
      const j=await api('/api/jobs','POST',{prompt,mode:cfg.mode,model:cfg.model||null,aspect_ratio:cfg.ratio==='auto'?null:cfg.ratio,references,options:{source:'iphone-safari',resolution:cfg.resolution,duration:cfg.duration}});$('#fb-prompt').value='';log('Queued '+j.id);stateText('Queued job '+j.id);}
    catch(e){log('Submit failed: '+e.message);}
  }
  function findComposer(){
    const selectors=['textarea[placeholder*="Generate"]','textarea[placeholder*="Describe"]','textarea[aria-label*="prompt" i]','textarea[placeholder*="prompt" i]','[contenteditable="true"][role="textbox"]','textarea'];
    for(const s of selectors){const el=document.querySelector(s);if(el && el.getClientRects().length && !root.contains(el))return el;}
    return null;
  }
  function fillPrompt(prompt){
    const el=findComposer(); if(!el) throw new Error('Flow prompt composer not found. Open a Flow project and select its prompt field.');
    el.focus();
    if(el.isContentEditable){el.textContent=prompt;el.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:prompt}));}
    else {const setter=Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el),'value')?.set; if(setter)setter.call(el,prompt);else el.value=prompt;el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));}
    return el;
  }
  async function claim(){
    if(paused||!cfg.enabled||current)return;
    try{
      const data=await api('/api/next');
      if(!data.job)return;
      current=data.job; fillPrompt(current.prompt);
      stateText('Claimed '+current.id+' · '+current.mode+'\nPrompt inserted. Review settings and press Generate in Flow manually.');
      $('#fb-complete').disabled=false; log('Claimed '+current.id+' and inserted prompt.');
      heartbeatTimer=setInterval(()=>api('/api/jobs/'+current.id+'/heartbeat','POST',{progress:25,message:'Waiting for user-operated Flow generation'}).catch(e=>log('Heartbeat: '+e.message)),45000);
    }catch(e){log('Worker error: '+e.message);if(current)await report('failed',e.message);}
  }
  async function report(status,error=''){
    if(!current)return;
    const job=current;
    try{
      const supplied=$('#fb-result-url').value.trim();
      const result={flow_page:location.href,completed_at:new Date().toISOString(),browser:'iPhone Safari'};
      if(supplied){if(!/^https:\/\//i.test(supplied)){log('Result URL must start with https://');return;}result.result_url=supplied;}

      await api('/api/jobs/'+job.id+'/status','POST',{status,progress:status==='completed'?100:0,result,error:error||null});
      log('Reported '+status+' for '+job.id);
    }catch(e){log('Could not report '+status+': '+e.message);}
    clearInterval(heartbeatTimer);heartbeatTimer=null;current=null;$('#fb-complete').disabled=true;stateText('No active job.'); 
  }
  $('#fb-submit').onclick=submit;
  $('#fb-poll').onclick=()=>checkHealth().then(()=>claim());
  $('#fb-start').onclick=async()=>{if(!await checkHealth())return;cfg.enabled=true;paused=false;save();if(timer)clearInterval(timer);timer=setInterval(claim,10000);log('Worker started. Keep this Flow tab open and active.');claim();};
  $('#fb-pause').onclick=async()=>{paused=!paused;$('#fb-pause').textContent=paused?'Resume':'Pause';try{await api('/api/queue/control','POST',{action:paused?'pause':'resume'});}catch(e){log('Queue control: '+e.message);}log(paused?'Worker paused.':'Worker resumed.');if(!paused)claim();};
  $('#fb-stop').onclick=()=>{cfg.enabled=false;paused=true;save();if(timer)clearInterval(timer);timer=null;log('Worker stopped. Active job remains leased until completion or timeout.');};
  $('#fb-complete').onclick=()=>report('completed');
  $('#fb-export').onclick=()=>{const blob=new Blob([JSON.stringify(cfg,null,2)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='flow-bridge-settings.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);};
  $('#fb-import').onclick=()=>$('#fb-import-file').click();
  $('#fb-import-file').onchange=async(e)=>{const file=e.target.files?.[0];if(!file)return;try{const imported=JSON.parse(await file.text());if(typeof imported.apiUrl!=='string'||typeof imported.token!=='string')throw new Error('Invalid settings file');cfg={...defaults,...imported};save();$('#fb-url').value=cfg.apiUrl;$('#fb-token').value=cfg.token;log('Settings imported.');checkHealth();}catch(err){log('Import failed: '+err.message);}e.target.value='';};
  checkHealth();
  log('Connector loaded. Configure your API URL and token.');
})();
