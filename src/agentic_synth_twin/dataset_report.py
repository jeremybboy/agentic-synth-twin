"""Read-only local explorer for the verified synthetic dataset."""

from __future__ import annotations

import argparse
import hashlib
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse

from .dataset import DATASET_SCHEMA


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class DatasetReportError(RuntimeError):
    """Raised when report inputs do not match the committed dataset evidence."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_report_data(
    dataset_path: str | Path, audio_root: str | Path
) -> tuple[dict[str, Any], dict[str, Path]]:
    dataset = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    if dataset.get("schema") != DATASET_SCHEMA:
        raise DatasetReportError("synthetic dataset schema is not supported")
    audio_directory = Path(audio_root)
    audio_paths: dict[str, Path] = {}
    samples = []
    for sample in dataset.get("samples", []):
        path = audio_directory / sample["wav"]["filename"]
        if not path.is_file():
            raise DatasetReportError(f"missing dataset audio: {path}")
        if _sha256(path) != sample["wav"]["sha256"]:
            raise DatasetReportError(f"dataset audio hash mismatch: {path}")
        url = f"/audio/{sample['sample_id']}.wav"
        audio_paths[url] = path
        samples.append({**sample, "audio_url": url})
    if len(samples) != dataset["summary"]["sample_count"]:
        raise DatasetReportError("sample count does not match dataset summary")
    return {**dataset, "samples": samples}, audio_paths


def render_report_page() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" href="data:,">
  <title>Synth Twin · Dataset Explorer</title>
  <style>
    :root { color-scheme:light; --ink:#111827; --muted:#667085; --line:#d8dee8; --paper:#f3f5f8; --blue:#155eef; --green:#087a55; --orange:#e04f16; }
    * { box-sizing:border-box; }
    body { margin:0; background:var(--paper); color:var(--ink); font:15px/1.42 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
    main { width:min(1180px,calc(100% - 28px)); margin:30px auto 54px; }
    header { display:grid; grid-template-columns:1fr auto; gap:24px; align-items:end; }
    .eyebrow { margin:0 0 7px; color:var(--blue); font-size:12px; font-weight:850; letter-spacing:.11em; text-transform:uppercase; }
    h1 { margin:0; font-size:clamp(34px,5vw,60px); line-height:1; letter-spacing:-.05em; }
    .sub { max-width:760px; margin:12px 0 0; color:var(--muted); font-size:17px; }
    .stats { display:flex; gap:9px; }
    .stat { min-width:92px; padding:13px 14px; background:#fff; border:1px solid var(--line); border-radius:13px; text-align:center; }
    .stat strong { display:block; font-size:25px; } .stat span { color:var(--muted); font-size:10px; font-weight:800; text-transform:uppercase; }
    .boundary { margin:20px 0; padding:14px 17px; background:#fff8eb; border-left:5px solid #f79009; border-radius:8px; }
    .grid { display:grid; grid-template-columns:minmax(0,1.55fr) minmax(300px,.75fr); gap:16px; align-items:start; }
    .card { background:#fff; border:1px solid var(--line); border-radius:16px; padding:18px; box-shadow:0 12px 35px rgba(15,23,42,.05); }
    .card h2 { margin:0; font-size:20px; letter-spacing:-.02em; } .hint { margin:5px 0 14px; color:var(--muted); font-size:13px; }
    svg { width:100%; height:auto; display:block; background:#fbfcfe; border:1px solid #e7ebf1; border-radius:12px; }
    circle { cursor:pointer; transition:opacity .15s,stroke-width .15s; } circle:hover,circle:focus { stroke:#111827; stroke-width:3; outline:none; }
    .legend { display:flex; flex-wrap:wrap; gap:7px; margin-top:12px; }
    .legend button,.play { border:1px solid var(--line); border-radius:9px; padding:8px 10px; background:#fff; color:var(--ink); font:inherit; font-weight:750; cursor:pointer; }
    .legend button.active { border-color:var(--blue); box-shadow:0 0 0 2px #cbdafa; }
    .dot { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:6px; }
    .sample-id { margin:12px 0 2px; color:var(--blue); font-size:12px; font-weight:850; letter-spacing:.08em; text-transform:uppercase; }
    .selected-title { margin:0 0 14px; font-size:25px; letter-spacing:-.03em; }
    dl { display:grid; grid-template-columns:1fr auto; gap:0; margin:0; }
    dt,dd { margin:0; padding:8px 0; border-bottom:1px solid #edf0f4; } dt { color:var(--muted); } dd { font-variant-numeric:tabular-nums; font-weight:750; text-align:right; }
    .play { width:100%; margin-top:14px; padding:13px; border-color:#b9cdf8; background:#edf3ff; color:#0b4db8; }
    .play.playing { background:var(--blue); color:#fff; }
    .summary { margin-top:16px; overflow-x:auto; }
    table { width:100%; border-collapse:collapse; min-width:650px; } th,td { padding:10px; border-bottom:1px solid #e8ecf2; text-align:right; }
    th { color:var(--muted); font-size:10px; letter-spacing:.06em; text-transform:uppercase; } th:first-child,td:first-child { text-align:left; }
    footer { display:flex; justify-content:space-between; gap:18px; margin:15px 3px; color:var(--muted); font-size:12px; } a { color:var(--blue); font-weight:750; }
    @media(max-width:850px) { header,.grid { grid-template-columns:1fr; } .stats { justify-content:flex-start; } main { width:min(680px,calc(100% - 20px)); } footer { flex-direction:column; } }
  </style>
</head>
<body>
<main>
  <header><div><p class="eyebrow">Agentic Synth Twin · Milestone 6</p><h1>Verified dataset explorer</h1><p class="sub">One canonical baseline plus 255 deterministic synth states. Click any point to inspect its exact parameters, measured output, and local WAV.</p></div><div class="stats"><div class="stat"><strong id="sampleCount">–</strong><span>samples</span></div><div class="stat"><strong>3</strong><span>parameters</span></div><div class="stat"><strong id="clipCount">–</strong><span>clipped</span></div></div></header>
  <div class="boundary"><strong>This is coverage, not intelligence.</strong> No model has been trained, no parameter combination has been judged by a human, and numeric Filter Type values remain opaque plugin categories.</div>
  <div class="grid">
    <section class="card"><h2>Parameter-space coverage</h2><p class="hint">X = Cutoff in Keys · Y = Amplitude Attack · color = Filter Type. Filter or click a point.</p><svg id="plot" viewBox="0 0 720 430" role="img" aria-label="Dataset parameter coverage scatter plot"></svg><div class="legend" id="legend"></div></section>
    <aside class="card"><h2>Selected example</h2><p class="sample-id" id="sampleId">Loading…</p><h3 class="selected-title" id="selectedTitle">Verified real-synth render</h3><dl id="details"></dl><button class="play" id="play" type="button">▶ PLAY SELECTED WAV</button></aside>
  </div>
  <section class="card summary"><h2>Descriptive output summary by Filter Type</h2><p class="hint">Medians describe this sampled dataset only; they are not causal effects or model predictions.</p><table><thead><tr><th>Filter Type</th><th>Rows</th><th>RMS dBFS</th><th>Peak dBFS</th><th>Centroid Hz</th><th>Rolloff Hz</th></tr></thead><tbody id="summaryRows"></tbody></table></section>
  <footer><span id="status">Loading hash-verified dataset…</span><a href="/api/dataset">DOWNLOAD DATASET JSON</a></footer>
</main>
<audio id="audio" preload="none"></audio>
<script>
  const ids={filter:'14255',cutoff:'17',attack:'2874'};
  const colors=['#155eef','#087a55','#e04f16','#9333ea','#d97706','#0891b2'];
  const svg=document.getElementById('plot'),audio=document.getElementById('audio'),playButton=document.getElementById('play');
  let dataset=null,activeFilter='all',selected=null;
  const median=(values)=>{const sorted=[...values].sort((a,b)=>a-b),middle=Math.floor(sorted.length/2);return sorted.length%2?sorted[middle]:(sorted[middle-1]+sorted[middle])/2;};
  const number=(value,digits=3)=>value===null?'censored':Number(value).toFixed(digits);
  const svgElement=(name,attributes={})=>{const element=document.createElementNS('http://www.w3.org/2000/svg',name);Object.entries(attributes).forEach(([key,value])=>element.setAttribute(key,value));return element;};
  function drawPlot(){
    svg.replaceChildren();
    const left=62,right=24,top=24,bottom=48,width=720-left-right,height=430-top-bottom;
    for(let tick=0;tick<=4;tick++){const y=top+tick/4*height,value=(1-tick/4).toFixed(2);svg.append(svgElement('line',{x1:left,x2:left+width,y1:y,y2:y,stroke:'#e5e9f0'}));const label=svgElement('text',{x:left-10,y:y+4,'text-anchor':'end',fill:'#667085','font-size':'12'});label.textContent=value;svg.append(label);}
    for(const value of [1,32.5,64,95.5,127]){const x=left+(value-1)/126*width;svg.append(svgElement('line',{x1:x,x2:x,y1:top,y2:top+height,stroke:'#edf0f4'}));const label=svgElement('text',{x,y:top+height+23,'text-anchor':'middle',fill:'#667085','font-size':'12'});label.textContent=Number(value).toFixed(value%1?1:0);svg.append(label);}
    const xTitle=svgElement('text',{x:left+width/2,y:420,'text-anchor':'middle',fill:'#344054','font-size':'13','font-weight':'700'});xTitle.textContent='Cutoff in Keys';svg.append(xTitle);
    const yTitle=svgElement('text',{x:15,y:top+height/2,transform:`rotate(-90 15 ${top+height/2})`,'text-anchor':'middle',fill:'#344054','font-size':'13','font-weight':'700'});yTitle.textContent='Amplitude Attack';svg.append(yTitle);
    dataset.samples.forEach((sample)=>{const filter=sample.parameter_values[ids.filter],cutoff=sample.parameter_values[ids.cutoff],attack=sample.parameter_values[ids.attack];const circle=svgElement('circle',{cx:left+(cutoff-1)/126*width,cy:top+(1-attack)*height,r:sample.is_canonical_baseline?7:4.5,fill:colors[filter],opacity:activeFilter==='all'||Number(activeFilter)===filter?.88:.08,stroke:sample.is_canonical_baseline?'#111827':'#fff','stroke-width':sample.is_canonical_baseline?2.5:1,tabindex:'0','aria-label':`${sample.sample_id}, Filter Type ${filter}, Cutoff ${number(cutoff,2)}, Attack ${number(attack,3)}`});circle.addEventListener('click',()=>selectSample(sample));circle.addEventListener('keydown',(event)=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();selectSample(sample);}});svg.append(circle);});
  }
  function selectSample(sample){selected=sample;document.getElementById('sampleId').textContent=sample.sample_id+(sample.is_canonical_baseline?' · canonical baseline':'');document.getElementById('selectedTitle').textContent=`Filter Type ${sample.parameter_values[ids.filter]}`;const f=sample.wav.features;const rows=[['Cutoff in Keys',number(sample.parameter_values[ids.cutoff],4)],['Amplitude Attack',number(sample.parameter_values[ids.attack],5)],['RMS',`${number(f.rms_dbfs)} dBFS`],['Peak',`${number(f.peak_dbfs)} dBFS`],['Centroid',`${number(f.spectral_centroid_hz,1)} Hz`],['85% rolloff',`${number(f.spectral_rolloff_85_hz,1)} Hz`],['Measured attack',`${number(f.attack_10_to_90_seconds)} s`],['Measured release',f.release_censored_by_tail?'censored':`${number(f.release_90_to_10_seconds)} s`]];const details=document.getElementById('details');details.replaceChildren();rows.forEach(([label,value])=>{const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=value;details.append(dt,dd);});playButton.classList.remove('playing');}
  function buildLegend(){const legend=document.getElementById('legend');const choices=[['all','All'],...[0,1,2,3,4,5].map((value)=>[String(value),`Type ${value}`])];choices.forEach(([value,label])=>{const button=document.createElement('button');button.type='button';button.dataset.value=value;button.innerHTML=value==='all'?label:`<span class="dot" style="background:${colors[Number(value)]}"></span>${label}`;button.addEventListener('click',()=>{activeFilter=value;legend.querySelectorAll('button').forEach((item)=>item.classList.toggle('active',item===button));drawPlot();});if(value==='all')button.classList.add('active');legend.append(button);});}
  function buildSummary(){const body=document.getElementById('summaryRows');for(let filter=0;filter<6;filter++){const rows=dataset.samples.filter((sample)=>sample.parameter_values[ids.filter]===filter),get=(key)=>rows.map((row)=>row.wav.features[key]);const tr=document.createElement('tr');[filter,rows.length,number(median(get('rms_dbfs'))),number(median(get('peak_dbfs'))),number(median(get('spectral_centroid_hz')),1),number(median(get('spectral_rolloff_85_hz')),1)].forEach((value)=>{const td=document.createElement('td');td.textContent=value;tr.append(td);});body.append(tr);}}
  playButton.addEventListener('click',()=>{if(!selected)return;audio.src=selected.audio_url;audio.currentTime=0;audio.play().then(()=>playButton.classList.add('playing')).catch((error)=>{document.getElementById('status').textContent=`Playback blocked: ${error.message}`;});});
  fetch('/api/dataset').then((response)=>response.json()).then((value)=>{dataset=value;document.getElementById('sampleCount').textContent=dataset.summary.sample_count;document.getElementById('clipCount').textContent=dataset.summary.clipped_sample_count;buildLegend();drawPlot();buildSummary();selectSample(dataset.samples[0]);document.getElementById('status').textContent=`Seed ${dataset.sampling.seed} · ${dataset.summary.unique_wav_sha256_count} unique WAV hashes · all audio verified locally.`;}).catch((error)=>{document.getElementById('status').textContent=error.message;});
</script>
</body>
</html>
"""


def create_report_server(
    *,
    dataset_path: str | Path,
    audio_root: str | Path,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> ThreadingHTTPServer:
    if host not in LOCAL_HOSTS:
        raise DatasetReportError("dataset report server must bind to a loopback host")
    dataset, audio_paths = load_report_data(dataset_path, audio_root)
    page = render_report_page().encode("utf-8")
    json_body = (json.dumps(dataset, ensure_ascii=False) + "\n").encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
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

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/":
                self._send(page, "text/html; charset=utf-8")
            elif path == "/api/dataset":
                self._send(json_body, "application/json; charset=utf-8")
            elif path in audio_paths:
                self._send(audio_paths[path].read_bytes(), "audio/wav")
            else:
                self._send(b'{"error":"not found"}\n', "application/json", HTTPStatus.NOT_FOUND)

        def log_message(self, format: str, *args: object) -> None:
            print(f"dataset-report: {format % args}")

    return ThreadingHTTPServer((host, port), Handler)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve the local dataset explorer")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--audio-root", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    server = create_report_server(
        dataset_path=args.dataset,
        audio_root=args.audio_root,
        host=args.host,
        port=args.port,
    )
    print(f"local dataset report: http://{args.host}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
