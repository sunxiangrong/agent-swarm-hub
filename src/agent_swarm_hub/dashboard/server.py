from __future__ import annotations

import json
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from ..project_context import ProjectContextStore
from .snapshot import build_dashboard_snapshot


def serve_dashboard(*, host: str, port: int) -> None:
    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/api/projects":
                payload = build_dashboard_snapshot()
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path in {"/", "/index.html"}:
                body = _dashboard_html().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/projects/") and parsed.path.endswith("/pin"):
                project_id = parsed.path.removeprefix("/api/projects/").removesuffix("/pin").strip("/")
                raw_length = self.headers.get("Content-Length", "0")
                length = int(raw_length) if raw_length.isdigit() else 0
                payload = {}
                if length > 0:
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                pinned = bool(payload.get("pinned"))
                ProjectContextStore().set_project_pinned(project_id, pinned)
                body = json.dumps({"ok": True, "project_id": project_id, "pinned": pinned}, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path.startswith("/api/projects/") and parsed.path.endswith("/sync-memory"):
                project_id = parsed.path.removeprefix("/api/projects/").removesuffix("/sync-memory").strip("/")
                ok = _sync_project_memory(project_id)
                body = json.dumps({"ok": ok, "project_id": project_id}, ensure_ascii=False).encode("utf-8")
                self.send_response(200 if ok else 404)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path.startswith("/api/projects/") and parsed.path.endswith("/open-path"):
                project_id = parsed.path.removeprefix("/api/projects/").removesuffix("/open-path").strip("/")
                ok = _open_project_path(project_id)
                body = json.dumps({"ok": ok, "project_id": project_id}, ensure_ascii=False).encode("utf-8")
                self.send_response(200 if ok else 404)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path.startswith("/api/projects/") and parsed.path.endswith("/focus-driver"):
                project_id = parsed.path.removeprefix("/api/projects/").removesuffix("/focus-driver").strip("/")
                ok = _focus_driver_pane(project_id)
                body = json.dumps({"ok": ok, "project_id": project_id}, ensure_ascii=False).encode("utf-8")
                self.send_response(200 if ok else 404)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

    server = ThreadingHTTPServer((host, port), DashboardHandler)
    print(f"[agent-swarm-hub] dashboard=http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _dashboard_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>agent-swarm-hub dashboard</title>
  <style>
    :root {
      --bg: #10161d;
      --bg-strong: #0a0f14;
      --panel: rgba(17, 24, 32, 0.92);
      --panel-strong: #16202a;
      --line: rgba(146, 166, 184, 0.18);
      --line-strong: rgba(146, 166, 184, 0.3);
      --ink: #edf4fb;
      --muted: #93a5b6;
      --accent: #70d6b6;
      --accent-strong: #baf5e1;
      --warning: #ffcc7a;
      --danger: #ff7d7d;
      --tracked: #8db7ff;
      --shadow: 0 24px 60px rgba(0, 0, 0, 0.28);
      --radius: 18px;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "IBM Plex Sans", "Avenir Next", "Segoe UI", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(112, 214, 182, 0.14) 0, transparent 28%),
        radial-gradient(circle at top right, rgba(141, 183, 255, 0.12) 0, transparent 32%),
        linear-gradient(180deg, #111922, var(--bg));
      min-height: 100vh;
    }
    header {
      padding: 28px 30px 18px;
      border-bottom: 1px solid var(--line);
      background: linear-gradient(180deg, rgba(10, 15, 20, 0.88), rgba(10, 15, 20, 0.42));
      backdrop-filter: blur(18px);
      position: sticky;
      top: 0;
      z-index: 10;
    }
    .header-row {
      display: flex;
      justify-content: space-between;
      gap: 20px;
      align-items: end;
      flex-wrap: wrap;
    }
    h1 {
      margin: 0;
      font-size: 31px;
      letter-spacing: -0.03em;
      font-weight: 650;
    }
    .sub {
      color: var(--muted);
      margin-top: 8px;
      max-width: 760px;
      line-height: 1.5;
    }
    .header-side {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      align-items: center;
    }
    main {
      padding: 26px 24px 40px;
      max-width: 1540px;
      margin: 0 auto;
    }
    .meta {
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      margin-bottom: 22px;
    }
    .pill {
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.03);
      padding: 7px 11px;
      border-radius: 999px;
      color: var(--muted);
      font-size: 12px;
      letter-spacing: 0.02em;
    }
    .section {
      margin-top: 28px;
    }
    .section-head {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      margin-bottom: 12px;
    }
    .section h2 {
      margin: 0;
      font-size: 13px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted);
    }
    .section-note {
      color: var(--muted);
      font-size: 12px;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
      gap: 18px;
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 18px;
      box-shadow: var(--shadow);
      position: relative;
      overflow: hidden;
    }
    .card::before {
      content: "";
      position: absolute;
      inset: 0 auto auto 0;
      width: 100%;
      height: 2px;
      background: linear-gradient(90deg, rgba(112, 214, 182, 0.9), rgba(141, 183, 255, 0.65), transparent);
      opacity: 0.4;
    }
    .card.empty {
      padding: 28px 20px;
      color: var(--muted);
      background: rgba(17, 24, 32, 0.72);
    }
    .card.active {
      border-color: rgba(112, 214, 182, 0.26);
    }
    .card.active::before {
      opacity: 0.9;
    }
    .title {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
      margin-bottom: 14px;
    }
    .title-main h3 {
      margin: 0;
      font-size: 22px;
      line-height: 1.15;
      letter-spacing: -0.03em;
    }
    .title-actions {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
    }
    .path {
      color: var(--muted);
      font-size: 12px;
      word-break: break-all;
      margin-top: 5px;
    }
    .status-badge {
      border-radius: 999px;
      padding: 5px 10px;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.04);
      color: var(--muted);
    }
    .status-badge[data-tone="executing"],
    .status-badge[data-tone="active"] {
      color: var(--accent-strong);
      border-color: rgba(112, 214, 182, 0.4);
      background: rgba(112, 214, 182, 0.1);
    }
    .status-badge[data-tone="discussion"],
    .status-badge[data-tone="reviewing"],
    .status-badge[data-tone="tracked"] {
      color: #c8d9ff;
      border-color: rgba(141, 183, 255, 0.35);
      background: rgba(141, 183, 255, 0.1);
    }
    .status-badge[data-tone="blocked"],
    .status-badge[data-tone="error"] {
      color: #ffd5d5;
      border-color: rgba(255, 125, 125, 0.4);
      background: rgba(255, 125, 125, 0.12);
    }
    .status-badge[data-tone="idle"],
    .status-badge[data-tone="bound"] {
      color: var(--warning);
      border-color: rgba(255, 204, 122, 0.28);
      background: rgba(255, 204, 122, 0.08);
    }
    .quickbar {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-bottom: 14px;
    }
    .quickbar .pill {
      padding: 5px 9px;
      font-size: 11px;
    }
    .summary-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }
    .summary-item {
      border: 1px solid rgba(146, 166, 184, 0.12);
      background: rgba(255, 255, 255, 0.02);
      border-radius: 14px;
      padding: 12px;
      min-height: 106px;
    }
    .summary-item.wide {
      grid-column: 1 / -1;
      min-height: 96px;
    }
    dt {
      font-size: 11px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    dd {
      margin: 7px 0 0;
      font-size: 14px;
      line-height: 1.52;
      color: var(--ink);
    }
    .sessions, .runtime {
      margin-top: 16px;
      padding-top: 14px;
      border-top: 1px dashed rgba(146, 166, 184, 0.18);
    }
    .stack {
      display: grid;
      gap: 10px;
      margin-top: 10px;
    }
    .kv {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }
    .kv span {
      background: rgba(112, 214, 182, 0.1);
      color: var(--accent-strong);
      border-radius: 999px;
      padding: 4px 9px;
      font-size: 12px;
      border: 1px solid rgba(112, 214, 182, 0.14);
    }
    .runtime-item {
      padding: 8px 0;
      border-top: 1px solid rgba(146, 166, 184, 0.1);
    }
    .runtime-item:first-child { border-top: 0; }
    .terminal {
      margin-top: 8px;
      padding: 12px 13px;
      background: linear-gradient(180deg, rgba(7, 11, 15, 0.92), rgba(12, 18, 24, 0.98));
      border: 1px solid rgba(141, 183, 255, 0.12);
      border-radius: 14px;
      color: #d4e4f6;
      font: 12px/1.55 "SFMono-Regular", "Menlo", "Consolas", monospace;
      white-space: pre-wrap;
      word-break: break-word;
      min-height: 96px;
    }
    .pin {
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.02);
      color: var(--muted);
      border-radius: 999px;
      padding: 5px 10px;
      cursor: pointer;
      font: inherit;
      transition: background 120ms ease, border-color 120ms ease, color 120ms ease;
    }
    .pin.active {
      background: rgba(112, 214, 182, 0.1);
      color: var(--accent-strong);
      border-color: rgba(112, 214, 182, 0.26);
    }
    .action-row {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-top: 14px;
    }
    .action {
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.03);
      color: var(--ink);
      border-radius: 10px;
      padding: 8px 10px;
      cursor: pointer;
      font: inherit;
      font-size: 12px;
      transition: border-color 120ms ease, background 120ms ease, color 120ms ease;
    }
    .action:hover,
    .pin:hover {
      border-color: var(--line-strong);
      background: rgba(255, 255, 255, 0.06);
    }
    .action.sync {
      color: var(--accent-strong);
      border-color: rgba(112, 214, 182, 0.24);
    }
    .action.copy {
      color: #d7e4f1;
    }
    .toast {
      position: fixed;
      right: 18px;
      bottom: 18px;
      background: rgba(8, 12, 16, 0.96);
      color: var(--ink);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 10px 12px;
      font-size: 12px;
      box-shadow: var(--shadow);
      opacity: 0;
      transform: translateY(10px);
      pointer-events: none;
      transition: opacity 140ms ease, transform 140ms ease;
    }
    .toast.visible {
      opacity: 1;
      transform: translateY(0);
    }
    .timestamp {
      color: var(--muted);
      font-size: 12px;
    }
    @media (max-width: 860px) {
      main {
        padding: 18px 14px 30px;
      }
      header {
        padding: 22px 16px 16px;
      }
      .grid {
        grid-template-columns: 1fr;
      }
      .summary-grid {
        grid-template-columns: 1fr;
      }
      .summary-item.wide {
        grid-column: auto;
      }
    }
  </style>
