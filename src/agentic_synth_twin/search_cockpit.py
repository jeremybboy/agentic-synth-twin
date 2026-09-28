"""Loopback-only live cockpit for direct real-synth optimization."""

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import parse_qs, urlparse

from .direct_search import DirectSearchError, SearchRun, create_search_run


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class SearchCockpitError(RuntimeError):
    """Raised when the cockpit violates its local execution boundary."""


def render_cockpit_page() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" href="data:,">
  <title>Agentic Synth Twin · Real Synth Search</title>
  <style>
    :root{color-scheme:dark;--bg:#07111e;--panel:#0d1a2a;--panel2:#101f32;--line:#23364c;--ink:#f4f7fb;--muted:#97a8bc;--blue:#39a0ff;--green:#38d58a;--orange:#ff9a45;--purple:#a978ff;--red:#ff5e6b}
    *{box-sizing:border-box}[hidden]{display:none!important} body{margin:0;background:radial-gradient(circle at 50% -20%,#17304b 0,var(--bg) 45%);color:var(--ink);font:14px/1.35 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif} button,select{font:inherit} button{cursor:pointer}
    main{width:min(1520px,calc(100% - 24px));margin:12px auto 28px}.top{display:grid;grid-template-columns:minmax(260px,1fr) auto minmax(600px,1.5fr);gap:12px;align-items:center;background:#091522;border:1px solid var(--line);border-radius:12px;padding:12px 16px;position:sticky;top:6px;z-index:3;box-shadow:0 12px 32px #0008}
    .brand h1{font-size:22px;margin:0}.brand p{margin:2px 0 0;color:var(--muted);font-weight:700}.controls{display:flex;gap:7px;flex-wrap:wrap}.btn{border:1px solid #354c66;background:#17283c;color:var(--ink);border-radius:7px;padding:10px 15px;font-weight:800}.btn.primary{background:#18ad66;border-color:#32d685}.btn:disabled{opacity:.38;cursor:not-allowed}
    .status-grid{display:grid;grid-template-columns:repeat(6,minmax(80px,1fr));gap:8px}.metric{border-left:1px solid var(--line);padding-left:10px}.metric span{display:block;color:var(--muted);font-size:10px;font-weight:800;letter-spacing:.06em;text-transform:uppercase}.metric strong{display:block;font-size:16px;margin-top:3px;font-variant-numeric:tabular-nums}.metric strong.good{color:var(--green)}.badge{display:inline-block;background:#0d68d4;color:#fff;border-radius:6px;padding:4px 8px;font-size:12px}
    .layout{display:grid;grid-template-columns:1.05fr 1.25fr 1.1fr;gap:10px;margin-top:10px}.panel{background:linear-gradient(160deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:10px;padding:12px;min-width:0}.panel h2{font-size:14px;margin:0 0 3px}.hint{color:var(--muted);font-size:12px;margin:0 0 10px}.wide{grid-column:span 2}.full{grid-column:1/-1}
    .players{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.player{background:#0a1624;border:1px solid var(--line);border-radius:9px;padding:10px}.player.best{border-color:#247d59}.player strong{display:block;margin-bottom:7px}.player audio{width:100%;height:35px}
    .target-bank{display:grid;grid-template-columns:repeat(5,minmax(130px,1fr));gap:7px}.target-option{border:1px solid #354c66;background:#102137;color:var(--ink);border-radius:7px;padding:9px 10px;text-align:left;font-weight:800}.target-option.selected{border-color:var(--blue);background:#123861;box-shadow:inset 3px 0 var(--blue)}.target-option:disabled{opacity:.45;cursor:not-allowed}.target-meta{display:flex;justify-content:space-between;gap:10px;align-items:center;margin-bottom:9px}.target-meta strong{font-size:15px}.provenance{color:var(--green);font-size:12px;font-weight:800}
    .parameter{margin:12px 0}.parameter-head{display:flex;justify-content:space-between;gap:10px}.parameter-head strong{font-size:13px}.value{font-variant-numeric:tabular-nums}.track{height:8px;background:#263a50;border-radius:5px;margin-top:7px;position:relative}.fill{height:100%;background:var(--blue);border-radius:5px;width:0}.knob{width:14px;height:14px;background:#fff;border:4px solid var(--blue);border-radius:50%;position:absolute;top:50%;left:0;transform:translate(-50%,-50%)}
    .instrument-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap}.instrument-controls{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.instrument-controls label{color:var(--muted);font-size:12px}.instrument-controls input[type=range]{width:125px;vertical-align:middle}.editable-parameters{display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:10px;margin:12px 0}.editable-parameter{background:#0a1624;border:1px solid var(--line);border-radius:8px;padding:9px}.editable-parameter label{display:flex;justify-content:space-between;font-size:12px;font-weight:700}.editable-parameter input{width:100%;accent-color:var(--green)}.editable-parameter small{display:block;color:var(--muted);font-variant-numeric:tabular-nums;margin-top:3px}.keyboard-wrap{overflow-x:auto;padding:4px 0}.keyboard{position:relative;height:152px;min-width:760px;user-select:none;touch-action:none}.piano-key{position:absolute;border:1px solid #08101a;border-radius:0 0 6px 6px;padding:0;display:flex;align-items:flex-end;justify-content:center;font-size:10px;font-weight:800}.piano-key.white{height:150px;background:linear-gradient(#fff,#cbd5df);color:#26384b;z-index:1}.piano-key.black{height:94px;background:linear-gradient(#26384b,#05080c);color:#dce9f5;z-index:2}.piano-key.active{background:linear-gradient(#38d58a,#168e5a);color:white}.play-status{color:var(--green);font-size:12px;font-weight:800;min-width:170px}.play-status.error{color:var(--red)}
    .distribution{margin:12px 0}.dist-head{display:flex;justify-content:space-between;color:var(--muted);font-size:12px}.dist-track{height:12px;background:#263a50;border-radius:7px;margin-top:6px;position:relative;overflow:hidden}.dist-range{height:100%;position:absolute;background:linear-gradient(90deg,#714fe6,#278cff);opacity:.75}.dist-mean{width:3px;height:100%;background:white;position:absolute}.dist-current{width:8px;height:8px;border:2px solid white;background:var(--green);border-radius:50%;position:absolute;top:2px;transform:translateX(-50%)}
    .breakdown-row{display:grid;grid-template-columns:92px 1fr 58px;align-items:center;gap:8px;margin:9px 0}.bar{height:9px;background:#263a50;border-radius:5px;overflow:hidden}.bar>i{display:block;height:100%;background:var(--purple);width:0}.bar.spectral>i{background:var(--blue)}.bar.envelope>i{background:var(--green)}.bar.loudness>i{background:var(--orange)}
    canvas{display:block;width:100%;height:250px;background:#071321;border:1px solid #1c3047;border-radius:7px}.spectra{display:grid;grid-template-columns:1fr 1fr;gap:9px}.spectra canvas{height:190px}.canvas-title{font-size:12px;color:var(--muted);margin:0 0 4px}
    .axis-controls{display:flex;gap:8px;margin-bottom:8px}.axis-controls label{color:var(--muted);font-size:12px}.axis-controls select{background:#14263a;border:1px solid var(--line);border-radius:6px;color:var(--ink);padding:6px}
    .info-grid{display:grid;grid-template-columns:1fr 1fr;gap:5px 16px}.info-grid div{display:flex;justify-content:space-between;border-bottom:1px solid #1a2c40;padding:6px 0}.info-grid span{color:var(--muted)}.info-grid strong{font-variant-numeric:tabular-nums}
    .table-wrap{max-height:255px;overflow:auto;border:1px solid #1c3047;border-radius:7px}table{width:100%;border-collapse:collapse;font-size:12px}th{position:sticky;top:0;background:#13253a;color:var(--muted);text-align:right}th,td{padding:7px 9px;border-bottom:1px solid #1d3045;white-space:nowrap;text-align:right;font-variant-numeric:tabular-nums}th:first-child,td:first-child{text-align:left}tbody tr{cursor:pointer}tbody tr:hover,tbody tr.selected{background:#17314b}.yes{color:var(--green)}
    .reveal{display:flex;align-items:center;justify-content:space-between;gap:12px}.reveal-output{color:var(--muted);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;overflow-wrap:anywhere}.error{color:var(--red)}footer{display:flex;justify-content:space-between;color:var(--muted);font-size:11px;margin-top:9px;padding:0 3px}
    @media(max-width:1100px){.top{grid-template-columns:1fr}.status-grid{grid-template-columns:repeat(3,1fr)}.layout{grid-template-columns:1fr 1fr}.full{grid-column:1/-1}.wide{grid-column:span 1}}
    @media(max-width:720px){main{width:min(100% - 12px,680px)}.layout{grid-template-columns:1fr}.wide,.full{grid-column:1}.players,.spectra{grid-template-columns:1fr}.target-bank{grid-template-columns:repeat(2,minmax(130px,1fr))}.status-grid{grid-template-columns:repeat(2,1fr)}.info-grid,.editable-parameters{grid-template-columns:1fr}.top{position:static}}
  </style>
</head>
<body>
<main>
  <header class="top">
    <div class="brand"><h1>Agentic Synth Twin</h1><p>REAL SYNTH SEARCH · TARGET AUDIO</p></div>
    <div class="controls">
      <button class="btn primary" id="start">▶ START SEARCH</button><button class="btn" id="pause">Ⅱ PAUSE</button><button class="btn" id="resume">▶ RESUME</button><button class="btn" id="stop">■ STOP</button>
    </div>
    <div class="status-grid">
      <div class="metric"><span>Status</span><strong><b class="badge" id="status">IDLE</b></strong></div><div class="metric"><span>Generation</span><strong id="generation">0 / 20</strong></div><div class="metric"><span>Evaluations</span><strong id="evaluations">0 / 160</strong></div><div class="metric"><span>Current loss</span><strong id="currentLoss">—</strong></div><div class="metric"><span>Best loss</span><strong id="bestLoss">—</strong></div><div class="metric"><span>Improvement</span><strong class="good" id="improvement">0.0%</strong></div>
    </div>
  </header>

  <section class="layout">
    <div class="panel full" id="targetBankPanel" hidden><div class="target-meta"><div><h2>Select external target</h2><p class="hint">Choose one named C3 reference, hear it, review the retrieved Top 5, then press Start Search.</p></div><span class="provenance" id="targetProvenance"></span></div><div class="target-bank" id="targetBank" role="group" aria-label="External target sounds"></div></div>
    <div class="panel full"><div class="players">
      <div class="player"><strong>TARGET · <span id="targetTitle">fixed regression reference</span></strong><small class="hint" id="targetSource">Parameters hidden during the regression experiment.</small><audio id="targetAudio" controls preload="metadata" src="/audio/target.wav"></audio></div>
      <div class="player"><strong id="basePlayerLabel">STARTING PATCH</strong><audio id="startAudio" controls preload="metadata" src="/audio/starting-patch.wav"></audio></div>
      <div class="player best"><strong>CURRENT BEST</strong><audio id="bestAudio" controls preload="none"></audio></div>
    </div></div>

    <div class="panel full">
      <div class="instrument-head"><div><h2>Playable patch</h2><p class="hint">Load Best or Base, edit its real synth parameters, then play with the piano, computer keys, or USB MIDI. Notes other than C3 are exploratory listening.</p></div>
      <div class="instrument-controls"><button class="btn primary" id="loadBest">LOAD CURRENT BEST</button><button class="btn" id="loadStarting">LOAD BASE</button><button class="btn" id="octaveDown">− OCTAVE</button><strong id="octaveLabel">C3–C5</strong><button class="btn" id="octaveUp">+ OCTAVE</button><label>Velocity <input id="playVelocity" type="range" min="1" max="127" value="100"><b id="velocityValue">100</b></label><button class="btn" id="connectMidi">CONNECT USB MIDI</button><span class="play-status" id="playStatus">Ready</span></div></div>
      <div class="editable-parameters" id="playableParameters"></div>
      <div class="keyboard-wrap"><div class="keyboard" id="piano" role="application" aria-label="Two octave playable piano keyboard"></div></div>
      <p class="hint">Computer keyboard (Ableton layout): white notes A S D F G H J K L; black notes W E T Y U O; Z/X changes octave and C/V changes velocity. These shortcuts keep working after you click a cockpit control. Each pitch is rendered by the real CLAP synth and cached; the first strike can lag. Notes are capped at six seconds, and browser release uses a short fade—this is playable audition, not a native real-time host.</p>
    </div>

    <div class="panel"><h2>Live synth parameters</h2><p class="hint">Actual real-synth state currently being evaluated; read-only.</p><div id="parameters"></div></div>
    <div class="panel"><h2>Search-space exploration</h2><p class="hint" id="spaceHint">2-D projection of selected parameters.</p><div class="axis-controls"><label>X <select id="xAxis"></select></label><label>Y <select id="yAxis"></select></label><button class="btn" id="playSelected" disabled>Play selected</button></div><canvas id="map" width="700" height="360" aria-label="Tested real-synth patches"></canvas></div>
    <div class="panel"><h2>CMA-ES search distribution</h2><p class="hint">Mean and one-standard-deviation search spread; not an animation of thought.</p><div id="distribution"></div></div>

    <div class="panel"><h2>Objective breakdown</h2><p class="hint">Current candidate; numeric similarity is not human preference.</p><div id="objective"></div><div class="info-grid"><div><span>Total</span><strong id="totalObjective">—</strong></div><div><span>Starting</span><strong id="startingObjective">—</strong></div><div><span>Elapsed</span><strong id="elapsed">0.0 s</strong></div><div><span>Remaining evals</span><strong id="remaining">160</strong></div></div></div>
    <div class="panel wide"><h2>Convergence</h2><p class="hint">Best-so-far and unsmoothed generation median loss.</p><canvas id="convergence" width="900" height="300" aria-label="Live objective convergence"></canvas></div>

    <div class="panel wide"><h2>Spectral comparison</h2><p class="hint">Target and current best use the same log-magnitude scale; updates only on a new best.</p><div class="spectra"><div><p class="canvas-title">TARGET</p><canvas id="targetSpectrum" width="600" height="260"></canvas></div><div><p class="canvas-title">CURRENT BEST</p><canvas id="bestSpectrum" width="600" height="260"></canvas></div></div></div>
    <div class="panel"><h2>Current candidate</h2><p class="hint">One atomic render and objective evaluation at a time.</p><div class="info-grid" id="candidateInfo"></div></div>

    <div class="panel full" id="presetPanel" hidden><h2>Closest factory presets</h2><p class="hint">Automated retrieval under the fixed C3 audition; numerical rank is not human preference.</p><div class="players" id="presetRanking"></div></div>
    <div class="panel full"><h2>Search history</h2><p class="hint">Complete record of every real-synth patch evaluated in this run. Click a row to inspect/play its WAV.</p><div class="table-wrap"><table><thead><tr id="historyHead"></tr></thead><tbody id="history"></tbody></table></div></div>
    <div class="panel full reveal" id="revealPanel"><div><h2>Hidden target state</h2><p class="hint">Available only after COMPLETE, STOPPED, or ERROR; parameter distance is not the objective.</p><div class="reveal-output" id="revealOutput">Hidden during the experiment.</div></div><button class="btn" id="reveal" disabled>REVEAL TARGET PARAMETERS</button></div>
  </section>
  <footer><span id="runInfo">Loading local run…</span><span id="renderClaim">Loopback only · Every point is a real CLAP render</span></footer>
</main>
<script>
  const $=(id)=>document.getElementById(id);let state=null,history=[],selected=null,lastBestHash=null,lastTargetId=null,targetSelecting=false;const historyAudio=new Audio();let playableVector=null,playableBase='starting',keyboardBase=48,audioContext=null,midiAccess=null;const voices=new Map(),pendingNotes=new Set(),releasedBeforeStart=new Set(),noteBuffers=new Map();
  const fmt=(v,d=4)=>v===null||v===undefined?'—':Number(v).toFixed(d);const pct=(v)=>`${Number(v||0).toFixed(1)}%`;
  function post(action){return fetch(`/api/run/${action}`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(async r=>{const body=await r.json();if(!r.ok)throw Error(body.error);return body;}).then(update).catch(e=>{$('runInfo').innerHTML=`<span class="error">${e.message}</span>`;});}
  async function selectTarget(targetId){if(targetSelecting||!state)return;targetSelecting=true;renderTargets();$('runInfo').textContent='Ranking the shared Surge preset cache for the selected target…';try{const response=await fetch('/api/targets/select',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({target_id:targetId})}),body=await response.json();if(!response.ok)throw Error(body.error);update(body);}catch(error){$('runInfo').innerHTML=`<span class="error">${error.message}</span>`;}finally{targetSelecting=false;renderTargets();}}
  function renderTargets(){const panel=$('targetBankPanel'),root=$('targetBank');if(!state?.targets){panel.hidden=true;return;}panel.hidden=false;if(root.children.length!==state.targets.length){root.replaceChildren();state.targets.forEach(target=>{const button=document.createElement('button');button.className='target-option';button.dataset.targetId=target.target_id;button.textContent=target.title;button.onclick=()=>selectTarget(target.target_id);root.append(button);});}const locked=targetSelecting||['SEARCHING','PAUSING','PAUSED'].includes(state.status);root.querySelectorAll('.target-option').forEach(button=>{button.classList.toggle('selected',button.dataset.targetId===state.selected_target_id);button.disabled=locked;});}
  $('start').onclick=()=>post('start');$('pause').onclick=()=>post('pause');$('resume').onclick=()=>post('resume');$('stop').onclick=()=>post('stop');$('reveal').onclick=()=>post('reveal');
  const noteNames=['C','C♯','D','D♯','E','F','F♯','G','G♯','A','A♯','B'];
  const computerNotes={'a':0,'w':1,'s':2,'e':3,'d':4,'f':5,'t':6,'g':7,'y':8,'h':9,'u':10,'j':11,'k':12,'o':13,'l':14};
  function noteLabel(note){return `${noteNames[note%12]}${Math.floor(note/12)-1}`;}
  function setPlayStatus(text,error=false){$('playStatus').textContent=text;$('playStatus').className=`play-status${error?' error':''}`;}
  function playableSourceVector(name){if(!state)return null;if(name==='best'&&state.best)return [...state.best.parameter_values_normalized];return state.parameters.map(p=>(p.current-p.min)/(p.max-p.min));}
  function loadPlayable(name){const vector=playableSourceVector(name);if(!vector)return;playableVector=vector;playableBase=name;noteBuffers.clear();updatePlayableControls();setPlayStatus(name==='best'&&state.best?'Best loaded':'Base loaded');}
  function updatePlayableControls(){if(!state||!playableVector)return;state.parameters.forEach((p,i)=>{const input=$(`pp${i}`);if(!input)return;input.value=playableVector[i];const real=p.min+playableVector[i]*(p.max-p.min);$(`ppv${i}`).textContent=`${real.toFixed(p.id===17?2:4)} · normalized ${playableVector[i].toFixed(3)}`;});}
  function buildPlayableParameters(){if($('playableParameters').children.length||!state)return;state.parameters.forEach((p,i)=>{const box=document.createElement('div');box.className='editable-parameter';box.innerHTML=`<label><span>${p.label}</span><span>${p.min}…${p.max}</span></label><input id="pp${i}" type="range" min="0" max="1" step="0.001"><small id="ppv${i}">—</small>`;$('playableParameters').append(box);box.querySelector('input').oninput=e=>{if(!playableVector)loadPlayable('best');playableVector[i]=Number(e.target.value);playableBase='custom';noteBuffers.clear();updatePlayableControls();setPlayStatus('Edited patch ready');};});loadPlayable(state.best?'best':'starting');}
  function buildPiano(){const piano=$('piano');piano.replaceChildren();const whiteWidth=100/15;let whiteIndex=0;for(let offset=0;offset<=24;offset++){const note=keyboardBase+offset,isBlack=[1,3,6,8,10].includes(note%12),key=document.createElement('button');key.className=`piano-key ${isBlack?'black':'white'}`;key.dataset.note=note;key.setAttribute('aria-label',noteLabel(note));key.textContent=noteLabel(note);if(isBlack){key.style.width=`${whiteWidth*.62}%`;key.style.left=`${(whiteIndex-.31)*whiteWidth}%`;}else{key.style.width=`${whiteWidth}%`;key.style.left=`${whiteIndex*whiteWidth}%`;whiteIndex++;}key.onpointerdown=e=>{e.preventDefault();key.setPointerCapture(e.pointerId);noteOn(note,Number($('playVelocity').value));};key.onpointerup=()=>noteOff(note);key.onpointercancel=()=>noteOff(note);key.onkeydown=e=>{if(!e.repeat&&(e.key==='Enter'||e.key===' ')){e.preventDefault();noteOn(note,Number($('playVelocity').value));}};key.onkeyup=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();noteOff(note);}};piano.append(key);}$('octaveLabel').textContent=`${noteLabel(keyboardBase)}–${noteLabel(keyboardBase+24)}`;}
  function markKey(note,active){document.querySelectorAll(`.piano-key[data-note="${note}"]`).forEach(key=>key.classList.toggle('active',active));}
  async function ensureAudio(){if(!audioContext)audioContext=new AudioContext();if(audioContext.state==='suspended')await audioContext.resume();return audioContext;}
  async function noteOn(note,velocity){if(note<21||note>108||voices.has(note)||pendingNotes.has(note)||!playableVector)return;pendingNotes.add(note);releasedBeforeStart.delete(note);markKey(note,true);try{const context=await ensureAudio(),values=playableVector.map(v=>Number(v).toFixed(6)).join(','),cacheKey=`${values}|${note}|${velocity}`;let buffer=noteBuffers.get(cacheKey);if(!buffer){setPlayStatus(`Rendering ${noteLabel(note)}…`);const url=`/api/playable/note?source=custom&note=${note}&velocity=${velocity}&values=${encodeURIComponent(values)}`,response=await fetch(url,{cache:'no-store'});if(!response.ok){const body=await response.json();throw Error(body.error||'note render failed');}buffer=await context.decodeAudioData(await response.arrayBuffer());noteBuffers.set(cacheKey,buffer);}const source=context.createBufferSource(),gain=context.createGain();source.buffer=buffer;source.connect(gain).connect(context.destination);gain.gain.value=1;source.onended=()=>{voices.delete(note);markKey(note,false);};voices.set(note,{source,gain});source.start();setPlayStatus(`${noteLabel(note)} · ${playableBase==='custom'?'edited patch':playableBase}`);if(releasedBeforeStart.has(note))setTimeout(()=>noteOff(note),220);}catch(error){markKey(note,false);setPlayStatus(error.message,true);}finally{pendingNotes.delete(note);releasedBeforeStart.delete(note);}}
  function noteOff(note){if(pendingNotes.has(note)){releasedBeforeStart.add(note);return;}const voice=voices.get(note);if(!voice){markKey(note,false);return;}const now=audioContext.currentTime;voice.gain.gain.cancelScheduledValues(now);voice.gain.gain.setValueAtTime(voice.gain.gain.value,now);voice.gain.gain.linearRampToValueAtTime(0,now+.08);voice.source.stop(now+.09);voices.delete(note);markKey(note,false);}
  function midiMessage(event){const [status,note,velocity]=event.data,command=status&0xf0;if(command===0x90&&velocity>0)noteOn(note,velocity);else if(command===0x80||(command===0x90&&velocity===0))noteOff(note);}
  async function connectMidi(){if(!navigator.requestMIDIAccess){setPlayStatus('Web MIDI unavailable in this browser',true);return;}try{midiAccess=await navigator.requestMIDIAccess();const bind=()=>{midiAccess.inputs.forEach(input=>input.onmidimessage=midiMessage);setPlayStatus(`${midiAccess.inputs.size} USB MIDI input${midiAccess.inputs.size===1?'':'s'} connected`);};midiAccess.onstatechange=bind;bind();}catch(error){setPlayStatus(`MIDI permission not granted`,true);}}
  function shiftOctave(delta){keyboardBase=Math.max(24,Math.min(72,keyboardBase+delta));buildPiano();setPlayStatus(`Keyboard ${noteLabel(keyboardBase)}–${noteLabel(keyboardBase+24)}`);}
  function adjustVelocity(delta){const input=$('playVelocity'),value=Math.max(1,Math.min(127,Number(input.value)+delta));input.value=value;$('velocityValue').textContent=value;setPlayStatus(`Velocity ${value}`);}
  $('loadBest').onclick=()=>loadPlayable('best');$('loadStarting').onclick=()=>loadPlayable('starting');$('octaveDown').onclick=()=>shiftOctave(-12);$('octaveUp').onclick=()=>shiftOctave(12);$('playVelocity').oninput=e=>$('velocityValue').textContent=e.target.value;$('connectMidi').onclick=connectMidi;addEventListener('keydown',e=>{if(e.repeat||e.metaKey||e.ctrlKey||e.altKey||e.target.matches('textarea,select,input:not([type="range"]),[contenteditable="true"]'))return;const key=e.key.toLowerCase();if(key==='z'){e.preventDefault();shiftOctave(-12);return;}if(key==='x'){e.preventDefault();shiftOctave(12);return;}if(key==='c'){e.preventDefault();adjustVelocity(-10);return;}if(key==='v'){e.preventDefault();adjustVelocity(10);return;}const offset=computerNotes[key];if(offset!==undefined){e.preventDefault();noteOn(keyboardBase+offset,Number($('playVelocity').value));}});addEventListener('keyup',e=>{const offset=computerNotes[e.key.toLowerCase()];if(offset!==undefined)noteOff(keyboardBase+offset);});buildPiano();
  function controls(s){$('start').disabled=s!=='IDLE';$('pause').disabled=s!=='SEARCHING';$('resume').disabled=s!=='PAUSED';$('stop').disabled=!['SEARCHING','PAUSING','PAUSED'].includes(s);$('reveal').disabled=!state.can_reveal||state.target_revealed;$('revealPanel').hidden=!!state.external_target_mode;renderTargets();}
  function buildStatic(){if($('parameters').children.length)return;state.parameters.forEach((p,i)=>{const el=document.createElement('div');el.className='parameter';el.innerHTML=`<div class="parameter-head"><strong>${p.label}</strong><span class="value" id="pv${i}">—</span></div><div class="track"><div class="fill" id="pf${i}"></div><div class="knob" id="pk${i}"></div></div><div class="hint" id="pn${i}">normalized —</div>`;$('parameters').append(el);const dist=document.createElement('div');dist.className='distribution';dist.innerHTML=`<div class="dist-head"><strong>${p.label}</strong><span id="dt${i}">μ — · σ —</span></div><div class="dist-track"><div class="dist-range" id="dr${i}"></div><div class="dist-mean" id="dm${i}"></div><div class="dist-current" id="dc${i}"></div></div>`;$('distribution').append(dist);const option=document.createElement('option');option.value=String(i);option.textContent=p.label;$('xAxis').append(option);$('yAxis').append(option.cloneNode(true));});$('xAxis').value=String(Math.min(2,state.parameters.length-1));$('yAxis').value=String(Math.min(3,state.parameters.length-1));$('xAxis').onchange=drawMap;$('yAxis').onchange=drawMap;['spectral','envelope','loudness'].forEach(name=>{const row=document.createElement('div');row.className='breakdown-row';row.innerHTML=`<span>${name[0].toUpperCase()+name.slice(1)}</span><div class="bar ${name}"><i id="ob-${name}"></i></div><strong id="ov-${name}">—</strong>`;$('objective').append(row);});const head=$('historyHead');['Eval','Gen',...state.parameters.map(p=>p.label),'Spectral','Envelope','Loudness','Total','Best'].forEach(label=>{const th=document.createElement('th');th.textContent=label;head.append(th);});$('spaceHint').textContent=`2-D projection of selected parameters; the search remains ${state.parameters.length}-dimensional.`;buildPlayableParameters();}
  function update(s){const targetId=s.selected_target_id||s.target?.target_id||null,targetChanged=lastTargetId!==null&&targetId!==lastTargetId;if(targetChanged){history=[];selected=null;lastBestHash=null;playableVector=null;noteBuffers.clear();$('history').replaceChildren();$('presetRanking').replaceChildren();$('playSelected').disabled=true;['targetAudio','startAudio','bestAudio'].forEach(id=>{const audio=$(id);audio.pause();audio.removeAttribute('src');audio.load();});}state=s;lastTargetId=targetId;buildStatic();if(targetChanged)loadPlayable('starting');$('status').textContent=s.status;$('generation').textContent=`${s.generation} / ${s.generations}`;$('evaluations').textContent=`${s.evaluations} / ${s.budget}`;$('currentLoss').textContent=fmt(s.current_loss);$('bestLoss').textContent=fmt(s.best_loss);$('improvement').textContent=pct(s.improvement_percent);$('startingObjective').textContent=fmt(s.starting_loss);$('elapsed').textContent=`${fmt(s.elapsed_seconds,1)} s`;$('remaining').textContent=s.evaluations_remaining;const synth=s.synth?`${s.synth.name} ${s.synth.version}`:'clap-saw-demo',fixed=s.fixed_filter?` · Filter Type ${s.fixed_filter.value} fixed`:'';$('runInfo').textContent=`${s.run_id} · ${synth} · ${s.optimizer.name} ${s.optimizer.version} · seed ${s.optimizer.seed}${fixed}`;$('renderClaim').textContent=`Loopback only · Every candidate is rendered by ${synth}`;$('targetTitle').textContent=s.target?.title||'fixed regression reference';$('targetSource').textContent=s.target_provenance||'Parameters hidden during the regression experiment.';$('targetProvenance').textContent=s.target_provenance||'';if(s.target_audio_url&&$('targetAudio').src!==new URL(s.target_audio_url,location.href).href)$('targetAudio').src=s.target_audio_url;if(s.base_label){$('basePlayerLabel').textContent=s.base_label;$('loadStarting').textContent='LOAD BASE';}if(s.starting_audio_url&&$('startAudio').src!==new URL(s.starting_audio_url,location.href).href)$('startAudio').src=s.starting_audio_url;if(s.preset_ranking)renderPresetRanking(s.preset_ranking);controls(s.status);
    const current=s.current;state.parameters.forEach((p,i)=>{const n=current?current.parameter_values_normalized[i]:state.optimizer.mean[i],real=current?current.parameter_values_real[String(p.id)]:p.current;$(`pv${i}`).textContent=real===undefined?'—':`${fmt(real,p.id===17?2:4)}`;$(`pn${i}`).textContent=`normalized ${fmt(n,3)} · range ${p.min}…${p.max}`;$(`pf${i}`).style.width=`${100*n}%`;$(`pk${i}`).style.left=`${100*n}%`;const mean=state.optimizer.mean[i],spread=state.optimizer.spread[i],lo=Math.max(0,mean-spread),hi=Math.min(1,mean+spread);$(`dr${i}`).style.left=`${100*lo}%`;$(`dr${i}`).style.width=`${100*(hi-lo)}%`;$(`dm${i}`).style.left=`${100*mean}%`;$(`dc${i}`).style.left=`${100*n}%`;$(`dt${i}`).textContent=`μ ${fmt(mean,3)} · σ ${fmt(spread,3)}`;});
    const o=current||{};['spectral','envelope','loudness'].forEach(name=>{const v=o[`${name}_loss`];$(`ov-${name}`).textContent=fmt(v);$(`ob-${name}`).style.width=`${100*Math.min(1,v||0)}%`;});$('totalObjective').textContent=fmt(o.total_loss);candidateInfo(current);drawConvergence();drawMap();if(s.best_audio_url&&$('bestAudio').src!==new URL(s.best_audio_url,location.href).href)$('bestAudio').src=s.best_audio_url;if(s.best&&s.best.wav_sha256!==lastBestHash){lastBestHash=s.best.wav_sha256;loadSpectra();}if(s.target_revealed)$('revealOutput').textContent=JSON.stringify(s.target_parameters,null,2);if(s.error)$('runInfo').innerHTML=`<span class="error">${s.error}</span>`;}
  function candidateInfo(c){const root=$('candidateInfo');root.replaceChildren();const rows=c?[['Evaluation',c.total_evaluations],['Generation',c.generation],['Candidate',c.candidate_index],['New best',c.is_new_best?'YES':'NO'],['WAV hash',c.wav_sha256.slice(0,12)+'…'],['Peak',fmt(c.peak_float,5)]]:[['Evaluation','—'],['State','Awaiting search']];rows.forEach(([a,b])=>{const d=document.createElement('div');d.innerHTML=`<span>${a}</span><strong>${b}</strong>`;root.append(d);});}
  function setupCanvas(canvas){const ratio=devicePixelRatio||1,w=canvas.clientWidth||canvas.width,h=canvas.clientHeight||canvas.height;canvas.width=Math.round(w*ratio);canvas.height=Math.round(h*ratio);const c=canvas.getContext('2d');c.setTransform(ratio,0,0,ratio,0,0);return {c,w,h};}
  function drawAxes(c,w,h,xLabel,yLabel){c.strokeStyle='#27405a';c.lineWidth=1;c.beginPath();c.moveTo(46,12);c.lineTo(46,h-34);c.lineTo(w-12,h-34);c.stroke();c.fillStyle='#8ea3b9';c.font='11px system-ui';c.fillText(yLabel,7,15);c.fillText(xLabel,w-95,h-10);}
  function drawConvergence(){if(!state)return;const {c,w,h}=setupCanvas($('convergence'));c.clearRect(0,0,w,h);drawAxes(c,w,h,'real synth evaluations','loss');const summaries=state.generation_summaries;if(!summaries.length)return;const max=Math.max(state.starting_loss,...summaries.map(x=>x.median_loss)),min=Math.min(...summaries.map(x=>x.best_loss));const x=v=>46+(w-62)*(v/state.budget),y=v=>12+(h-46)*(v-min)/(Math.max(max-min,1e-9));[['median_loss','#ff9a45'],['best_loss','#39a0ff']].forEach(([key,color])=>{c.strokeStyle=color;c.lineWidth=2;c.beginPath();summaries.forEach((d,i)=>{const px=x(d.evaluations),py=h-34-y(d[key])+12;i?c.lineTo(px,py):c.moveTo(px,py)});c.stroke();});c.fillStyle='#39a0ff';c.fillText('best',55,26);c.fillStyle='#ff9a45';c.fillText('median',95,26);}
  function drawMap(){if(!state)return;const canvas=$('map'),{c,w,h}=setupCanvas(canvas);c.clearRect(0,0,w,h);const xi=Number($('xAxis').value||0),yi=Number($('yAxis').value||Math.min(1,state.parameters.length-1));if(xi===yi){$('yAxis').value=String((yi+1)%state.parameters.length);return drawMap();}drawAxes(c,w,h,state.parameters[xi].label,state.parameters[yi].label);const sx=v=>46+v*(w-60),sy=v=>12+(1-v)*(h-46);history.forEach(row=>{const score=Math.max(0,1-row.total_loss/Math.max(state.starting_loss,.001)),r=row.is_new_best?6:3+score*2;c.fillStyle=row.is_new_best?'#38d58a':`rgba(57,160,255,${.25+.65*score})`;c.beginPath();c.arc(sx(row.parameter_values_normalized[xi]),sy(row.parameter_values_normalized[yi]),r,0,Math.PI*2);c.fill();});if(state.current){c.strokeStyle='#fff';c.lineWidth=2;c.beginPath();c.arc(sx(state.current.parameter_values_normalized[xi]),sy(state.current.parameter_values_normalized[yi]),8,0,Math.PI*2);c.stroke();}if(state.best){c.strokeStyle='#38d58a';c.lineWidth=3;c.beginPath();c.arc(sx(state.best.parameter_values_normalized[xi]),sy(state.best.parameter_values_normalized[yi]),10,0,Math.PI*2);c.stroke();}}
  function drawSpectrum(canvas,matrix,max){const {c,w,h}=setupCanvas(canvas);c.clearRect(0,0,w,h);const rows=matrix.length,cols=matrix[0].length,cellW=w/cols,cellH=h/rows;for(let y=0;y<rows;y++)for(let x=0;x<cols;x++){const t=Math.max(0,Math.min(1,matrix[y][x]/Math.max(max,.001)));const hue=250-210*t,light=8+56*t;c.fillStyle=`hsl(${hue} 90% ${light}%)`;c.fillRect(x*cellW,h-(y+1)*cellH,Math.ceil(cellW),Math.ceil(cellH));}}
  function loadSpectra(){fetch('/api/spectra').then(r=>r.json()).then(v=>{drawSpectrum($('targetSpectrum'),v.target.values,v.shared_max);drawSpectrum($('bestSpectrum'),v.best.values,v.shared_max);});}
  function renderPresetRanking(rows){const panel=$('presetPanel'),root=$('presetRanking');panel.hidden=false;if(root.children.length)return;rows.forEach(row=>{const box=document.createElement('div');box.className=`player${row.rank===1?' best':''}`;box.innerHTML=`<strong>#${row.rank} ${row.preset_name}</strong><small>${row.preset_category} · loss ${fmt(row.total_loss)}</small><audio controls preload="none" src="${row.audio_url}?run=${encodeURIComponent(state.run_id)}"></audio>`;root.append(box);});}
  function renderHistory(){const body=$('history');body.replaceChildren();history.slice(-180).reverse().forEach(row=>{const tr=document.createElement('tr');if(selected&&selected.total_evaluations===row.total_evaluations)tr.className='selected';const vals=[row.total_evaluations,row.generation,...row.parameter_values_normalized.map(v=>fmt(v,3)),fmt(row.spectral_loss),fmt(row.envelope_loss),fmt(row.loudness_loss),fmt(row.total_loss),row.is_new_best?'✓':''];vals.forEach((v,i)=>{const td=document.createElement('td');td.textContent=v;if(i===vals.length-1&&v)td.className='yes';tr.append(td)});tr.onclick=()=>{selected=row;$('playSelected').disabled=false;renderHistory();};body.append(tr);});}
  $('playSelected').onclick=()=>{if(!selected)return;historyAudio.src=selected.audio_url;historyAudio.currentTime=0;historyAudio.play();};
  function poll(){Promise.all([fetch('/api/status',{cache:'no-store'}).then(r=>r.json()),fetch('/api/history',{cache:'no-store'}).then(r=>r.json())]).then(([s,h])=>{history=h;update(s);renderHistory();}).catch(e=>{$('runInfo').innerHTML=`<span class="error">${e.message}</span>`;}).finally(()=>setTimeout(poll,400));}
  addEventListener('resize',()=>{drawConvergence();drawMap();if(lastBestHash)loadSpectra();});poll();
</script>
</body>
</html>
"""


def create_cockpit_server(
    run: Any, *, host: str = "127.0.0.1", port: int = 8765
) -> ThreadingHTTPServer:
    if host not in LOCAL_HOSTS:
        raise SearchCockpitError("search cockpit must bind to a loopback host")
    page = render_cockpit_page().encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def _send(
            self, body: bytes, content_type: str, status: int = HTTPStatus.OK
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'unsafe-inline'; "
                "script-src 'unsafe-inline'; media-src 'self'",
            )
            self.end_headers()
            self.wfile.write(body)

        def _json(self, value: Any, status: int = HTTPStatus.OK) -> None:
            self._send(
                (json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8"),
                "application/json; charset=utf-8",
                status,
            )

        def _request_json(self) -> dict[str, Any]:
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0]
            if content_type != "application/json":
                raise ValueError("Content-Type must be application/json")
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 4096:
                raise ValueError("request body is too large")
            try:
                value = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError as error:
                raise ValueError("request body must be valid JSON") from error
            if not isinstance(value, dict):
                raise ValueError("request body must be a JSON object")
            return value

        def do_GET(self) -> None:
            request = urlparse(self.path)
            path = request.path
            try:
                if path == "/":
                    self._send(page, "text/html; charset=utf-8")
                elif path == "/api/status":
                    self._json(run.public_status())
                elif path == "/api/history":
                    self._json(run.history_public())
                elif path == "/api/spectra":
                    self._json(run.spectra_public())
                elif path == "/api/targets" and hasattr(run, "targets_public"):
                    self._json(run.targets_public())
                elif path == "/api/playable/note":
                    query = parse_qs(request.query, strict_parsing=True)
                    source = query.get("source", [""])[0]
                    midi_key = int(query.get("note", [""])[0])
                    velocity = int(query.get("velocity", [""])[0])
                    values_text = query.get("values", [None])[0]
                    normalized = (
                        [float(value) for value in values_text.split(",")]
                        if values_text is not None
                        else None
                    )
                    playable = run.render_playable_note(
                        source=source,
                        midi_key=midi_key,
                        velocity=velocity,
                        normalized=normalized,
                    )
                    self._send(playable.read_bytes(), "audio/wav")
                elif path.startswith("/audio/"):
                    self._send(run.audio_path_for_url(path).read_bytes(), "audio/wav")
                else:
                    self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            except (RuntimeError, OSError, ValueError) as error:
                self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if path == "/api/targets/select":
                if not hasattr(run, "select_target"):
                    self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                    return
                try:
                    request = self._request_json()
                    target_id = request.get("target_id")
                    if not isinstance(target_id, str) or not target_id:
                        raise ValueError("target_id must be a non-empty string")
                    self._json(run.select_target(target_id))
                except ValueError as error:
                    self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                except RuntimeError as error:
                    self._json({"error": str(error)}, HTTPStatus.CONFLICT)
                return
            actions = {
                "/api/run/start": run.start_search,
                "/api/run/pause": run.pause_search,
                "/api/run/resume": run.resume_search,
                "/api/run/stop": run.stop_search,
                "/api/run/reveal": run.reveal_target,
            }
            action = actions.get(path)
            if action is None:
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            try:
                self._request_json()
                self._json(action())
            except ValueError as error:
                self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            except RuntimeError as error:
                self._json({"error": str(error)}, HTTPStatus.CONFLICT)

        def log_message(self, format: str, *args: object) -> None:
            print(f"search-cockpit: {format % args}")

    return ThreadingHTTPServer((host, port), Handler)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve live direct-search cockpit")
    parser.add_argument("--state", required=True)
    parser.add_argument("--plugin", required=True)
    parser.add_argument("--renderer", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--generations", type=int, default=20)
    parser.add_argument("--population", type=int, default=8)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    run = create_search_run(
        canonical_state_path=args.state,
        plugin_path=args.plugin,
        renderer_path=args.renderer,
        output_directory=args.output,
        generations=args.generations,
        population_size=args.population,
    )
    server = create_cockpit_server(run, host=args.host, port=args.port)
    print(f"live real-synth cockpit: http://{args.host}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        if run.status in {"SEARCHING", "PAUSING", "PAUSED"}:
            run.stop_search()
            run.wait(timeout=30)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
