"""Read-only local browser report for traceable DSP A/B measurements."""

from __future__ import annotations

import argparse
import hashlib
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

from .dsp import DSP_SCHEMA


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class DSPReportError(RuntimeError):
    """Raised when the local report cannot prove its inputs."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_report_data(
    measurements_path: str | Path, audio_root: str | Path
) -> tuple[dict[str, Any], dict[str, Path]]:
    """Load evidence and bind every report audio URL to its declared hash."""

    measurements_file = Path(measurements_path)
    evidence = json.loads(measurements_file.read_text(encoding="utf-8"))
    if evidence.get("schema") != DSP_SCHEMA:
        raise DSPReportError("DSP measurement schema is not supported")
    audio_directory = Path(audio_root)
    audio_paths: dict[str, Path] = {}
    report_measurements = []
    for item in evidence.get("measurements", []):
        ordinal = item["ordinal"]
        parameter_id = item["parameter_id"]
        probe_directory = audio_directory / f"{ordinal:02d}-parameter-{parameter_id}"
        enriched = dict(item)
        for side, filename in (("A", "A-baseline.wav"), ("B", "B-variant.wav")):
            path = probe_directory / filename
            if not path.is_file():
                raise DSPReportError(f"missing report audio: {path}")
            if _sha256(path) != item[side]["sha256"]:
                raise DSPReportError(f"report audio hash mismatch: {path}")
            url = f"/audio/{ordinal}/{side}.wav"
            audio_paths[url] = path
            enriched[side] = {**item[side], "url": url}
        report_measurements.append(enriched)
    if len(report_measurements) != evidence["summary"]["measurement_count"]:
        raise DSPReportError("measurement count does not match summary")
    return {**evidence, "measurements": report_measurements}, audio_paths


def render_report_page() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" href="data:,">
  <title>Synth Twin · DSP Comparison</title>
  <style>
    :root { color-scheme:light; --ink:#101828; --muted:#667085; --line:#d8dee9; --paper:#f4f6f8; --blue:#1455d9; --green:#087a55; --red:#b42318; --warm:#f79009; }
    * { box-sizing:border-box; }
    body { margin:0; background:var(--paper); color:var(--ink); font:15px/1.42 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
    main { width:min(1240px,calc(100% - 32px)); margin:34px auto 60px; }
    header { display:grid; grid-template-columns:1fr auto; gap:24px; align-items:end; margin-bottom:22px; }
    .eyebrow { margin:0 0 7px; color:var(--blue); font-size:12px; font-weight:850; letter-spacing:.11em; text-transform:uppercase; }
    h1 { margin:0; font-size:clamp(34px,5vw,62px); line-height:.98; letter-spacing:-.05em; }
    .sub { max-width:760px; margin:13px 0 0; color:var(--muted); font-size:17px; }
    .stats { display:flex; gap:10px; }
    .stat { min-width:98px; padding:13px 16px; background:#fff; border:1px solid var(--line); border-radius:14px; text-align:center; }
    .stat strong { display:block; font-size:25px; }
    .stat span { color:var(--muted); font-size:12px; font-weight:700; text-transform:uppercase; }
    .callout { margin:20px 0; padding:15px 18px; border-left:5px solid var(--warm); background:#fff8eb; border-radius:8px; }
    .table-wrap { overflow-x:auto; background:#fff; border:1px solid var(--line); border-radius:16px; box-shadow:0 15px 45px rgba(15,23,42,.06); }
    table { width:100%; border-collapse:collapse; min-width:1050px; }
    th,td { padding:13px 11px; border-bottom:1px solid #e8ecf2; text-align:right; white-space:nowrap; }
    th { color:var(--muted); font-size:11px; letter-spacing:.06em; text-transform:uppercase; background:#f9fafb; }
    th:first-child,td:first-child,th:nth-child(2),td:nth-child(2) { text-align:left; }
    tbody tr:last-child td { border-bottom:0; }
    tbody tr:hover { background:#f8faff; }
    .parameter { font-weight:750; }
    .values { color:var(--muted); font-size:12px; }
    .decision { display:inline-block; min-width:46px; padding:5px 8px; border-radius:999px; color:#fff; text-align:center; font-size:12px; font-weight:850; }
    .yes { background:var(--green); } .no { background:var(--red); }
    .plays { display:flex; justify-content:flex-end; gap:6px; }
    button { border:1px solid #b9cdf8; border-radius:8px; padding:7px 9px; background:#edf3ff; color:#0b4db8; font:inherit; font-weight:800; cursor:pointer; }
    button.playing { background:var(--blue); color:#fff; }
    .delta { font-variant-numeric:tabular-nums; }
    .positive { color:#9a3412; } .negative { color:#155eef; } .zero { color:var(--muted); }
    footer { display:flex; justify-content:space-between; gap:20px; margin:16px 3px; color:var(--muted); font-size:12px; }
    a { color:var(--blue); font-weight:750; }
    #status { min-height:22px; }
    @media (max-width:1200px) {
      main { width:min(680px,calc(100% - 20px)); margin-top:22px; }
      header { grid-template-columns:1fr; } .stats { justify-content:flex-start; } footer { flex-direction:column; }
      .table-wrap { overflow:visible; background:transparent; border:0; box-shadow:none; }
      table,tbody { display:block; min-width:0; }
      thead { display:none; }
      tbody { display:grid; gap:12px; }
      tbody tr { display:block; padding:10px 14px; background:#fff; border:1px solid var(--line); border-radius:14px; box-shadow:0 8px 25px rgba(15,23,42,.05); }
      tbody tr:hover { background:#fff; }
      td,td:first-child,td:nth-child(2) { display:grid; grid-template-columns:108px 1fr; gap:10px; padding:7px 0; border-bottom:1px solid #edf0f5; text-align:left; white-space:normal; }
      td:last-child { border-bottom:0; }
      td::before { content:attr(data-label); color:var(--muted); font-size:10px; font-weight:800; letter-spacing:.06em; text-transform:uppercase; }
      .plays { justify-content:flex-start; }
    }
  </style>
</head>
<body>
<main>
  <header>
    <div>
      <p class="eyebrow">Agentic Synth Twin · Milestone 5</p>
      <h1>Measured change vs. human hearing</h1>
      <p class="sub">The same ten completed A/B probes, now measured without altering or normalizing the audio. Values are B minus A.</p>
    </div>
    <div class="stats"><div class="stat"><strong id="count">–</strong><span>pairs</span></div><div class="stat"><strong id="yesCount">–</strong><span>human yes</span></div></div>
  </header>
  <div class="callout"><strong>Do not confuse magnitude with perception.</strong> A large descriptor delta is not automatically meaningful, and a human YES is not proof of which acoustic property caused it.</div>
  <div class="table-wrap">
    <table>
      <thead><tr><th>#</th><th>Real discovered parameter</th><th>Human</th><th>Listen</th><th>RMS dB</th><th>Peak dB</th><th>Centroid Hz</th><th>Rolloff Hz</th><th>Attack s</th><th>Release s</th></tr></thead>
      <tbody id="rows"></tbody>
    </table>
  </div>
  <footer><span id="status">Loading verified measurements…</span><a href="/api/measurements">DOWNLOAD MEASUREMENTS JSON</a></footer>
</main>
<audio id="player" preload="none"></audio>
<script>
  const player = document.getElementById('player');
  const format = (value, digits=3) => value === null ? 'censored' : `${value > 0 ? '+' : ''}${Number(value).toFixed(digits)}`;
  const tone = (value) => value === null || Math.abs(value) < 1e-9 ? 'zero' : value > 0 ? 'positive' : 'negative';
  const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (character) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[character]));
  function play(url, button) {
    document.querySelectorAll('button').forEach((item) => item.classList.remove('playing'));
    player.src=url; player.currentTime=0; button.classList.add('playing');
    player.play().catch((error) => { document.getElementById('status').textContent=`Playback blocked: ${error.message}`; });
  }
  fetch('/api/measurements').then((response) => response.json()).then((data) => {
    document.getElementById('count').textContent=data.summary.measurement_count;
    document.getElementById('yesCount').textContent=data.summary.human_yes_count;
    document.getElementById('rows').innerHTML=data.measurements.map((item) => {
      const d=item.deltas, yes=item.human_meaningful_difference;
      const cells=[
        [d.rms_dbfs_B_minus_A,3], [d.peak_dbfs_B_minus_A,3],
        [d.spectral_centroid_hz_B_minus_A,1], [d.spectral_rolloff_85_hz_B_minus_A,1],
        [d.attack_10_to_90_seconds_B_minus_A,3], [d.release_90_to_10_seconds_B_minus_A,3]
      ];
      const labels=['RMS dB','Peak dB','Centroid Hz','Rolloff Hz','Attack s','Release s'];
      const metricCells=cells.map(([value,digits],index) => `<td data-label="${labels[index]}" class="delta ${tone(value)}">${format(value,digits)}</td>`).join('');
      return `<tr><td data-label="Probe">${item.ordinal}</td><td data-label="Parameter"><div class="parameter">${escapeHtml(item.parameter_name)}</div><div class="values">${escapeHtml(item.baseline_value)} → ${escapeHtml(item.variant_value)}</div></td><td data-label="Human"><span class="decision ${yes?'yes':'no'}">${yes?'YES':'NO'}</span></td><td data-label="Listen"><div class="plays"><button onclick="play('${item.A.url}',this)">A</button><button onclick="play('${item.B.url}',this)">B</button></div></td>${metricCells}</tr>`;
    }).join('');
    document.getElementById('status').textContent=`Verified against calibration session ${data.source.session_id}.`;
  }).catch((error) => { document.getElementById('status').textContent=error.message; });
</script>
</body>
</html>
"""


def create_report_server(
    *,
    measurements_path: str | Path,
    audio_root: str | Path,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> ThreadingHTTPServer:
    if host not in LOCAL_HOSTS:
        raise DSPReportError("DSP report server must bind to a loopback host")
    evidence, audio_paths = load_report_data(measurements_path, audio_root)
    page = render_report_page().encode("utf-8")
    json_body = (json.dumps(evidence, ensure_ascii=False) + "\n").encode("utf-8")

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
            elif path == "/api/measurements":
                self._send(json_body, "application/json; charset=utf-8")
            elif path in audio_paths:
                self._send(audio_paths[path].read_bytes(), "audio/wav")
            else:
                self._send(b'{"error":"not found"}\n', "application/json", HTTPStatus.NOT_FOUND)

        def log_message(self, format: str, *args: object) -> None:
            print(f"dsp-report: {format % args}")

    return ThreadingHTTPServer((host, port), Handler)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve the local DSP comparison report")
    parser.add_argument("--measurements", required=True)
    parser.add_argument("--audio-root", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    server = create_report_server(
        measurements_path=args.measurements,
        audio_root=args.audio_root,
        host=args.host,
        port=args.port,
    )
    print(f"local DSP report: http://{args.host}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