</head>
<body>
  <header>
    <div class="header-row">
      <div>
        <h1>agent-swarm-hub</h1>
        <div class="sub">Multi-project command center for deciding what is active, what changed, and which session to open next.</div>
      </div>
      <div class="header-side">
        <div class="pill">watch first</div>
        <div class="pill">tmux aware</div>
        <div class="pill">manual control</div>
      </div>
    </div>
  </header>
  <main>
    <div class="meta" id="meta"></div>
    <section class="section">
      <div class="section-head">
        <h2>Watching Now</h2>
        <div class="section-note">Pinned projects override passive active ordering.</div>
      </div>
      <div class="grid" id="watching"></div>
    </section>
    <section class="section">
      <div class="section-head">
        <h2>Other Projects</h2>
        <div class="section-note">Tracked context that is not in the current watch set.</div>
      </div>
      <div class="grid" id="others"></div>
    </section>
  </main>
  <div class="toast" id="toast"></div>
  <script>
    function escapeHtml(value) {
      return String(value || '').replace(/[&<>"']/g, (char) => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
      }[char]));
    }

    function statusTone(value) {
      const status = String(value || '').toLowerCase();
      if (['executing', 'running', 'active'].includes(status)) return 'executing';
      if (['discussion', 'reviewing', 'tracked'].includes(status)) return status;
      if (['blocked', 'error', 'failed'].includes(status)) return 'blocked';
      if (['completed', 'merged'].includes(status)) return 'executing';
      if (['bound'].includes(status)) return 'bound';
      return 'idle';
    }

    function renderCard(project) {
      const pinLabel = project.pinned ? 'Unpin' : 'Pin';
      const pinClass = project.pinned ? 'pin active' : 'pin';
      const tone = statusTone(project.status);
      const currentSessions = project.current_sessions ? `<span>${escapeHtml(project.current_sessions)}</span>` : '<span>no current binding</span>';
      const tmuxPreview = project.tmux_preview ? escapeHtml(project.tmux_preview) : 'No tmux pane attached.';
      const updatedAt = project.updated_at ? `<div class="timestamp">updated ${escapeHtml(project.updated_at)}</div>` : '';
      const swarmAgents = (project.swarm_agents || []).map((agent) => `
        <div class="runtime-item">
          <strong>${escapeHtml(agent.name || 'agent')}</strong>
          <span class="status-badge" data-tone="${statusTone(agent.status)}">${escapeHtml(agent.status || 'idle')}</span>
          <div class="timestamp">${escapeHtml(agent.backend || 'native cli')}</div>
          ${agent.launch_status ? `<div class="timestamp">launch ${escapeHtml(agent.launch_status)} ${escapeHtml(agent.launch_pane_id || '')}</div>` : ''}
          <div>${escapeHtml(agent.summary || 'No result summary yet.')}</div>
        </div>
      `).join('');
      const ccbProviders = (project.ccb_live_providers || []).map((item) => `
        <div class="runtime-item">
          <strong>${escapeHtml(item.provider || 'provider')}</strong>
          <span class="status-badge" data-tone="active">live</span>
          <div class="timestamp">pane ${escapeHtml(item.pane_id || item.pane_title_marker || 'unknown')}</div>
          <div>${escapeHtml(item.session_id || item.ccb_session_id || 'ccb session active')}</div>
        </div>
      `).join('');
      const fallbackSwarmOverview = `
              <div class="runtime-item">No live swarm capture yet.</div>
              <div class="runtime-item">Current phase: ${escapeHtml(project.live_phase || 'discussion')}</div>
              <div class="runtime-item">${escapeHtml(project.live_summary || project.memory || 'The current native/ccb run has not written runtime swarm state yet.')}</div>
              ${ccbProviders || ''}
            `;
      const swarmOverview = project.swarm_active
        ? `
              <div class="runtime-item">Task: ${escapeHtml(project.swarm_task_id || 'none')}</div>
              <div class="runtime-item">Session: ${escapeHtml(project.swarm_session_key || 'none')}</div>
              ${
                project.swarm_orchestrator_launch && project.swarm_orchestrator_launch.status
                  ? `<div class="runtime-item">Orchestrator Launch: ${escapeHtml(project.swarm_orchestrator_launch.provider || 'claude')} (${escapeHtml(project.swarm_orchestrator_launch.status || '')})</div>`
                  : ''
              }
              <div class="runtime-item">Agents: ${escapeHtml(String(project.swarm_agent_count || 0))} | Handoffs: ${escapeHtml(String(project.swarm_handoff_count || 0))}</div>
              <div class="runtime-item">${escapeHtml(project.swarm_summary || 'Swarm execution is active.')}</div>
              ${swarmAgents || '<div class="runtime-item">No active sub-agent runs recorded.</div>'}
            `
        : fallbackSwarmOverview;
      const driverLine = project.driver_session_id || project.driver_tmux_pane_id
        ? `
              <div class="runtime-item">Driver Session: ${escapeHtml(project.driver_session_id || 'none')}</div>
              <div class="runtime-item">Driver Pane: ${escapeHtml(project.driver_tmux_pane_id || 'none')} ${escapeHtml(project.driver_tmux_title || '')}</div>
            `
        : `
              <div class="runtime-item">Orchestrator is ${escapeHtml(project.current_driver || 'claude')}, but no live session/pane is attached right now.</div>
            `;
      return `
        <section class="card ${project.active ? 'active' : ''}">
          <div class="title">
            <div class="title-main">
              <h3>${escapeHtml(project.project_id)}</h3>
              <div class="path">${escapeHtml(project.workspace_path || '')}</div>
            </div>
            <div class="title-actions">
              <button class="${pinClass}" data-project="${project.project_id}" data-pinned="${project.pinned ? '1' : '0'}">${pinLabel}</button>
              <div class="status-badge" data-tone="${tone}">${escapeHtml(project.status || 'idle')}</div>
            </div>
          </div>
          <div class="quickbar">
            <div class="pill">${project.active ? 'active now' : 'passive record'}</div>
            <div class="pill">${escapeHtml(project.session_count_text || 'active 0 / archived 0')}</div>
            ${updatedAt}
          </div>
          <dl class="summary-grid">
            <div class="summary-item"><dt>Current Focus</dt><dd>${escapeHtml(project.focus || 'No focus recorded.')}</dd></div>
            <div class="summary-item"><dt>Current State</dt><dd>${escapeHtml(project.state || 'No current state recorded.')}</dd></div>
            <div class="summary-item"><dt>Next Step</dt><dd>${escapeHtml(project.next_step || 'No explicit next step.')}</dd></div>
            <div class="summary-item"><dt>Current Sessions</dt><dd><div class="kv">${currentSessions}</div></dd></div>
            <div class="summary-item wide"><dt>Live Summary</dt><dd>${escapeHtml(project.live_summary || project.memory || 'No live summary yet.')}</dd></div>
          </dl>
          <div class="sessions">
            <dt>Signal</dt>
            <div class="stack">
              <div class="runtime-item">Phase: ${escapeHtml(project.live_phase || 'n/a')}</div>
              <div class="runtime-item">Inventory: ${escapeHtml(project.session_count_text || 'active 0 / archived 0')}</div>
            </div>
          </div>
          <div class="runtime">
            <dt>tmux Preview</dt>
            <div class="terminal">${tmuxPreview}</div>
          </div>
          <div class="runtime">
            <dt>Swarm</dt>
            <div class="stack">
              <div class="runtime-item">Trigger Terminal: ${escapeHtml(project.current_trigger || 'claude')}</div>
              <div class="runtime-item">Swarm Orchestrator: ${escapeHtml(project.current_driver || 'claude')}</div>
              <div class="runtime-item">Review Return Target: ${escapeHtml(project.review_return_target || project.current_driver || 'claude')}</div>
              <div class="runtime-item">Flow: ${escapeHtml(project.swarm_roles_text || 'trigger=claude | orchestrator=claude | planner=claude | executor=codex | reviewer=claude')}</div>
              <div class="runtime-item">Live CCB Providers: ${escapeHtml(String(project.ccb_live_count || 0))}</div>
              ${driverLine}
              ${swarmOverview}
            </div>
          </div>
          <div class="action-row">
            <button class="action focus" data-action="focus-driver" data-project="${project.project_id}">Focus Driver</button>
            <button class="action sync" data-action="sync" data-project="${project.project_id}">Sync Memory</button>
            <button class="action open" data-action="open" data-project="${project.project_id}">Open Project</button>
          </div>
        </section>
      `;
    }

    function showToast(message) {
      const toast = document.getElementById('toast');
      toast.textContent = message;
      toast.classList.add('visible');
      window.clearTimeout(window.__ashToastTimer);
      window.__ashToastTimer = window.setTimeout(() => {
        toast.classList.remove('visible');
      }, 1800);
    }

    async function setPinned(projectId, pinned) {
      await fetch(`/api/projects/${projectId}/pin`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pinned })
      });
      await load();
    }

    async function syncMemory(projectId) {
      const response = await fetch(`/api/projects/${projectId}/sync-memory`, {
        method: 'POST'
      });
      if (!response.ok) {
        showToast(`sync failed: ${projectId}`);
        return;
      }
      showToast(`synced ${projectId}`);
      await load();
    }

    async function openProject(projectId) {
      const response = await fetch(`/api/projects/${projectId}/open-path`, {
        method: 'POST'
      });
      if (!response.ok) {
        showToast(`open failed: ${projectId}`);
        return;
      }
      showToast(`opened ${projectId}`);
    }

    async function focusDriver(projectId) {
      const response = await fetch(`/api/projects/${projectId}/focus-driver`, {
        method: 'POST'
      });
      if (!response.ok) {
        showToast(`focus failed: ${projectId}`);
        return;
      }
      showToast(`focused ${projectId}`);
    }

    async function load() {
      const response = await fetch('/api/projects');
      const payload = await response.json();
      const watchedProjects = payload.watched_projects || [];
      const allProjects = payload.projects || [];
      const watchedIds = new Set(watchedProjects.map((item) => item.project_id));
      const otherProjects = allProjects.filter((item) => !watchedIds.has(item.project_id));
      document.getElementById('meta').innerHTML = [
        `<div class="pill">projects ${payload.project_count}</div>`,
        `<div class="pill">watching ${payload.pinned_project_count || payload.active_project_count}</div>`,
        `<div class="pill">active ${payload.active_project_count}</div>`,
        `<div class="pill">refresh 15s</div>`
      ].join('');
      document.getElementById('watching').innerHTML = watchedProjects.map(renderCard).join('') || '<div class="card empty">No pinned or active projects.</div>';
      document.getElementById('others').innerHTML = otherProjects.map(renderCard).join('') || '<div class="card empty">No additional projects.</div>';
      document.querySelectorAll('.pin').forEach((button) => {
        button.addEventListener('click', async () => {
          const projectId = button.dataset.project;
          const pinned = button.dataset.pinned !== '1';
          await setPinned(projectId, pinned);
        });
      });
      document.querySelectorAll('.action').forEach((button) => {
        button.addEventListener('click', async () => {
          const action = button.dataset.action;
          if (action === 'sync') {
            await syncMemory(button.dataset.project);
            return;
          }
          if (action === 'open') {
            await openProject(button.dataset.project);
            return;
          }
          if (action === 'focus-driver') {
            await focusDriver(button.dataset.project);
          }
        });
      });
    }
    load();
    setInterval(load, 15000);
  </script>
