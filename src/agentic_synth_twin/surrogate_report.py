"""Read-only local training cockpit for Milestone 7 surrogate evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse

from .surrogate import (
    FREEZE_SCHEMA,
    PREDICTIONS_SCHEMA,
    RESULTS_SCHEMA,
    STATUS_SCHEMA,
)


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class SurrogateReportError(RuntimeError):
    """Raised when dashboard inputs violate the frozen evidence contract."""


def _sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_report_data(
    *,
    results_path: str | Path,
    predictions_path: str | Path,
    status_path: str | Path,
    dataset_path: str | Path,
) -> dict[str, Any]:
    results_file = Path(results_path)
    root = results_file.parent
    results = json.loads(results_file.read_text(encoding="utf-8"))
    if results.get("schema") != RESULTS_SCHEMA:
        raise SurrogateReportError("surrogate result schema is not supported")
    predictions_file = Path(predictions_path)
    predictions = json.loads(predictions_file.read_text(encoding="utf-8"))
    if predictions.get("schema") != PREDICTIONS_SCHEMA:
        raise SurrogateReportError("surrogate prediction schema is not supported")
    if _sha256(predictions_file) != results["predictions"]["sha256"]:
        raise SurrogateReportError("prediction evidence hash mismatch")
    status = json.loads(Path(status_path).read_text(encoding="utf-8"))
    if status.get("schema") != STATUS_SCHEMA:
        raise SurrogateReportError("surrogate status schema is not supported")
    if _sha256(dataset_path) != results["dataset"]["sha256"]:
        raise SurrogateReportError("frozen dataset hash mismatch")
    for name, artifact in results["artifacts"].items():
        path = root / artifact["filename"]
        if not path.is_file() or _sha256(path) != artifact["sha256"]:
            raise SurrogateReportError(f"{name} artifact hash mismatch")
    freeze = json.loads((root / results["artifacts"]["freeze"]["filename"]).read_text())
    if freeze.get("schema") != FREEZE_SCHEMA or freeze.get("test_status") != "locked":
        raise SurrogateReportError("pre-test freeze record is invalid")
    if (
        results["protocol"]["freeze_sha256_before_test"]
        != results["artifacts"]["freeze"]["sha256"]
    ):
        raise SurrogateReportError("test results are not bound to the pre-test freeze")
    return {"results": results, "predictions": predictions, "status": status}


def render_report_page() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" href="data:,">
  <title>Synth Twin · Surrogate Training Cockpit</title>
  <style>
    :root{color-scheme:light;--ink:#101828;--muted:#667085;--paper:#eef1f5;--card:#fff;--line:#d7dde7;--blue:#155eef;--green:#087a55;--orange:#e04f16;--purple:#6938ef;--red:#d92d20}
    *{box-sizing:border-box} body{margin:0;background:var(--paper);color:var(--ink);font:14px/1.42 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
    main{width:min(1240px,calc(100% - 28px));margin:28px auto 56px} header{display:grid;grid-template-columns:1fr auto;gap:22px;align-items:end}
    .eyebrow{margin:0 0 7px;color:var(--blue);font-size:11px;font-weight:850;letter-spacing:.12em;text-transform:uppercase} h1{margin:0;font-size:clamp(34px,5vw,62px);line-height:.98;letter-spacing:-.055em}
    .sub{max-width:790px;margin:12px 0 0;color:var(--muted);font-size:17px}.badge{padding:11px 14px;border:1px solid #a7d7c5;border-radius:999px;background:#ecfdf3;color:var(--green);font-weight:850}
    .boundary{margin:20px 0;padding:14px 17px;background:#fff8eb;border-left:5px solid #f79009;border-radius:8px}.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:15px}
    .card{grid-column:span 12;background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px;box-shadow:0 10px 30px rgba(15,23,42,.045)}.half{grid-column:span 6}.third{grid-column:span 4}
    h2{margin:0;font-size:19px;letter-spacing:-.02em}h3{margin:18px 0 8px;font-size:14px}.hint{margin:5px 0 14px;color:var(--muted);font-size:12px}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.stat{padding:13px;border:1px solid var(--line);border-radius:12px;background:#fafbfc}.stat strong{display:block;font-size:26px}.stat span{color:var(--muted);font-size:10px;font-weight:800;text-transform:uppercase}
    .split{display:grid;grid-template-columns:repeat(3,1fr);gap:9px}.split div{padding:12px;border-radius:10px;background:#f8fafc;border:1px solid var(--line)}.split b,.state b{display:block;font-size:20px}.locked{color:var(--orange)}.revealed{color:var(--green)}
    .states{display:grid;gap:8px}.state{display:flex;align-items:center;justify-content:space-between;padding:10px 12px;border:1px solid var(--line);border-radius:10px}.state b{font-size:13px}.pill{padding:4px 8px;border-radius:999px;background:#ecfdf3;color:var(--green);font-size:10px;font-weight:850;text-transform:uppercase}
    .controls{display:flex;flex-wrap:wrap;gap:7px;margin:11px 0}.controls button,.controls select{border:1px solid var(--line);border-radius:9px;padding:8px 10px;background:#fff;color:var(--ink);font:inherit;font-weight:750;cursor:pointer}.controls button.active{border-color:var(--blue);background:#edf3ff;color:#0b4db8}
    svg{width:100%;height:auto;display:block;background:#fbfcfe;border:1px solid #e7ebf1;border-radius:11px}table{width:100%;border-collapse:collapse;min-width:600px}th,td{padding:9px;border-bottom:1px solid #e9edf3;text-align:right;font-variant-numeric:tabular-nums}th{color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.06em}th:first-child,td:first-child{text-align:left}.scroll{overflow-x:auto}.selected{color:var(--purple);font-weight:850}.pass{color:var(--green);font-weight:850}.fail{color:var(--red);font-weight:850}.barrow{display:grid;grid-template-columns:145px 1fr 75px;gap:9px;align-items:center;margin:9px 0}.bar{height:11px;background:#edf0f5;border-radius:999px;overflow:hidden}.bar i{display:block;height:100%;background:var(--purple)}
    .gate{display:grid;grid-template-columns:1fr auto;gap:12px;padding:10px 0;border-bottom:1px solid #edf0f4}.gate:last-child{border-bottom:0}.timeline{display:flex;align-items:center;flex-wrap:wrap;gap:7px}.timeline span{padding:7px 9px;border:1px solid var(--line);border-radius:8px;background:#fafbfc;font-size:11px;font-weight:750}.arrow{color:var(--muted)}footer{margin:16px 2px;color:var(--muted);font-size:12px}
    @media(max-width:900px){header{grid-template-columns:1fr}.half,.third{grid-column:span 12}.stats{grid-template-columns:repeat(2,1fr)}main{width:min(700px,calc(100% - 20px))}}
  </style>
</head>
<body><main>
  <header><div><p class="eyebrow">Agentic Synth Twin · Milestone 7</p><h1>Surrogate training cockpit</h1><p class="sub">A transparent view of what was fitted, what remained locked, where predictions fail, and whether the bounded learnability claim passed.</p></div><div id="overall" class="badge">LOADING EVIDENCE</div></header>
  <div class="boundary"><b>Scientific boundary:</b> this models exact Filter Type, Cutoff, and Attack states → measured DSP descriptors inside one sampled region. It does not understand timbre, infer perceptual quality, optimize sounds, or implement LOCK.</div>
  <section class="grid">
    <article class="card half"><h2>Dataset and split</h2><p class="hint">Filter Type is categorical; quiet examples remain present.</p><div class="stats"><div class="stat"><strong id="examples">—</strong><span>examples</span></div><div class="stat"><strong>3</strong><span>active inputs</span></div><div class="stat"><strong>6</strong><span>DSP targets</span></div><div class="stat"><strong id="release">—</strong><span>release signal</span></div></div><h3>Leakage-safe manifest</h3><div class="split"><div><b id="trainCount">—</b>TRAIN</div><div><b id="validationCount">—</b>VALIDATION</div><div><b id="testCount">—</b>TEST <span id="testState" class="locked">LOCKED</span></div></div></article>
    <article class="card half"><h2>Training status</h2><p class="hint">Fast estimators expose truthful state transitions, not fabricated epoch progress.</p><div id="states" class="states"></div><h3>Final-test protocol</h3><div class="timeline"><span>CONFIG + SPLIT SAVED</span><b class="arrow">→</b><span>TEST LOCKED</span><b class="arrow">→</b><span>MODEL FROZEN</span><b class="arrow">→</b><span>TEST REVEALED ONCE</span></div></article>
    <article class="card"><h2>Model comparison</h2><p class="hint">Candidate decisions use validation only. Choose a target to inspect MAE, normalized MAE, R², and rank correlation.</p><div class="controls"><select id="targetSelect"></select></div><div class="scroll"><table><thead><tr><th>Model</th><th>Train MAE</th><th>Validation MAE</th><th>Validation normalized MAE</th><th>Validation R²</th><th>Validation Spearman</th></tr></thead><tbody id="comparison"></tbody></table></div></article>
    <article class="card half"><h2>Learning curves</h2><p class="hint">Mean normalized error across predeclared selection targets; lower is better.</p><div id="curveButtons" class="controls"></div><svg id="curve" viewBox="0 0 600 330" role="img" aria-label="Training and validation learning curves"></svg></article>
    <article class="card half"><h2>Feature importance</h2><p class="hint">Validation permutation importance measures predictive reliance, not causality.</p><div id="importance"></div></article>
    <article class="card half"><h2>Predicted vs actual</h2><p class="hint">Final untouched test set; the diagonal is ideal agreement.</p><svg id="scatter" viewBox="0 0 600 380" role="img" aria-label="Predicted versus actual test values"></svg></article>
    <article class="card half"><h2>Residual view</h2><p class="hint">Prediction minus actual; the horizontal line marks zero error.</p><svg id="residual" viewBox="0 0 600 380" role="img" aria-label="Test residuals"></svg></article>
    <article class="card"><h2>Regional error</h2><p class="hint">Find failures by actual Filter Type category, Cutoff region, and Attack region. R² can become extreme inside small, low-variance groups; read it beside MAE and rank.</p><div class="controls"><select id="regionSelect"><option value="filter_type">Filter Type</option><option value="cutoff_region">Cutoff region</option><option value="attack_region">Attack region</option></select></div><div class="scroll"><table><thead><tr><th>Region</th><th>Samples</th><th>MAE</th><th>Normalized MAE</th><th>R²</th><th>Spearman</th></tr></thead><tbody id="regions"></tbody></table></div></article>
    <article class="card third"><h2>Predeclared gates</h2><p class="hint">Thresholds were hashed before test reveal.</p><div id="gates"></div></article>
    <article class="card third"><h2>Structured holdout</h2><p class="hint">Separate bounded diagnostic, not primary test evidence.</p><div id="holdout"></div></article>
    <article class="card third"><h2>Real-synth rerender</h2><p class="hint">Held-out states rerendered exactly; never added to training.</p><div id="rerender"></div></article>
  </section>
  <footer id="footer">Loading verified local evidence…</footer>
</main>
<script>
let report,results,records,target='rms_dbfs',curveModel;
const colors={mean:'#98a2b3',linear:'#155eef',random_forest:'#6938ef'},models=['mean','linear','random_forest'];
const fmt=(v,d=3)=>v===null||v===undefined?'—':Number(v).toLocaleString(undefined,{maximumFractionDigits:d});
const label=(key)=>results.dataset.targets.find((item)=>item.key===key).label;
function svg(name,attrs={}){const element=document.createElementNS('http://www.w3.org/2000/svg',name);Object.entries(attrs).forEach(([k,v])=>element.setAttribute(k,String(v)));return element}
function textAt(parent,value,x,y,anchor='start',size=11,fill='#667085'){const element=svg('text',{x,y,'text-anchor':anchor,'font-size':size,fill});element.textContent=value;parent.append(element);return element}
function axis(parent,x1,y1,x2,y2){parent.append(svg('line',{x1,y1,x2,y2,stroke:'#cfd6e2','stroke-width':1}))}
function renderHeader(){const counts=results.split.counts;document.getElementById('examples').textContent=results.dataset.sample_count;document.getElementById('trainCount').textContent=counts.train;document.getElementById('validationCount').textContent=counts.validation;document.getElementById('testCount').textContent=counts.test;const state=document.getElementById('testState');state.textContent='REVEALED AFTER FREEZE';state.className='revealed';document.getElementById('release').textContent=results.dataset.release_characterization.low_information_by_predeclared_rule?'LOW':'ACTIVE';const passed=results.final_test.thresholds.overall_passed;const overall=document.getElementById('overall');overall.textContent=passed?'FEASIBILITY PASS':'FEASIBILITY FAIL';overall.style.borderColor=passed?'#a7d7c5':'#f3b5b0';overall.style.background=passed?'#ecfdf3':'#fff1f0';overall.style.color=passed?'#087a55':'#d92d20'}
function renderStates(){const root=document.getElementById('states');models.forEach((name)=>{const row=document.createElement('div');row.className='state';const title=document.createElement('b');title.textContent=name==='mean'?'Mean baseline':name==='linear'?'Regularized linear':'Random Forest';const state=document.createElement('span');state.className='pill';state.textContent=results.candidates[name].state;row.append(title,state);root.append(row)})}
function renderTargets(){const select=document.getElementById('targetSelect');results.dataset.targets.forEach((item)=>{const option=document.createElement('option');option.value=item.key;option.textContent=item.label;select.append(option)});select.addEventListener('change',()=>{target=select.value;renderComparison();renderScatter();renderResidual();renderRegions()})}
function renderComparison(){const body=document.getElementById('comparison');body.textContent='';models.forEach((name)=>{const candidate=results.candidates[name],tr=candidate.train.targets[target],va=candidate.validation.targets[target],row=document.createElement('tr');if(name===results.selection.selected_model)row.className='selected';[name+(name===results.selection.selected_model?' · SELECTED':''),fmt(tr.mae),fmt(va.mae),fmt(va.normalized_mae,4),fmt(va.r2),fmt(va.spearman)].forEach((value)=>{const cell=document.createElement('td');cell.textContent=value;row.append(cell)});body.append(row)})}
function renderCurveButtons(){const root=document.getElementById('curveButtons');curveModel=results.selection.selected_model;models.forEach((name)=>{const button=document.createElement('button');button.textContent=name;button.dataset.model=name;button.addEventListener('click',()=>{curveModel=name;[...root.children].forEach((x)=>x.classList.toggle('active',x.dataset.model===name));renderCurve()});root.append(button)});[...root.children].forEach((x)=>x.classList.toggle('active',x.dataset.model===curveModel));renderCurve()}
function renderCurve(){const root=document.getElementById('curve');root.textContent='';const data=results.learning_curves[curveModel],left=58,right=570,top=24,bottom=286,maxX=Math.max(...data.map((d)=>d.training_samples)),maxY=Math.max(...data.flatMap((d)=>[d.training_mean_normalized_mae,d.validation_mean_normalized_mae]))*1.1;axis(root,left,bottom,right,bottom);axis(root,left,top,left,bottom);const xp=(v)=>left+(v/maxX)*(right-left),yp=(v)=>bottom-(v/maxY)*(bottom-top);for(let i=0;i<=4;i++){const value=maxY*i/4,y=yp(value);root.append(svg('line',{x1:left,y1:y,x2:right,y2:y,stroke:'#edf0f5'}));textAt(root,fmt(value,3),left-8,y+4,'end')};[['training_mean_normalized_mae','#155eef','TRAIN'],['validation_mean_normalized_mae','#e04f16','VALIDATION']].forEach(([key,color,name],legendIndex)=>{const points=data.map((d)=>`${xp(d.training_samples)},${yp(d[key])}`).join(' ');root.append(svg('polyline',{points,fill:'none',stroke:color,'stroke-width':3}));data.forEach((d)=>root.append(svg('circle',{cx:xp(d.training_samples),cy:yp(d[key]),r:4,fill:color})));textAt(root,name,470,18+legendIndex*16,'start',10,color)});data.forEach((d)=>textAt(root,d.training_samples,xp(d.training_samples),bottom+19,'middle'));textAt(root,'training samples',(left+right)/2,322,'middle',11,'#344054')}
function predictionRows(){return records.filter((record)=>record.split==='test')}
function extents(values){let min=Math.min(...values),max=Math.max(...values);if(min===max){min-=1;max+=1}const pad=(max-min)*.08;return [min-pad,max+pad]}
function renderScatter(){const root=document.getElementById('scatter');root.textContent='';const rows=predictionRows(),actual=rows.map((r)=>r.targets[target].actual),predicted=rows.map((r)=>r.targets[target].predicted),[min,max]=extents([...actual,...predicted]),left=62,right=574,top=20,bottom=332,x=(v)=>left+(v-min)/(max-min)*(right-left),y=(v)=>bottom-(v-min)/(max-min)*(bottom-top);axis(root,left,bottom,right,bottom);axis(root,left,top,left,bottom);root.append(svg('line',{x1:x(min),y1:y(min),x2:x(max),y2:y(max),stroke:'#98a2b3','stroke-dasharray':'6 5'}));rows.forEach((row)=>root.append(svg('circle',{cx:x(row.targets[target].actual),cy:y(row.targets[target].predicted),r:4,fill:colors[results.selection.selected_model],opacity:.72})));textAt(root,'actual',(left+right)/2,370,'middle',12,'#344054');const yl=textAt(root,'predicted',17,(top+bottom)/2,'middle',12,'#344054');yl.setAttribute('transform',`rotate(-90 17 ${(top+bottom)/2})`);textAt(root,fmt(min),left,bottom+18,'middle');textAt(root,fmt(max),right,bottom+18,'middle');textAt(root,fmt(max),left-8,top+4,'end');textAt(root,fmt(min),left-8,bottom+4,'end')}
function renderResidual(){const root=document.getElementById('residual');root.textContent='';const rows=predictionRows(),actual=rows.map((r)=>r.targets[target].actual),residual=rows.map((r)=>r.targets[target].residual),[xmin,xmax]=extents(actual),[ymin,ymax]=extents([...residual,0]),left=62,right=574,top=20,bottom=332,x=(v)=>left+(v-xmin)/(xmax-xmin)*(right-left),y=(v)=>bottom-(v-ymin)/(ymax-ymin)*(bottom-top);axis(root,left,bottom,right,bottom);axis(root,left,top,left,bottom);root.append(svg('line',{x1:left,y1:y(0),x2:right,y2:y(0),stroke:'#d92d20','stroke-dasharray':'6 5'}));rows.forEach((row)=>root.append(svg('circle',{cx:x(row.targets[target].actual),cy:y(row.targets[target].residual),r:4,fill:'#e04f16',opacity:.72})));textAt(root,'actual',(left+right)/2,370,'middle',12,'#344054');const yl=textAt(root,'prediction − actual',17,(top+bottom)/2,'middle',12,'#344054');yl.setAttribute('transform',`rotate(-90 17 ${(top+bottom)/2})`);textAt(root,fmt(ymax),left-8,top+4,'end');textAt(root,fmt(ymin),left-8,bottom+4,'end')}
function renderImportance(){const root=document.getElementById('importance'),data=results.permutation_importance_on_validation,max=Math.max(...data.map((d)=>Math.max(0,d.mean_validation_normalized_mae_increase)),.0001);root.textContent='';data.forEach((item)=>{const row=document.createElement('div');row.className='barrow';const name=document.createElement('b');name.textContent=item.input;const bar=document.createElement('div');bar.className='bar';const fill=document.createElement('i');fill.style.width=`${Math.max(0,item.mean_validation_normalized_mae_increase)/max*100}%`;bar.append(fill);const value=document.createElement('span');value.textContent=fmt(item.mean_validation_normalized_mae_increase,4);row.append(name,bar,value);root.append(row)})}
function renderRegions(){const dimension=document.getElementById('regionSelect').value,body=document.getElementById('regions');body.textContent='';results.regional_error_on_final_test[dimension].forEach((entry)=>{const metric=entry.targets[target]||{},row=document.createElement('tr');[entry.region,entry.sample_count,fmt(metric.mae),fmt(metric.normalized_mae,4),fmt(metric.r2),fmt(metric.spearman)].forEach((value)=>{const cell=document.createElement('td');cell.textContent=value;row.append(cell)});body.append(row)})}
function renderGates(){const root=document.getElementById('gates'),gates=results.final_test.thresholds.gates;Object.entries(gates).forEach(([name,value])=>{const row=document.createElement('div');row.className='gate';const title=document.createElement('span');title.textContent=name.replaceAll('_',' ');const state=document.createElement('b');state.className=value.passed?'pass':'fail';state.textContent=value.passed?'PASS':'FAIL';row.append(title,state);root.append(row)})}
function renderHoldout(){const item=results.structured_holdout_diagnostic,root=document.getElementById('holdout');const lines=[`${item.holdout_sample_count} held-out examples`,`${item.training_sample_count} training examples`,`Centroid MAE ${fmt(item.metrics.targets.spectral_centroid_hz.mae,1)} Hz`,`RMS MAE ${fmt(item.metrics.targets.rms_dbfs.mae,2)} dBFS`];lines.forEach((line)=>{const p=document.createElement('p');p.textContent=line;root.append(p)})}
function renderRerender(){const item=results.real_synth_rerender,root=document.getElementById('rerender');const state=document.createElement('p');state.className=item.all_exact?'pass':'fail';state.textContent=item.all_exact?'ALL EXACT':'INTEGRITY FAILURE';const count=document.createElement('p');count.textContent=`${item.sample_count} untouched-test states rerendered twice`;root.append(state,count)}
function load(){fetch('/api/report').then((response)=>{if(!response.ok)throw new Error(`HTTP ${response.status}`);return response.json()}).then((value)=>{report=value;results=value.results;records=value.predictions.records;renderHeader();renderStates();renderTargets();renderComparison();renderCurveButtons();renderImportance();renderScatter();renderResidual();renderRegions();document.getElementById('regionSelect').addEventListener('change',renderRegions);renderGates();renderHoldout();renderRerender();document.getElementById('footer').textContent=`Selected ${results.selection.selected_model} · dataset ${results.dataset.sha256.slice(0,12)}… · model ${results.artifacts.model.sha256.slice(0,12)}… · ${results.claim}`}).catch((error)=>{document.getElementById('footer').textContent=`Dashboard error: ${error.message}`})}
load();
</script></body></html>"""


