"""Local browser calibration with blinded A/B playback and SQLite persistence."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import secrets
import sqlite3
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

from .ab_probe import generate_probe
from .synth_state import load_canonical_state


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
CLAP_PARAM_IS_STEPPED = 1 << 0


class CalibrationError(RuntimeError):
    """Raised when calibration state or interaction violates the contract."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def default_probe_plan(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Choose a strong range movement without inventing parameter semantics."""

    plan = []
    for parameter in state["parameters"]:
        if not parameter["automatable"] or parameter["max"] == parameter["min"]:
            continue
        if parameter["current"] != parameter["max"]:
            variant = parameter["max"]
        elif parameter["flags"] & CLAP_PARAM_IS_STEPPED:
            variant = parameter["min"]
        else:
            variant = (parameter["min"] + parameter["max"]) / 2
        plan.append({"parameter_id": parameter["id"], "parameter_value": variant})
    if not plan:
        raise CalibrationError("the real inventory contains no probeable parameters")
    return plan


def prepare_probe_manifests(
    *,
    canonical_state_path: str | Path,
    accepted_manifest_path: str | Path,
    accepted_wav_path: str | Path,
    plugin_path: str | Path,
    renderer_path: str | Path,
    output_directory: str | Path,
) -> list[Path]:
    """Render one deterministic range-movement probe per real mutable parameter."""

    canonical_path = Path(canonical_state_path)
    state = load_canonical_state(canonical_path)
    output = Path(output_directory)
    manifests = []
    for index, item in enumerate(default_probe_plan(state), start=1):
        parameter_id = item["parameter_id"]
        probe_directory = output / f"{index:02d}-parameter-{parameter_id}"
        generate_probe(
            canonical_state_path=canonical_path,
            accepted_manifest_path=accepted_manifest_path,
            accepted_wav_path=accepted_wav_path,
            plugin_path=plugin_path,
            renderer_path=renderer_path,
            output_directory=probe_directory,
            parameter_id=parameter_id,
            parameter_value=item["parameter_value"],
        )
        manifests.append(probe_directory / "ab-probe.json")
    return manifests


