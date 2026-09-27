"""Thin local browser surface for one deterministic audition WAV."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Sequence


def render_page(manifest: Mapping[str, Any]) -> str:
    plugin = manifest["plugin"]
    audition = manifest["audition"]
    wav = manifest["wav"]
    limitation = manifest["known_limitations"]["velocity_note"]
    def escape(value: object) -> str:
        return html.escape(str(value), quote=True)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" href="data:,">
  <title>Deterministic C3 Audition</title>
  <style>
    :root {{ color-scheme: light; --ink:#111827; --muted:#5b6472; --line:#d8dee8; --blue:#0759d9; --paper:#f7f9fc; }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; min-height:100vh; display:grid; place-items:center; background:var(--paper); color:var(--ink); font:16px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
    main {{ width:min(720px,calc(100% - 32px)); background:white; border:1px solid var(--line); border-radius:18px; padding:32px; box-shadow:0 18px 50px rgba(15,23,42,.08); }}
    .eyebrow {{ margin:0 0 8px; color:var(--blue); font-size:13px; font-weight:750; letter-spacing:.09em; text-transform:uppercase; }}
    h1 {{ margin:0; font-size:clamp(30px,6vw,54px); letter-spacing:-.04em; }}
    .sub {{ margin:8px 0 28px; color:var(--muted); }}
    button {{ width:100%; border:0; border-radius:12px; padding:18px 24px; background:var(--blue); color:white; font:inherit; font-weight:800; letter-spacing:.04em; cursor:pointer; }}
    button:hover {{ background:#0649b2; }}
    button:focus-visible {{ outline:4px solid #a8c7ff; outline-offset:3px; }}
    #status {{ min-height:24px; margin:12px 0 24px; color:var(--muted); text-align:center; font-variant-numeric:tabular-nums; }}
    dl {{ display:grid; grid-template-columns:1fr 1fr; gap:0; margin:0; border-top:1px solid var(--line); }}
    .datum {{ padding:15px 0; border-bottom:1px solid var(--line); }}
    .datum:nth-child(odd) {{ padding-right:18px; }}
    dt {{ color:var(--muted); font-size:12px; font-weight:700; text-transform:uppercase; letter-spacing:.06em; }}
    dd {{ margin:4px 0 0; font-weight:650; }}
    .note {{ margin:22px 0 0; padding:14px 16px; border-left:4px solid #f0a000; background:#fff8e8; font-size:14px; }}
    .hash {{ overflow-wrap:anywhere; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px; }}
    @media (max-width:560px) {{ main {{ padding:24px; }} dl {{ grid-template-columns:1fr; }} .datum:nth-child(odd) {{ padding-right:0; }} }}
  </style>
</head>
<body>
<main>
  <p class="eyebrow">Agentic Synth Twin · Milestone 3</p>
  <h1>{escape(audition['note_name'])}</h1>
  <p class="sub">One fixed real-synth audition. No parameter controls yet.</p>
  <audio id="audition" preload="auto" src="/audition.wav"></audio>
  <button id="play" type="button">PLAY C3</button>
  <p id="status" aria-live="polite">Ready · {escape(wav['duration_seconds'])} seconds</p>
  <dl>
    <div class="datum"><dt>Plugin</dt><dd>{escape(plugin['name'])} {escape(plugin['version'])}</dd></div>
    <div class="datum"><dt>Pitch</dt><dd>MIDI {escape(audition['midi_key'])} · scientific C3</dd></div>
    <div class="datum"><dt>Velocity</dt><dd>{escape(audition['velocity'])} / 127</dd></div>
    <div class="datum"><dt>Note + tail</dt><dd>{escape(audition['note_duration_seconds'])} s + {escape(audition['release_tail_seconds'])} s</dd></div>
    <div class="datum"><dt>Format</dt><dd>{escape(wav['sample_rate'])} Hz · {escape(wav['bits_per_sample'])}-bit stereo</dd></div>
    <div class="datum"><dt>Determinism</dt><dd>2 byte-identical renders</dd></div>
    <div class="datum"><dt>WAV SHA-256</dt><dd class="hash">{escape(wav['sha256'])}</dd></div>
  </dl>
  <p class="note"><strong>Known limitation:</strong> {escape(limitation)}</p>
</main>
<script>
  const audio = document.getElementById('audition');
  const button = document.getElementById('play');
  const status = document.getElementById('status');
  button.addEventListener('click', async () => {{
    audio.currentTime = 0;
    try {{ await audio.play(); status.textContent = 'Playing C3'; }}
    catch (error) {{ status.textContent = 'Playback blocked: ' + error.message; }}
  }});
  audio.addEventListener('timeupdate', () => {{
    if (!audio.paused) status.textContent = `Playing · ${{audio.currentTime.toFixed(1)}} / ${{audio.duration.toFixed(1)}} s`;
  }});
  audio.addEventListener('ended', () => {{ status.textContent = 'Finished · ready to replay'; }});
</script>
</body>
</html>
"""


def create_server(
    *, wav_path: str | Path, manifest_path: str | Path, host: str, port: int
) -> ThreadingHTTPServer:
    wav = Path(wav_path).resolve()
    manifest_file = Path(manifest_path).resolve()
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    actual_wav_sha256 = hashlib.sha256(wav.read_bytes()).hexdigest()
    expected_wav_sha256 = manifest["wav"]["sha256"]
    if actual_wav_sha256 != expected_wav_sha256:
        raise ValueError(
            "preview WAV does not match manifest: "
            f"expected {expected_wav_sha256}, got {actual_wav_sha256}"
        )
    page = render_page(manifest).encode("utf-8")
    manifest_bytes = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: bytes, content_type: str, *, head_only: bool) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if not head_only:
                self.wfile.write(body)

        def _route(self, *, head_only: bool) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/":
                self._send(page, "text/html; charset=utf-8", head_only=head_only)
            elif path == "/audition.wav":
                self._send(wav.read_bytes(), "audio/wav", head_only=head_only)
            elif path == "/manifest.json":
                self._send(
                    manifest_bytes,
                    "application/json; charset=utf-8",
                    head_only=head_only,
                )
            elif path == "/health":
                self._send(b'{"status":"ok"}\n', "application/json", head_only=head_only)
            else:
                self.send_error(HTTPStatus.NOT_FOUND)

        def do_GET(self) -> None:
            self._route(head_only=False)

        def do_HEAD(self) -> None:
            self._route(head_only=True)

        def log_message(self, format: str, *args: object) -> None:
            print(f"preview: {format % args}")

    return ThreadingHTTPServer((host, port), Handler)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve deterministic audition preview")
    parser.add_argument("--wav", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    server = create_server(
        wav_path=args.wav,
        manifest_path=args.manifest,
        host=args.host,
        port=args.port,
    )
    print(f"audition preview: http://{args.host}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