def create_report_server(
    *,
    results_path: str | Path,
    predictions_path: str | Path,
    status_path: str | Path,
    dataset_path: str | Path,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> ThreadingHTTPServer:
    if host not in LOCAL_HOSTS:
        raise SurrogateReportError("surrogate report server must bind to a loopback host")
    report = load_report_data(
        results_path=results_path,
        predictions_path=predictions_path,
        status_path=status_path,
        dataset_path=dataset_path,
    )
    page = render_report_page().encode("utf-8")
    payload = (json.dumps(report, ensure_ascii=False) + "\n").encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'",
            )
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/":
                self._send(page, "text/html; charset=utf-8")
            elif path == "/api/report":
                self._send(payload, "application/json; charset=utf-8")
            else:
                self._send(b'{"error":"not found"}\n', "application/json", HTTPStatus.NOT_FOUND)

        def log_message(self, format: str, *args: object) -> None:
            print(f"surrogate-report: {format % args}")

    return ThreadingHTTPServer((host, port), Handler)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve the local surrogate training cockpit")
    parser.add_argument("--results", required=True)
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    server = create_report_server(
        results_path=args.results,
        predictions_path=args.predictions,
        status_path=args.status,
        dataset_path=args.dataset,
        host=args.host,
        port=args.port,
    )
    print(f"local surrogate cockpit: http://{args.host}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