class CalibrationStore:
    """Small transactional store for one or more local listening sessions."""

    def __init__(self, database_path: str | Path):
        self.path = Path(database_path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._create_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _create_schema(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS calibration_sessions (
                    id TEXT PRIMARY KEY,
                    created_at_utc TEXT NOT NULL,
                    completed_at_utc TEXT,
                    seed INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('active', 'completed')),
                    probe_count INTEGER NOT NULL CHECK (probe_count > 0)
                );
                CREATE TABLE IF NOT EXISTS calibration_probes (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES calibration_sessions(id),
                    ordinal INTEGER NOT NULL,
                    manifest_path TEXT NOT NULL,
                    parameter_id INTEGER NOT NULL,
                    parameter_name TEXT NOT NULL,
                    baseline_value REAL NOT NULL,
                    variant_value REAL NOT NULL,
                    left_role TEXT NOT NULL CHECK (left_role IN ('A', 'B')),
                    right_role TEXT NOT NULL CHECK (right_role IN ('A', 'B')),
                    a_wav_path TEXT NOT NULL,
                    b_wav_path TEXT NOT NULL,
                    left_play_count INTEGER NOT NULL DEFAULT 0,
                    right_play_count INTEGER NOT NULL DEFAULT 0,
                    meaningful_difference INTEGER CHECK (meaningful_difference IN (0, 1)),
                    judged_at_utc TEXT,
                    UNIQUE (session_id, ordinal)
                );
                PRAGMA user_version = 1;
                """
            )

    @staticmethod
    def _load_manifest(path: Path) -> dict[str, Any]:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("schema") != "agentic-synth-twin/human-ab-probe/v1":
            raise CalibrationError(f"unsupported probe manifest: {path}")
        if not manifest["A"].get("matches_milestone_3"):
            raise CalibrationError(f"probe A is not the accepted baseline: {path}")
        determinism = manifest["determinism"]
        if not determinism.get("A_byte_identical") or not determinism.get(
            "B_byte_identical"
        ):
            raise CalibrationError(f"probe is not deterministic: {path}")
        parameter = manifest["parameter"]
        if parameter.get("variant_applied") != parameter.get("variant_value"):
            raise CalibrationError(f"probe variant was not retained by the synth: {path}")
        for side in ("A", "B"):
            wav = path.parent / manifest[side]["wav"]["filename"]
            if not wav.is_file() or _sha256(wav) != manifest[side]["wav"]["sha256"]:
                raise CalibrationError(f"{side} WAV does not match probe manifest: {path}")
            if manifest[side]["wav"]["clipped_samples"] != 0:
                raise CalibrationError(f"{side} WAV is clipped: {path}")
        return manifest

    def create_session(
        self, manifest_paths: Sequence[str | Path], *, seed: int | None = None
    ) -> str:
        if not manifest_paths:
            raise CalibrationError("a calibration session requires at least one probe")
        records = []
        for value in manifest_paths:
            path = Path(value).resolve()
            records.append((path, self._load_manifest(path)))
        session_seed = seed if seed is not None else secrets.randbelow(2**31)
        generator = random.Random(session_seed)
        generator.shuffle(records)
        session_id = str(uuid.uuid4())
        with self._connect() as connection:
            active = connection.execute(
                "SELECT id FROM calibration_sessions WHERE status = 'active'"
            ).fetchone()
            if active:
                raise CalibrationError(
                    f"active calibration session already exists: {active['id']}"
                )
            connection.execute(
                """
                INSERT INTO calibration_sessions
                    (id, created_at_utc, seed, status, probe_count)
                VALUES (?, ?, ?, 'active', ?)
                """,
                (session_id, _now(), session_seed, len(records)),
            )
            for ordinal, (path, manifest) in enumerate(records, start=1):
                left_role = "A" if generator.randrange(2) == 0 else "B"
                right_role = "B" if left_role == "A" else "A"
                parameter = manifest["parameter"]
                connection.execute(
                    """
                    INSERT INTO calibration_probes (
                        id, session_id, ordinal, manifest_path, parameter_id,
                        parameter_name, baseline_value, variant_value,
                        left_role, right_role, a_wav_path, b_wav_path
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"{session_id}-{ordinal:03d}",
                        session_id,
                        ordinal,
                        str(path),
                        parameter["id"],
                        parameter["name"],
                        parameter["baseline_value"],
                        parameter["variant_value"],
                        left_role,
                        right_role,
                        str(path.parent / manifest["A"]["wav"]["filename"]),
                        str(path.parent / manifest["B"]["wav"]["filename"]),
                    ),
                )
        return session_id

    def active_session_id(self) -> str:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id FROM calibration_sessions
                ORDER BY created_at_utc DESC LIMIT 1
                """
            ).fetchone()
        if not row:
            raise CalibrationError("no calibration session exists")
        return str(row["id"])

    def state(self, session_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            session = connection.execute(
                "SELECT * FROM calibration_sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if not session:
                raise CalibrationError("calibration session does not exist")
            completed = connection.execute(
                """
                SELECT COUNT(*) AS count FROM calibration_probes
                WHERE session_id = ? AND meaningful_difference IS NOT NULL
                """,
                (session_id,),
            ).fetchone()["count"]
            probe = connection.execute(
                """
                SELECT * FROM calibration_probes
                WHERE session_id = ? AND meaningful_difference IS NULL
                ORDER BY ordinal LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        result: dict[str, Any] = {
            "session_id": session_id,
            "status": session["status"],
            "completed": completed,
            "total": session["probe_count"],
        }
        if probe:
            result["probe"] = {
                "id": probe["id"],
                "number": probe["ordinal"],
                "left_heard": probe["left_play_count"] > 0,
                "right_heard": probe["right_play_count"] > 0,
                "left_url": f"/audio/{probe['id']}/left.wav",
                "right_url": f"/audio/{probe['id']}/right.wav",
            }
        else:
            result["probe"] = None
        return result

    def audio_path(self, session_id: str, probe_id: str, side: str) -> Path:
        if side not in {"left", "right"}:
            raise CalibrationError("audio side must be left or right")
        with self._connect() as connection:
            probe = connection.execute(
                """
                SELECT * FROM calibration_probes
                WHERE id = ? AND session_id = ?
                """,
                (probe_id, session_id),
            ).fetchone()
        if not probe:
            raise CalibrationError("probe does not exist")
        role = probe[f"{side}_role"]
        path = Path(probe["a_wav_path"] if role == "A" else probe["b_wav_path"])
        if not path.is_file():
            raise CalibrationError("probe audio is missing")
        return path

    def record_listen(self, session_id: str, probe_id: str, side: str) -> dict[str, Any]:
        if side not in {"left", "right"}:
            raise CalibrationError("listen side must be left or right")
        column = "left_play_count" if side == "left" else "right_play_count"
        with self._connect() as connection:
            current = connection.execute(
                """
                SELECT id FROM calibration_probes
                WHERE session_id = ? AND meaningful_difference IS NULL
                ORDER BY ordinal LIMIT 1
                """,
                (session_id,),
            ).fetchone()
            if not current or current["id"] != probe_id:
                raise CalibrationError("probe is not the current listening task")
            cursor = connection.execute(
                f"""
                UPDATE calibration_probes SET {column} = {column} + 1
                WHERE id = ? AND session_id = ? AND meaningful_difference IS NULL
                """,
                (probe_id, session_id),
            )
            if cursor.rowcount != 1:
                raise CalibrationError("probe is not available for listening")
        return self.state(session_id)

    def record_judgment(
        self, session_id: str, probe_id: str, meaningful_difference: bool
    ) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                """
                SELECT id FROM calibration_probes
                WHERE session_id = ? AND meaningful_difference IS NULL
                ORDER BY ordinal LIMIT 1
                """,
                (session_id,),
            ).fetchone()
            if not current or current["id"] != probe_id:
                raise CalibrationError("probe is not the current listening task")
            probe = connection.execute(
                """
                SELECT * FROM calibration_probes
                WHERE id = ? AND session_id = ? AND meaningful_difference IS NULL
                """,
                (probe_id, session_id),
            ).fetchone()
            if not probe:
                raise CalibrationError("probe is not awaiting a judgment")
            if probe["left_play_count"] < 1 or probe["right_play_count"] < 1:
                raise CalibrationError("listen to both sounds before answering")
            connection.execute(
                """
                UPDATE calibration_probes
                SET meaningful_difference = ?, judged_at_utc = ?
                WHERE id = ?
                """,
                (int(meaningful_difference), _now(), probe_id),
            )
            remaining = connection.execute(
                """
                SELECT COUNT(*) AS count FROM calibration_probes
                WHERE session_id = ? AND meaningful_difference IS NULL
                """,
                (session_id,),
            ).fetchone()["count"]
            if remaining == 0:
                connection.execute(
                    """
                    UPDATE calibration_sessions
                    SET status = 'completed', completed_at_utc = ? WHERE id = ?
                    """,
                    (_now(), session_id),
                )
        return self.state(session_id)

    def export(self, session_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            session = connection.execute(
                "SELECT * FROM calibration_sessions WHERE id = ?", (session_id,)
            ).fetchone()
            probes = connection.execute(
                """
                SELECT ordinal, parameter_id, parameter_name, baseline_value,
                       variant_value, left_role, right_role, left_play_count,
                       right_play_count, meaningful_difference, judged_at_utc
                FROM calibration_probes WHERE session_id = ? ORDER BY ordinal
                """,
                (session_id,),
            ).fetchall()
        if not session:
            raise CalibrationError("calibration session does not exist")
        if session["status"] != "completed":
            raise CalibrationError("results export is available after session completion")
        return {
            "schema": "agentic-synth-twin/calibration-results/v1",
            "session": dict(session),
            "probes": [dict(probe) for probe in probes],
        }


def render_calibration_page() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" href="data:,">
  <title>Human A/B Calibration</title>
  <style>
    :root { color-scheme:light; --ink:#111827; --muted:#647084; --line:#dce2eb; --blue:#0759d9; --green:#087a55; --red:#b83232; --paper:#f4f7fb; }
    * { box-sizing:border-box; }
    body { margin:0; min-height:100vh; display:grid; place-items:center; background:var(--paper); color:var(--ink); font:16px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
    main { width:min(820px,calc(100% - 28px)); background:#fff; border:1px solid var(--line); border-radius:20px; padding:32px; box-shadow:0 18px 60px rgba(15,23,42,.09); }
    .eyebrow { margin:0 0 8px; color:var(--blue); font-size:12px; font-weight:800; letter-spacing:.1em; text-transform:uppercase; }
    h1 { margin:0; font-size:clamp(30px,5vw,50px); letter-spacing:-.04em; }
    .sub { margin:8px 0 24px; color:var(--muted); }
    .progress { height:8px; border-radius:99px; background:#e7ebf1; overflow:hidden; }
    .progress span { display:block; height:100%; width:0; background:var(--blue); transition:width .25s ease; }
    #count { margin:9px 0 28px; color:var(--muted); font-size:14px; }
    .sounds { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
    button { border:0; border-radius:13px; padding:18px 20px; font:inherit; font-weight:800; cursor:pointer; transition:transform .08s ease,opacity .15s ease; }
    button:active { transform:translateY(1px); }
    button:focus-visible { outline:4px solid #a8c7ff; outline-offset:3px; }
    button:disabled { cursor:not-allowed; opacity:.38; }
    .play { min-height:88px; background:#edf3ff; color:#0748a9; border:1px solid #c8dafc; }
    .play.heard { background:#e8f7f1; color:var(--green); border-color:#b4e2d2; }
    .question { margin:30px 0 14px; text-align:center; font-size:20px; font-weight:750; }
    .answers { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
    .yes { background:var(--green); color:#fff; }
    .no { background:var(--red); color:#fff; }
    #status { min-height:24px; margin:16px 0 0; color:var(--muted); text-align:center; }
    .done { text-align:center; padding:28px 0 6px; }
    .done h2 { font-size:32px; margin:0 0 8px; }
    .download { display:inline-block; margin-top:16px; color:var(--blue); font-weight:750; }
    [hidden] { display:none !important; }
    @media (max-width:600px) { main { padding:24px; } .sounds,.answers { grid-template-columns:1fr; } }
  </style>
</head>
<body>
<main>
  <p class="eyebrow">Agentic Synth Twin · Local calibration</p>
  <h1>Human A/B probe</h1>
  <p class="sub">Listen to both sounds. Judge only whether the difference is meaningful; preference comes later.</p>
  <section id="active">
    <div class="progress" aria-label="Calibration progress"><span id="bar"></span></div>
    <p id="count">Loading session…</p>
    <div class="sounds">
      <button class="play" id="left" type="button">▶ PLAY SOUND 1</button>
      <button class="play" id="right" type="button">▶ PLAY SOUND 2</button>
    </div>
    <audio id="leftAudio" preload="auto"></audio>
    <audio id="rightAudio" preload="auto"></audio>
    <p class="question">Meaningfully different?</p>
    <div class="answers">
      <button class="yes" id="yes" type="button" disabled>YES · DIFFERENT</button>
      <button class="no" id="no" type="button" disabled>NO · NOT MEANINGFUL</button>
    </div>
    <p id="status" aria-live="polite">Play both sounds to answer.</p>
  </section>
  <section class="done" id="done" hidden>
    <h2>Calibration complete</h2>
    <p>Your judgments are saved locally in SQLite.</p>
    <a class="download" href="/api/export">DOWNLOAD RESULTS JSON</a>
  </section>
</main>
<script>
  let state = null;
  const byId = (id) => document.getElementById(id);
  const left = byId('left'), right = byId('right');
  const yes = byId('yes'), no = byId('no'), status = byId('status');
  const leftAudio = byId('leftAudio'), rightAudio = byId('rightAudio');

  async function request(path, body) {
    const response = await fetch(path, body === undefined ? {} : {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)
    });
    const value = await response.json();
    if (!response.ok) throw new Error(value.error || 'Request failed');
    return value;
  }
  function render() {
    byId('bar').style.width = `${state.total ? state.completed / state.total * 100 : 0}%`;
    if (!state.probe) {
      byId('active').hidden = true; byId('done').hidden = false; return;
    }
    byId('active').hidden = false; byId('done').hidden = true;
    byId('count').textContent = `Probe ${state.probe.number} of ${state.total}`;
    if (leftAudio.getAttribute('src') !== state.probe.left_url) leftAudio.src = state.probe.left_url;
    if (rightAudio.getAttribute('src') !== state.probe.right_url) rightAudio.src = state.probe.right_url;
    left.classList.toggle('heard', state.probe.left_heard);
    right.classList.toggle('heard', state.probe.right_heard);
    const ready = state.probe.left_heard && state.probe.right_heard;
    yes.disabled = !ready; no.disabled = !ready;
    status.textContent = ready ? 'Choose YES or NO.' : 'Play both sounds to answer.';
  }
  async function play(side) {
    const audio = side === 'left' ? leftAudio : rightAudio;
    audio.currentTime = 0;
    fetch('/api/listen', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({probe_id:state.probe.id,side})})
      .then((response) => response.json()).then((value) => { state=value; render(); })
      .catch((error) => { status.textContent=error.message; });
    try { await audio.play(); status.textContent = `Playing Sound ${side === 'left' ? '1' : '2'}…`; }
    catch (error) { status.textContent = `Playback blocked: ${error.message}`; }
  }
  async function answer(value) {
    yes.disabled = true; no.disabled = true; status.textContent = 'Saving judgment…';
    try { state = await request('/api/judgment', {probe_id:state.probe.id, meaningful_difference:value}); render(); }
    catch (error) { status.textContent=error.message; render(); }
  }
  left.addEventListener('click', () => play('left'));
  right.addEventListener('click', () => play('right'));
  yes.addEventListener('click', () => answer(true));
  no.addEventListener('click', () => answer(false));
  request('/api/session').then((value) => { state=value; render(); }).catch((error) => { status.textContent=error.message; });
</script>
</body>
</html>
"""


def create_calibration_server(
    *, database_path: str | Path, host: str = "127.0.0.1", port: int = 8765
) -> ThreadingHTTPServer:
    if host not in LOCAL_HOSTS:
        raise CalibrationError("calibration server must bind to a loopback host")
    store = CalibrationStore(database_path)
    session_id = store.active_session_id()
    page = render_calibration_page().encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'unsafe-inline'; "
                "script-src 'unsafe-inline'; media-src 'self'",
            )
            self.end_headers()
            self.wfile.write(body)

        def _json(self, value: Mapping[str, Any], status: int = 200) -> None:
            self._send(
                (json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8"),
                "application/json; charset=utf-8",
                status,
            )

        def _body(self) -> Mapping[str, Any]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 16_384:
                    raise CalibrationError("invalid request body")
                value = json.loads(self.rfile.read(length))
                if not isinstance(value, Mapping):
                    raise CalibrationError("request body must be an object")
                return value
            except (ValueError, json.JSONDecodeError) as error:
                raise CalibrationError("request body must be valid JSON") from error

        def do_GET(self) -> None:
            try:
                path = urlparse(self.path).path
                if path == "/":
                    self._send(page, "text/html; charset=utf-8")
                elif path == "/api/session":
                    self._json(store.state(session_id))
                elif path == "/api/export":
                    self._json(store.export(session_id))
                elif path.startswith("/audio/"):
                    parts = path.strip("/").split("/")
                    if len(parts) != 3 or parts[0] != "audio":
                        raise CalibrationError("invalid audio path")
                    side = parts[2].removesuffix(".wav")
                    audio = store.audio_path(session_id, parts[1], side)
                    self._send(audio.read_bytes(), "audio/wav")
                else:
                    self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            except CalibrationError as error:
                self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

        def do_POST(self) -> None:
            try:
                origin = self.headers.get("Origin")
                if origin is not None:
                    allowed_origins = {
                        f"http://127.0.0.1:{self.server.server_port}",
                        f"http://localhost:{self.server.server_port}",
                        f"http://[::1]:{self.server.server_port}",
                    }
                    if origin not in allowed_origins:
                        raise CalibrationError("cross-origin writes are not allowed")
                path = urlparse(self.path).path
                body = self._body()
                probe_id = body.get("probe_id")
                if not isinstance(probe_id, str) or not probe_id:
                    raise CalibrationError("probe_id is required")
                if path == "/api/listen":
                    side = body.get("side")
                    if not isinstance(side, str):
                        raise CalibrationError("side is required")
                    self._json(store.record_listen(session_id, probe_id, side))
                elif path == "/api/judgment":
                    answer = body.get("meaningful_difference")
                    if not isinstance(answer, bool):
                        raise CalibrationError("meaningful_difference must be boolean")
                    self._json(store.record_judgment(session_id, probe_id, answer))
                else:
                    self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            except CalibrationError as error:
                self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

        def log_message(self, format: str, *args: object) -> None:
            print(f"calibration: {format % args}")

    return ThreadingHTTPServer((host, port), Handler)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare or serve local A/B calibration")
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--state", required=True)
    prepare.add_argument("--accepted-manifest", required=True)
    prepare.add_argument("--accepted-wav", required=True)
    prepare.add_argument("--plugin", required=True)
    prepare.add_argument("--renderer", required=True)
    prepare.add_argument("--output", required=True)
    prepare.add_argument("--database", required=True)
    prepare.add_argument("--seed", type=int)
    serve = subparsers.add_parser("serve")
    serve.add_argument("--database", required=True)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "prepare":
        manifests = prepare_probe_manifests(
            canonical_state_path=args.state,
            accepted_manifest_path=args.accepted_manifest,
            accepted_wav_path=args.accepted_wav,
            plugin_path=args.plugin,
            renderer_path=args.renderer,
            output_directory=Path(args.output) / "probes",
        )
        store = CalibrationStore(args.database)
        session_id = store.create_session(manifests, seed=args.seed)
        print(f"prepared local calibration session {session_id} with {len(manifests)} probes")
        return 0
    server = create_calibration_server(
        database_path=args.database, host=args.host, port=args.port
    )
    print(f"local calibration: http://{args.host}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
