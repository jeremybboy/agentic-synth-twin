"""Loopback-only blinded audition report for Milestone 8B."""

from __future__ import annotations

import argparse
import json
import threading
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import unquote, urlparse

from .audition import _write_json_atomic
from .multitarget import SUITE_SCHEMA, public_suite


class MultiTargetReportError(RuntimeError):
    """Raised when the report or persisted judgments violate the contract."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MultiTargetSession:
    def __init__(self, output_directory: str | Path):
        self.output_directory = Path(output_directory).resolve()
        suite_path = self.output_directory / "suite.json"
        if not suite_path.is_file():
            raise MultiTargetReportError(f"suite result not found: {suite_path}")
        self.suite = json.loads(suite_path.read_text(encoding="utf-8"))
        if self.suite.get("schema") != SUITE_SCHEMA:
            raise MultiTargetReportError("unsupported multi-target suite schema")
        self.judgment_path = self.output_directory / "judgments.json"
        self._lock = threading.RLock()
        self.revealed = False
        self.judgments: dict[str, dict[str, Any]] = {}
        self.heard: dict[str, dict[str, str]] = {}
        if self.judgment_path.is_file():
            saved = json.loads(self.judgment_path.read_text(encoding="utf-8"))
            self.judgments = dict(saved.get("judgments", {}))
            self.heard = dict(saved.get("heard", {}))

    def status(self) -> dict[str, Any]:
        with self._lock:
            payload = public_suite(self.suite, reveal=self.revealed)
            payload["judgments"] = {
                target_id: {"choice": value["choice"], "recorded_at": value["recorded_at"]}
                for target_id, value in self.judgments.items()
            }
            payload["completed_judgments"] = len(self.judgments)
            payload["heard"] = {
                target_id: sorted(roles) for target_id, roles in self.heard.items()
            }
            payload["can_reveal"] = len(self.judgments) == len(payload["targets"])
            if not self.revealed:
                payload["aggregate"] = {
                    "target_count": payload["aggregate"]["target_count"],
                    "engine_integrity_pass": payload["aggregate"][
                        "engine_integrity_pass"
                    ],
                    "human_audition": payload["aggregate"]["human_audition"],
                }
            return payload

    def record_judgment(self, target_id: str, choice: str) -> dict[str, Any]:
        normalized_choice = choice.upper()
        if normalized_choice not in {"A", "B", "SAME"}:
            raise MultiTargetReportError("choice must be A, B, or SAME")
        private = next(
            (item for item in self.suite["targets_private"] if item["id"] == target_id),
            None,
        )
        if private is None:
            raise MultiTargetReportError("unknown target id")
        if not {"target", "candidate_a", "candidate_b"}.issubset(
            self.heard.get(target_id, {})
        ):
            raise MultiTargetReportError(
                "listen to Target, Candidate A, and Candidate B to completion first"
            )
        mapping = private["blind_assignment_private"]
        method = None
        if normalized_choice in {"A", "B"}:
            method = mapping[f"candidate_{normalized_choice.lower()}"]
        with self._lock:
            self.judgments[target_id] = {
                "target_id": target_id,
                "choice": normalized_choice,
                "selected_method_private": method,
                "recorded_at": _now(),
            }
            self._persist()
            return self.status()

    def record_listen(self, target_id: str, role: str) -> dict[str, Any]:
        allowed = {"target", "start", "candidate_a", "candidate_b"}
        if role not in allowed:
            raise MultiTargetReportError("unknown audio role")
        if not any(item["id"] == target_id for item in self.suite["targets_private"]):
            raise MultiTargetReportError("unknown target id")
        with self._lock:
            self.heard.setdefault(target_id, {})[role] = _now()
            self._persist()
            return self.status()

    def reveal(self) -> dict[str, Any]:
        with self._lock:
            if len(self.judgments) != len(self.suite["targets_private"]):
                raise MultiTargetReportError(
                    "complete all blinded judgments before revealing methods"
                )
            self.revealed = True
            return self.status()

    def _persist(self) -> None:
        _write_json_atomic(
            {
                "schema": SUITE_SCHEMA,
                "judgments": self.judgments,
                "heard": self.heard,
                "method_mapping_withheld_by_api_until_reveal": True,
            },
            self.judgment_path,
        )

    def audio_path(self, request_path: str) -> Path:
        relative = unquote(request_path.removeprefix("/audio/"))
        candidate = (self.output_directory / relative).resolve()
        try:
            candidate.relative_to(self.output_directory)
        except ValueError as error:
            raise MultiTargetReportError("audio path escapes suite directory") from error
        if candidate.suffix.lower() != ".wav" or not candidate.is_file():
            raise MultiTargetReportError("audio artifact does not exist")
        return candidate


def render_multitarget_page() -> str:
    return """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Agentic Synth Twin · Multi-target Validation</title><style>