</body>
</html>"""


def _sync_project_memory(project_id: str, store: ProjectContextStore | None = None) -> bool:
    store = store or ProjectContextStore()
    if not store.get_project(project_id):
        return False
    store.sync_project_summary(project_id)
    store.sync_project_memory_file(project_id)
    store.sync_project_skill_file(project_id)
    return True


def _open_project_path(project_id: str, store: ProjectContextStore | None = None) -> bool:
    store = store or ProjectContextStore()
    project = store.get_project(project_id)
    if project is None or not (project.workspace_path or "").strip():
        return False
    workspace_path = Path(project.workspace_path).expanduser()
    if not workspace_path.exists():
        return False
    try:
        subprocess.Popen(["open", str(workspace_path)])
    except OSError:
        return False
    return True


def _focus_driver_pane(project_id: str, store: ProjectContextStore | None = None) -> bool:
    store = store or ProjectContextStore()
    snapshot = build_dashboard_snapshot(project_store=store)
    project = next((item for item in snapshot.get("projects", []) if item.get("project_id") == project_id), None)
    if not project:
        return False
    session_name = str(project.get("driver_tmux_session_name") or "").strip()
    window_index = str(project.get("driver_tmux_window_index") or "").strip()
    pane_id = str(project.get("driver_tmux_pane_id") or "").strip()
    if not pane_id:
        return False
    try:
        if session_name:
            subprocess.run(
                ["tmux", "switch-client", "-t", session_name],
                check=False,
                capture_output=True,
                text=True,
            )
        if session_name and window_index:
            subprocess.run(
                ["tmux", "select-window", "-t", f"{session_name}:{window_index}"],
                check=False,
                capture_output=True,
                text=True,
            )
        result = subprocess.run(
            ["tmux", "select-pane", "-t", pane_id],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    return result.returncode == 0