:root{color-scheme:dark;--bg:#07101d;--panel:#0d1928;--line:#20334b;--text:#eef5ff;--muted:#8ca2ba;--blue:#3f8cff;--green:#36d17c;--orange:#ff9d42}*{box-sizing:border-box}body{margin:0;background:linear-gradient(140deg,#07101d,#0c1726);color:var(--text);font:14px Inter,system-ui,sans-serif}.shell{max-width:1450px;margin:auto;padding:18px}.top,.panel{background:rgba(13,25,40,.96);border:1px solid var(--line);border-radius:12px}.top{display:flex;gap:24px;align-items:center;padding:16px 20px}.top h1{margin:0;font-size:22px}.top small,.muted{color:var(--muted)}.summary{margin-left:auto;display:flex;gap:24px}.metric b{display:block;font-size:20px}.layout{display:grid;grid-template-columns:260px 1fr;gap:12px;margin-top:12px}.panel{padding:15px}.targets{display:grid;gap:8px}.target{width:100%;padding:11px;text-align:left;border:1px solid var(--line);border-radius:8px;background:#101f31;color:var(--text);cursor:pointer}.target.active{border-color:var(--blue);box-shadow:0 0 0 1px var(--blue)}.target.done::after{content:'✓';float:right;color:var(--green)}.players{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.player{padding:14px;background:#091525;border:1px solid var(--line);border-radius:9px}.player strong{display:block;margin-bottom:9px}.player audio{width:100%}.choice{display:flex;gap:10px;margin:16px 0}.choice button,#reveal{padding:11px 18px;border:1px solid var(--line);border-radius:8px;background:#16283d;color:var(--text);font-weight:700;cursor:pointer}.choice button:hover{border-color:var(--blue)}#reveal{background:#143a2a;border-color:#246044}.choice button:disabled,#reveal:disabled{opacity:.35;cursor:not-allowed}.result{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.card{padding:12px;background:#091525;border:1px solid var(--line);border-radius:8px}.card b{font-size:20px}.good{color:var(--green)}.bad{color:var(--orange)}pre{white-space:pre-wrap;color:#bad0e7}.note{margin-top:12px;padding:12px;border-left:3px solid var(--orange);background:#131b26;color:#c9d5e2}@media(max-width:900px){.layout{grid-template-columns:1fr}.players,.result{grid-template-columns:1fr 1fr}.summary{display:none}}</style></head><body><main class="shell"><header class="top"><div><h1>Agentic Synth Twin</h1><small>MILESTONE 8B · BLINDED MULTI-TARGET VALIDATION</small></div><div class="summary"><div class="metric"><small>ENGINE</small><b id="engine">—</b></div><div class="metric"><small>JUDGMENTS</small><b id="progress">0 / 8</b></div><div class="metric"><small>METHODS</small><b id="methodState">HIDDEN</b></div></div></header><div class="layout"><aside class="panel"><h2>Targets</h2><p class="muted">Eight fixed sounds generated by the real synth. Names describe parameter regions, not instrument identities.</p><div id="targets" class="targets"></div></aside><section class="panel"><h2 id="title">Select a target</h2><p class="muted">Listen to Target, then compare Candidate A and Candidate B. Starting Patch is context only.</p><div class="players"><div class="player"><strong>TARGET</strong><audio id="targetAudio" controls></audio></div><div class="player"><strong>STARTING PATCH</strong><audio id="startAudio" controls></audio></div><div class="player"><strong>CANDIDATE A</strong><audio id="aAudio" controls></audio></div><div class="player"><strong>CANDIDATE B</strong><audio id="bAudio" controls></audio></div></div><div class="choice"><button id="pickA">A IS CLOSER</button><button id="pickB">B IS CLOSER</button><button id="pickSame">NO CLEAR DIFFERENCE</button></div><div id="judgment" class="muted"></div><hr style="border:0;border-top:1px solid var(--line);margin:18px 0"><button id="reveal" disabled>REVEAL METHODS AND RESULTS</button><div id="results"></div><div class="note">This test measures performance inside the current saw synthesizer's reachable space. It does not claim guitar, Rhodes, or arbitrary sample matching.</div></section></div></main><script>
const $=id=>document.getElementById(id);let state=null,selected=0;function selectedTarget(){return state.targets[selected]}function render(){const t=selectedTarget();$('engine').textContent=state.aggregate.engine_integrity_pass?'PASS':'FAIL';$('progress').textContent=`${state.completed_judgments} / ${state.targets.length}`;$('methodState').textContent=state.revealed?'REVEALED':'HIDDEN';$('targets').innerHTML=state.targets.map((x,i)=>`<button class="target ${i===selected?'active':''} ${state.judgments[x.id]?'done':''}" data-index="${i}">${i+1}. ${x.label}</button>`).join('');document.querySelectorAll('.target').forEach(b=>b.onclick=()=>{selected=Number(b.dataset.index);render()});$('title').textContent=`${selected+1}. ${t.label}`;$('targetAudio').src=t.audio.target;$('startAudio').src=t.audio.start;$('aAudio').src=t.audio.candidate_a;$('bAudio').src=t.audio.candidate_b;const saved=state.judgments[t.id],heard=new Set(state.heard[t.id]||[]),ready=['target','candidate_a','candidate_b'].every(x=>heard.has(x));$('pickA').disabled=!ready;$('pickB').disabled=!ready;$('pickSame').disabled=!ready;$('judgment').textContent=saved?`Recorded: ${saved.choice==='SAME'?'no clear difference':`Candidate ${saved.choice}`}`:ready?'Required audio completed; record your judgment.':`Listen fully before judging: ${['target','candidate_a','candidate_b'].filter(x=>!heard.has(x)).join(', ')}`;$('reveal').disabled=!state.can_reveal||state.revealed;if(state.revealed){const a=state.aggregate;$('results').innerHTML=`<h2>Numeric results</h2><div class="result"><div class="card">CMA wins<br><b class="${a.adaptive_gate_pass?'good':'bad'}">${a.cma_win_count} / ${a.target_count}</b></div><div class="card">Median CMA loss<br><b>${a.median_cma_best_loss.toFixed(4)}</b></div><div class="card">Median random loss<br><b>${a.median_random_best_loss.toFixed(4)}</b></div></div><h3>${a.adaptive_gate_pass?'ADAPTIVE GATE PASS':'ADAPTIVE GATE FAIL'}</h3><pre>${JSON.stringify({assignment:t.blind_assignment,target:t.target_parameters,cma:t.cma,random:t.random},null,2)}</pre>`}else{$('results').innerHTML=''}}
async function post(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const value=await r.json();if(!r.ok)throw Error(value.error);state=value;render()}function hear(role){post('/api/listen',{target_id:selectedTarget().id,role}).catch(e=>alert(e.message))}function choose(choice){post('/api/judgment',{target_id:selectedTarget().id,choice}).then(()=>{if(selected<state.targets.length-1)selected++;render()}).catch(e=>alert(e.message))}$('targetAudio').onended=()=>hear('target');$('startAudio').onended=()=>hear('start');$('aAudio').onended=()=>hear('candidate_a');$('bAudio').onended=()=>hear('candidate_b');$('pickA').onclick=()=>choose('A');$('pickB').onclick=()=>choose('B');$('pickSame').onclick=()=>choose('SAME');$('reveal').onclick=()=>post('/api/reveal',{}).catch(e=>alert(e.message));fetch('/api/status',{cache:'no-store'}).then(r=>r.json()).then(s=>{state=s;render()});
</script></body></html>"""


def create_multitarget_server(
    session: MultiTargetSession, *, host: str = "127.0.0.1", port: int = 8766
) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise MultiTargetReportError("multi-target report must bind to loopback")

    class Handler(BaseHTTPRequestHandler):
        server_version = "AgenticSynthTwinMultiTarget/1"

        def _json(self, payload: Mapping[str, Any], status: int = 200) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(length) or b"{}")

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            try:
                if path == "/":
                    body = render_multitarget_page().encode()
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif path == "/api/status":
                    self._json(session.status())
                elif path.startswith("/audio/"):
                    audio = session.audio_path(path)
                    body = audio.read_bytes()
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "audio/wav")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            except (MultiTargetReportError, ValueError, json.JSONDecodeError) as error:
                self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            try:
                body = self._body()
                if path == "/api/judgment":
                    self._json(
                        session.record_judgment(
                            str(body.get("target_id", "")), str(body.get("choice", ""))
                        )
                    )
                elif path == "/api/listen":
                    self._json(
                        session.record_listen(
                            str(body.get("target_id", "")), str(body.get("role", ""))
                        )
                    )
                elif path == "/api/reveal":
                    self._json(session.reveal())
                else:
                    self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            except (MultiTargetReportError, ValueError, json.JSONDecodeError) as error:
                self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

        def log_message(self, format: str, *args: object) -> None:
            print(f"multi-target-report: {format % args}")

    return ThreadingHTTPServer((host, port), Handler)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve blinded multi-target report")
    parser.add_argument("--output", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    session = MultiTargetSession(args.output)
    server = create_multitarget_server(session, host=args.host, port=args.port)
    print(f"multi-target validation report: http://{args.host}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
