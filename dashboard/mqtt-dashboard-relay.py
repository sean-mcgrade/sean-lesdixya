"""
mqtt-dashboard-relay.py — Live Dashboard Relay Daemon
Subscribes to MQTT, polls HA/Syncthing, reads CC session data,
renders TrueColor ANSI dashboard, writes atomic file for statusLine.
"""
import os
import sys
import json
import time
import signal
import textwrap
import threading
import logging
from pathlib import Path
from datetime import datetime, timezone
from collections import deque

import psutil
import requests
import paho.mqtt.client as mqtt
from dotenv import load_dotenv

# ── Paths ──
BASE = Path(r"C:\ClaudeCode")
PATCH_VER_FILE = Path(
    r"C:\Users\mcgra\AppData\Local\mcp-bin\node_modules"
    r"\@anthropic-ai\claude-code\cli.js.patch-version"
)
def _read_patch_version():
    try:
        first = PATCH_VER_FILE.read_text(encoding="utf-8").split("\n")[0]
        # e.g. "claude-ast-patch v3.1.0 (2026-03-12)"
        if " v" in first:
            return first.split(" v")[1].split(" ")[0]
    except Exception:
        pass
    return "?.?.?"
PATCH_VERSION = _read_patch_version()

def full_model_name(model_id, display_name):
    # claude-opus-4-8 -> Opus 4.8 ; falls back to display_name
    if model_id and model_id.startswith("claude-"):
        parts = model_id.split("-")
        if len(parts) >= 4 and parts[2].isdigit() and parts[3].isdigit():
            return f"{parts[1].capitalize()} {parts[2]}.{parts[3]}"
    return display_name

ENV_FILE = BASE / ".env"
ANSI_FILE = BASE / "live-dashboard.ansi"
ANSI_TMP = BASE / "live-dashboard.ansi.tmp"
CC_SESSION = BASE / "cc-session.json"
LOG_FILE = BASE / "dashboard-relay.log"

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("relay")

# ── Load env ──
load_dotenv(ENV_FILE)
MQTT_HOST = os.getenv("MQTT_HOST", "100.82.12.120")  # Tailscale IP for Legion
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER = os.getenv("MQTT_USER", "agent0")
MQTT_PASS = os.getenv("MQTT_PASS", "agent0_lab2026")
HA_URL = os.getenv("HA_URL", "")
HA_TOKEN = os.getenv("HA_TOKEN", "")
HA_SENSORS = [s.strip() for s in os.getenv("HA_SENSORS", "").split(",") if s.strip()]
ST_URL = os.getenv("ST_URL", "http://127.0.0.1:8384")
ST_API_KEY = os.getenv("ST_API_KEY", "")

# ── ANSI Constants ──
ESC = "\033"
# Dashboard width — auto-size per machine (Legion is 1024x768, others are larger)
# Set DASH_WIDTH env var to override, otherwise detect from screen resolution
import os as _os
_dash_w = int(_os.environ.get("DASH_WIDTH", "0"))
if not _dash_w:
    _hostname = _os.environ.get("COMPUTERNAME", "").upper()
    _dash_w = 36 if _hostname in ("LEGION", "DEVBOX1") else 56  # narrow columns on 24% width
W = _dash_w
BW = W - 2
LW = BW // 2
RW = BW - LW - 1

# Color palette — Mission Control style
C_BORDER = "0;200;200"        # Cyan/teal — dashboard frame
C_CYAN = "0;255;255"
C_WHITE = "255;255;255"
C_GRAY = "200;200;200"
C_DIM = "120;120;120"
C_DIV = "80;80;80"
C_GREEN = "0;255;0"
C_RED = "255;60;60"
C_ORANGE = "255;165;0"
C_MINT = "0;255;180"
C_PINK = "255;100;150"
C_CORAL = "255;140;100"
C_BLUE = "80;160;255"
C_PURPLE = "180;120;255"
C_TEAL = "0;200;200"
C_SKY = "100;200;255"

# Section header background (uniform blue)
BG_HDR = "20;40;80"


# ═══════════════════════════════════════════════════════════════════════
# STATE
# ═══════════════════════════════════════════════════════════════════════

class DashState:
    """Thread-safe dashboard state container."""

    def __init__(self):
        self.lock = threading.RLock()
        self.nodes = {
            "seanwhite": {"status": "unknown", "ts": 0, "cpu": 0, "ram_pct": 0, "ram_used": 0, "ram_total": 0},
            "lenovom82a": {"status": "unknown", "ts": 0, "cpu": 0, "ram_pct": 0, "ram_used": 0, "ram_total": 0},
            "legion": {"status": "unknown", "ts": 0, "cpu": 0, "ram_pct": 0, "ram_used": 0, "ram_total": 0},
            "devbox": {"status": "unknown", "ts": 0, "cpu": 0, "ram_pct": 0, "ram_used": 0, "ram_total": 0},
        }
        self.mqtt_connected = False
        self.mqtt_msgs_in = 0
        self.mqtt_msgs_out = 0
        self.mqtt_last_topic = ""
        self.a0_message = "Awaiting orchestrator directive..."
        self.a0_tasks = deque(maxlen=3)
        self.recent_tools = deque(maxlen=3)
        self.subagents = []
        self.hardware_temps = {}
        self.syncthing = {"peers": 0, "total": 4, "pct": 0, "pending": "0B"}
        self.labclip = {"status": "unknown", "clips": 0}
        self.alerts = deque(maxlen=3)
        self.latency_history = deque(maxlen=12)
        self.cc_messages = deque(maxlen=5)
        self.a0_dialogue = deque(maxlen=5)
        self.activity_log = deque(maxlen=5)
        self.cc_per_machine = {
            "seanwhite": {"tool": "", "event": "", "ts": 0, "status": "", "status_ts": 0, "sessions": {}},
            "legion": {"tool": "", "event": "", "ts": 0, "status": "", "status_ts": 0, "sessions": {}},
            "lenovom82a": {"tool": "", "event": "", "ts": 0, "status": "", "status_ts": 0, "sessions": {}},
            "devbox": {"tool": "", "event": "", "ts": 0, "status": "", "status_ts": 0, "sessions": {}},
        }
        self.cc = {
            "cost": 0.0,
            "on_subscription": False,
            "tokens_in": 0,
            "tokens_out": 0,
            "cache_read": 0,
            "cache_write": 0,
            "context_pct": 0,
            "compactions": 0,
            "model": "opus-4-6",
            "style": "Default",
            "current_tool": None,
            "session_id": "",
            "api_duration_ms": 0,
            "version": "",
        }
        self._prev_api_dur = 0
        self.cc_mode = "INTERACTIVE"
        self.local_hw = {
            "cpu_pct": 0,
            "ram_used_gb": 0.0,
            "ram_total_gb": 0.0,
            "disk_used_gb": 0.0,
            "disk_total_gb": 0.0,
            "uptime_str": "0d 0h 0m",
            "net_up": True,
        }


state = DashState()
shutdown_event = threading.Event()


# ═══════════════════════════════════════════════════════════════════════
# MQTT
# ═══════════════════════════════════════════════════════════════════════

def on_connect(client, userdata, flags, reason_code, properties):
    log.info(f"MQTT connected: {reason_code}")
    with state.lock:
        state.mqtt_connected = True
    client.subscribe([
        ("lab/a0/#", 1),
        ("lab/cc/#", 1),
        ("lab/labclip/#", 1),
        ("lab/node/+/load", 1),
        ("lab/tasks/+/result", 1),
    ])


def on_disconnect(client, userdata, flags, reason_code, properties):
    log.warning(f"MQTT disconnected: {reason_code}")
    with state.lock:
        state.mqtt_connected = False


def on_message(client, userdata, msg):
    topic = msg.topic
    try:
        payload = msg.payload.decode("utf-8", errors="replace")
    except Exception:
        return

    with state.lock:
        state.mqtt_msgs_in += 1
        state.mqtt_last_topic = topic

    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        data = {"raw": payload}

    now = time.time()

    with state.lock:
        # Heartbeats
        if topic.startswith("lab/a0/heartbeat/"):
            node = topic.split("/")[-1]
            if node in state.nodes:
                state.nodes[node]["status"] = "online"
                state.nodes[node]["ts"] = now
                if isinstance(data, dict):
                    if "cpu_pct" in data:
                        state.nodes[node]["cpu"] = int(data["cpu_pct"])
                    if "ram_pct" in data:
                        state.nodes[node]["ram_pct"] = int(data["ram_pct"])
                    if "ram_used_gb" in data:
                        state.nodes[node]["ram_used"] = data["ram_used_gb"]
                    if "ram_total_gb" in data:
                        state.nodes[node]["ram_total"] = data["ram_total_gb"]

        # A0 commands
        elif topic.startswith("lab/a0/cmd/"):
            if isinstance(data, dict):
                msg_text = data.get("prompt", data.get("message", payload[:200]))
                state.a0_message = str(msg_text)[:200]
                tid = data.get("task_id", f"t{int(now)%10000}")
                target = topic.split("/")[-1]
                state.a0_tasks.append({
                    "id": str(tid)[-3:],
                    "target": target,
                    "status": "run",
                })
                state.a0_dialogue.append({"dir": "IN", "msg": str(msg_text)[:50], "ts": now})

        # Task status updates
        elif "/tasks/" in topic and topic.endswith("/status"):
            if isinstance(data, dict):
                tid = topic.split("/")[-2]
                st = data.get("status", "unknown")
                for t in state.a0_tasks:
                    if t["id"] == str(tid)[-3:]:
                        t["status"] = st[:6]

        # Task results (A0 dialogue OUT)
        elif "/tasks/" in topic and topic.endswith("/result"):
            if isinstance(data, dict):
                result = data.get("result", data.get("message", payload[:50]))
                state.a0_dialogue.append({"dir": "OUT", "msg": str(result)[:50], "ts": now})

        # CC-to-CC inbox messages
        elif topic in ("lab/cc/seanwhite/inbox", "lab/cc/broadcast"):
            if isinstance(data, dict):
                msg = data.get("message", payload[:100])
                frm = data.get("from", "?")
                state.cc_messages.append({"from": frm, "msg": str(msg)[:50], "ts": now})
            else:
                state.cc_messages.append({"from": "?", "msg": str(payload)[:50], "ts": now})

        # CC activity (hook events)
        elif topic.startswith("lab/cc/") and topic.endswith("/activity"):
            # Track per-machine CC activity
            machine = topic.split("/")[2]  # lab/cc/{machine}/activity
            if machine in state.cc_per_machine:
                if isinstance(data, dict):
                    state.cc_per_machine[machine]["tool"] = data.get("tool_name", data.get("tool", ""))
                    state.cc_per_machine[machine]["event"] = data.get("event", "")
                    state.cc_per_machine[machine]["desc"] = data.get("description", "")
                    state.cc_per_machine[machine]["ts"] = now
            if isinstance(data, dict):
                tool = data.get("tool_name", data.get("tool", ""))
                event = data.get("event", "")
                desc = data.get("description", "")
                if event == "PostToolUse" and tool:
                    dur = data.get("duration_ms", 0)
                    status = "OK" if not data.get("error") else "FAIL"
                    state.recent_tools.append({
                        "name": tool[:10],
                        "dur": dur,
                        "status": status,
                    })
                    state.cc["current_tool"] = tool
                    if desc:
                        state.activity_log.append({"msg": desc[:45], "ts": now})
                elif event == "SubagentStart":
                    state.subagents.append({
                        "type": data.get("agent_type", "?")[:6],
                        "status": "run",
                    })
                    if desc:
                        state.activity_log.append({"msg": desc[:45], "ts": now})
                elif event == "SubagentStop":
                    if state.subagents:
                        state.subagents[-1]["status"] = "done"
                elif event in ("SessionStart", "Stop") and desc:
                    state.activity_log.append({"msg": desc[:45], "ts": now})

        # CC daemon status per machine (new scheme: lab/cc/{machine}/status)
        elif topic.startswith("lab/cc/") and topic.endswith("/status") and topic.count("/") == 3:
            machine = topic.split("/")[2]
            cm = state.cc_per_machine.get(machine)
            if cm is not None and isinstance(data, dict):
                cm["status"] = str(data.get("status", "")).lower()[:8]
                cm["status_ts"] = now

        # CC session presence/card per machine (new scheme: lab/cc/{machine}/session/{id}/presence|card)
        elif topic.startswith("lab/cc/") and "/session/" in topic and (
                topic.endswith("/presence") or topic.endswith("/card")):
            parts = topic.split("/")
            machine = parts[2]
            sid = parts[4] if len(parts) > 4 else ""
            cm = state.cc_per_machine.get(machine)
            if cm is not None and sid:
                sessions = cm["sessions"]
                online = True
                if isinstance(data, dict) and "status" in data:
                    online = str(data.get("status", "")).lower() != "offline"
                if online:
                    info = sessions.get(sid, {})
                    info["ts"] = now
                    if isinstance(data, dict) and data.get("mode"):
                        info["mode"] = data.get("mode")
                    sessions[sid] = info
                else:
                    sessions.pop(sid, None)

        # LabClip heartbeat
        elif topic.startswith("lab/labclip/heartbeat/"):
            state.labclip["status"] = "running"
            state.labclip["ts"] = now


def setup_mqtt():
    import socket
    hostname = socket.gethostname().lower()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"dashboard-relay-{hostname}")
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    return client


# ═══════════════════════════════════════════════════════════════════════
# HTTP POLLERS
# ═══════════════════════════════════════════════════════════════════════

def poll_home_assistant():
    """Poll HA REST API for hardware temperatures every 10s."""
    while not shutdown_event.is_set():
        if HA_URL and HA_TOKEN:
            headers = {"Authorization": f"Bearer {HA_TOKEN}"}
            for sensor in HA_SENSORS:
                try:
                    r = requests.get(
                        f"{HA_URL}/api/states/{sensor}",
                        headers=headers,
                        timeout=5,
                    )
                    if r.status_code == 200:
                        data = r.json()
                        name = sensor.replace("sensor.", "").replace("_temperature", "")
                        # Clean up sentinel sensor names: "sentinel_X_X" → "X"
                        if name.startswith("sentinel_"):
                            parts = name.split("_", 2)
                            name = parts[1] if len(parts) > 1 else name
                        temp = data.get("state", "--")
                        with state.lock:
                            state.hardware_temps[name] = temp
                except Exception as ex:
                    log.debug(f"HA poll failed for {sensor}: {ex}")
        shutdown_event.wait(10)


def poll_syncthing():
    """Poll Syncthing REST API for sync status every 10s."""
    while not shutdown_event.is_set():
        if ST_URL:
            headers = {"X-API-Key": ST_API_KEY} if ST_API_KEY else {}
            # Connections
            try:
                r = requests.get(f"{ST_URL}/rest/system/connections", headers=headers, timeout=5)
                if r.status_code == 200:
                    conns = r.json().get("connections", {})
                    connected = sum(1 for c in conns.values() if c.get("connected"))
                    with state.lock:
                        state.syncthing["peers"] = connected
                        state.syncthing["total"] = max(len(conns), 1)
            except Exception as ex:
                log.debug(f"Syncthing connections poll failed: {ex}")
            # Completion
            try:
                r = requests.get(f"{ST_URL}/rest/db/completion", headers=headers, timeout=5)
                if r.status_code == 200:
                    data = r.json()
                    with state.lock:
                        state.syncthing["pct"] = int(data.get("completion", 0))
                        nb = data.get("needBytes", 0)
                        state.syncthing["pending"] = fmt_bytes(nb)
            except Exception as ex:
                log.debug(f"Syncthing completion poll failed: {ex}")
        shutdown_event.wait(10)


def read_cc_session():
    """Read CC session JSON written by the statusLine bridge script."""
    while not shutdown_event.is_set():
        try:
            if CC_SESSION.exists():
                raw = CC_SESSION.read_text(encoding="utf-8-sig")
                data = json.loads(raw)
                with state.lock:
                    cc = state.cc
                    cost = data.get("cost", {})
                    cc["cost"] = cost.get("total_cost_usd", cc["cost"])
                    # Subscription (Max/Pro) reports five_hour/seven_day rate-limit windows;
                    # pure API does not. On subscription the per-session $ is not real spend.
                    rl = data.get("rate_limits") or {}
                    cc["on_subscription"] = bool(rl.get("five_hour") or rl.get("seven_day"))
                    api_dur = cost.get("total_api_duration_ms", 0)
                    if api_dur > state._prev_api_dur and state._prev_api_dur > 0:
                        delta = api_dur - state._prev_api_dur
                        state.latency_history.append(delta)
                    state._prev_api_dur = api_dur
                    cw = data.get("context_window", {})
                    cc["tokens_in"] = cw.get("total_input_tokens", cc["tokens_in"])
                    cc["tokens_out"] = cw.get("total_output_tokens", cc["tokens_out"])
                    cc["context_pct"] = cw.get("used_percentage", cc["context_pct"]) or 0
                    cu = cw.get("current_usage") or {}
                    cc["cache_read"] = cu.get("cache_read_input_tokens", cc["cache_read"])
                    cc["cache_write"] = cu.get("cache_creation_input_tokens", cc["cache_write"])
                    m = data.get("model", {})
                    cc["model"] = m.get("display_name", cc["model"]) or cc["model"]
                    cc["model_id"] = m.get("id", cc.get("model_id", "")) or cc.get("model_id", "")
                    cc["style"] = (data.get("output_style", {}).get("name") or cc["style"])
                    cc["session_id"] = data.get("session_id", cc["session_id"])
                    cc["version"] = data.get("version", cc["version"])
                    cc["compactions"] = data.get("exceeds_200k_tokens", False)
                    # Read display name from sidecar file or session data
                    try:
                        name_file = BASE / "cc-display-name.txt"
                        if name_file.exists():
                            cc["display_name"] = name_file.read_text(encoding="utf-8").strip() or "CLAUDE MAIN"
                        elif data.get("name"):
                            cc["display_name"] = data["name"]
                    except Exception:
                        pass
                    # Detect CC mode from session data
                    mode = "INTERACTIVE"
                    if data.get("plan_mode") or data.get("planMode"):
                        mode = "PLAN MODE"
                    elif data.get("vim", {}).get("mode"):
                        mode = f"VIM:{data['vim']['mode'].upper()}"
                    elif state.subagents and any(s.get("status") == "run" for s in state.subagents):
                        mode = "AGENT"
                    state.cc_mode = mode
        except Exception as ex:
            log.debug(f"CC session read failed: {ex}")
        shutdown_event.wait(1)


def poll_local_hw():
    """Poll local hardware metrics via psutil every 2s."""
    while not shutdown_event.is_set():
        try:
            cpu = psutil.cpu_percent(interval=1)
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage("C:\\")
            boot = datetime.fromtimestamp(psutil.boot_time())
            uptime = datetime.now() - boot
            days = uptime.days
            hours = uptime.seconds // 3600
            mins = (uptime.seconds % 3600) // 60
            net_up = any(
                stats.isup for stats in psutil.net_if_stats().values()
                if stats.isup
            )
            with state.lock:
                hw = state.local_hw
                hw["cpu_pct"] = int(cpu)
                hw["ram_used_gb"] = round(mem.used / (1024 ** 3), 1)
                hw["ram_total_gb"] = round(mem.total / (1024 ** 3), 1)
                hw["disk_used_gb"] = round(disk.used / (1024 ** 3), 0)
                hw["disk_total_gb"] = round(disk.total / (1024 ** 3), 0)
                hw["uptime_str"] = f"{days}d {hours}h {mins}m"
                hw["net_up"] = net_up
        except Exception as ex:
            log.debug(f"Local HW poll failed: {ex}")
        shutdown_event.wait(2)


# ═══════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════

def fmt_bytes(b):
    if b >= 1_000_000_000:
        return f"{b / 1_000_000_000:.1f}GB"
    if b >= 1_000_000:
        return f"{b / 1_000_000:.1f}MB"
    if b >= 1_000:
        return f"{b / 1_000:.0f}KB"
    return f"{b}B"


def fmt_tokens(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def node_status_age(node_data):
    if node_data["ts"] == 0:
        return "unknown"
    age = time.time() - node_data["ts"]
    if age < 60:
        return "online"
    if age < 120:
        return "stale"
    return "offline"


def detect_alerts():
    """Detect alert conditions and update state.alerts."""
    alerts = []
    with state.lock:
        # Context near limit
        if state.cc["context_pct"] > 90:
            alerts.append("CTX: Compaction imminent")
        # Node offline
        for name, nd in state.nodes.items():
            st = node_status_age(nd)
            nd["status"] = st if nd["ts"] > 0 else nd["status"]
            if st == "offline" and name != "devbox":
                alerts.append(f"NODE: {name} offline")
        # MQTT disconnected
        if not state.mqtt_connected:
            alerts.append("MQTT: Disconnected")
        # Hardware temps
        for name, temp in state.hardware_temps.items():
            try:
                t = float(temp)
                if t > 80:
                    alerts.append(f"TEMP: {name} {t:.0f}°C")
            except (ValueError, TypeError):
                pass
        # Loop detection: 3+ identical tools in recent
        tools = [t["name"] for t in state.recent_tools]
        if len(tools) >= 3 and len(set(tools[-3:])) == 1:
            alerts.append(f"LOOP: 3x {tools[-1]}")
        state.alerts.clear()
        for a in alerts[:3]:
            state.alerts.append(a)


# ═══════════════════════════════════════════════════════════════════════
# ANSI RENDERING
# ═══════════════════════════════════════════════════════════════════════

def fg(c):
    return f"{ESC}[38;2;{c}m"

def bg(c):
    return f"{ESC}[48;2;{c}m"

def rst():
    return f"{ESC}[0m"

def bold():
    return f"{ESC}[1m"

# ── Heavy-border helpers (grid) ──
def bdr():
    return f"{fg(C_BORDER)}┃{rst()}"

def divider():
    return f"{fg(C_BORDER)}┣{'━' * BW}┫{rst()}"

def frow(ansi_text, vis_len):
    pad = max(0, BW - vis_len)
    return f"{bdr()}{ansi_text}{' ' * pad}{bdr()}"

# ── Light-border helpers (header boxes) ──
def hbdr():
    return f"{fg(C_BORDER)}│{rst()}"

def hbox_top(label):
    tag = f"[ {label} ]"
    fill = max(0, BW - 2 - len(tag))
    return f"{fg(C_BORDER)}┌──{tag}{'─' * fill}┐{rst()}"

def hbox_bot():
    return f"{fg(C_BORDER)}└{'─' * BW}┘{rst()}"

def hbox_row(ansi_text, vis_len):
    pad = max(0, BW - vis_len)
    return f"{hbdr()}{ansi_text}{' ' * pad}{hbdr()}"

# ── Mini bar (solid colored blocks) ──
def mini_bar(pct, w):
    pct = max(0, min(100, pct))
    filled = round(pct / 100 * w)
    if pct > 85:
        c = C_RED
    elif pct > 65:
        c = C_ORANGE
    elif pct > 40:
        c = C_TEAL
    else:
        c = C_GREEN
    return f"{bg(c)}{' ' * filled}{bg('40;40;60')}{' ' * (w - filled)}{rst()}"

def temp_bar(temp_c, w=6):
    pct = max(0, min(100, temp_c))
    filled = round(pct / 100 * w)
    c = C_GREEN if temp_c < 60 else C_ORANGE if temp_c < 80 else C_RED
    return f"{bg(c)}{' ' * filled}{bg('40;40;60')}{' ' * (w - filled)}{rst()}"

def hatched_bar(pct, w):
    """Hatched bar with gradient background colors."""
    pct = max(0, min(100, pct))
    filled = round(pct / 100 * w)
    r = ""
    for i in range(w):
        if i < filled:
            ratio = i / max(1, w - 1)
            if ratio < 0.45:
                c = C_GREEN
            elif ratio < 0.65:
                c = C_TEAL
            elif ratio < 0.82:
                c = C_ORANGE
            else:
                c = C_RED
            r += f"{bg(c)}{fg('0;0;0')}░"
        else:
            r += f"{bg('40;40;60')}{fg('60;60;80')}░"
    return r + rst()

def gradient_bar(pct, w):
    """Gradient ▰/▱ bar — green→teal→orange→red."""
    pct = max(0, min(100, pct))
    filled = round(pct / 100 * w)
    r = ""
    for i in range(w):
        if i < filled:
            ratio = i / max(1, w - 1)
            if ratio < 0.45:
                c = C_GREEN
            elif ratio < 0.65:
                c = C_TEAL
            elif ratio < 0.82:
                c = C_ORANGE
            else:
                c = C_RED
            r += f"{fg(c)}▰"
        else:
            r += f"{fg(C_DIM)}▱"
    return r + rst()

def sparkline(values, width=10):
    chars = " ▁▂▃▅▆▇█"
    if not values:
        return " " * width
    vals = list(values)[-width:]
    mx = max(vals) if vals else 1
    mx = mx or 1
    result = ""
    for v in vals:
        idx = min(7, int(v / mx * 7))
        result += chars[idx]
    return result.ljust(width)


def grid_top():
    return f"{fg(C_BORDER)}┏{'━' * LW}┯{'━' * RW}┓{rst()}"

def grid_join():
    return f"{fg(C_BORDER)}┣{'━' * LW}┿{'━' * RW}┫{rst()}"

def grid_bot():
    return f"{fg(C_BORDER)}┗{'━' * LW}┷{'━' * RW}┛{rst()}"

def sec_hdr(left, right):
    lp = left.ljust(LW)
    rp = right.ljust(RW)
    return f"{bdr()}{bg(BG_HDR)}{fg(C_WHITE)}{bold()}{lp}{fg(C_DIV)}│{fg(C_WHITE)}{bold()}{rp}{rst()}{bdr()}"

def mrow(la, lv, ra, rv):
    lpad = max(0, LW - lv)
    rpad = max(0, RW - rv)
    return f"{bdr()}{la}{' ' * lpad}{fg(C_DIV)}│{rst()}{ra}{' ' * rpad}{bdr()}"


def render_dashboard():
    """Build SeanDex Mission Control ANSI dashboard from current state."""
    now = time.time()
    lines = []
    with state.lock:
        cc = state.cc
        hw = state.local_hw
        model = full_model_name(cc.get("model_id", ""), cc.get("model", "opus-4-6"))
        style = cc.get("style", "Default")
        cost_str = "SUB" if cc.get("on_subscription") else f"${cc['cost']:.2f}"
        tin = fmt_tokens(cc["tokens_in"])
        tout = fmt_tokens(cc["tokens_out"])
        ctx_pct = int(cc["context_pct"])
        mode = state.cc_mode

        # ══════════════════════════════════════════════════════
        # HEADER BOX 1: CLAUDE MAIN : CURRENT FOCUS
        # ══════════════════════════════════════════════════════
        mode_tag = f" : {mode}" if mode != "INTERACTIVE" else ""
        display_name = cc.get("display_name", "CLAUDE MAIN")
        lines.append(hbox_top(f"{display_name} : CURRENT FOCUS{mode_tag}"))
        tool = cc.get("current_tool") or "Idle"
        desc = tool[:BW - 4]
        lines.append(hbox_row(f" {fg(C_WHITE)}> {desc}{rst()}", 3 + len(desc)))
        ver = cc.get("version") or ""
        ver_tag = f" v{ver}" if ver else ""
        ml = f"> {model}{ver_tag} | {style}"
        ml = ml[:BW - 2]
        lines.append(hbox_row(f" {fg(C_GRAY)}{ml}{rst()}", 1 + len(ml)))
        lines.append(hbox_bot())

        # ── AGENT ZERO : ORCHESTRATOR (single line) ──
        lines.append(hbox_top("AGENT ZERO : ORCHESTRATOR"))
        a0_msg = state.a0_message or "Awaiting orchestrator directive."
        # Strip non-ASCII to prevent emoji/unicode width miscalculation
        a0_clean = ''.join(c for c in a0_msg if ord(c) < 128 and c != '\n' and c != '\r')
        a0_short = a0_clean[:BW - 2]
        lines.append(hbox_row(f" {fg(C_GRAY)}{a0_short}{rst()}", 1 + len(a0_short)))
        lines.append(hbox_bot())

        # ══════════════════════════════════════════════════════
        # GRID: 2-column layout
        # ══════════════════════════════════════════════════════
        lines.append(grid_top())

        # ── TOKEN ECONOMY | CONTEXT WINDOW ──
        lines.append(sec_hdr("TOKEN ECONOMY", "CONTEXT WINDOW"))
        # Row 1: COST | CTX% + bar
        l = f"{fg(C_GREEN)} COST: {cost_str}{rst()}"
        lv = 7 + len(cost_str)
        ctx_c = C_RED if ctx_pct > 85 else C_ORANGE if ctx_pct > 65 else C_CYAN
        mb = mini_bar(ctx_pct, 10)
        r = f" {fg(ctx_c)}CTX: {ctx_pct}%{rst()} {mb}"
        rv = 6 + len(str(ctx_pct)) + 2 + 10
        lines.append(mrow(l, lv, r, rv))
        # Row 2: IN/OUT | Warn/OK
        l = f"{fg(C_GRAY)} IN: {tin} | OUT: {tout}{rst()}"
        lv = 5 + len(tin) + 8 + len(tout)
        if ctx_pct > 85:
            warn = f"{fg(C_RED)} WARN: Compaction imminent{rst()}"
            wv = 26
        elif ctx_pct > 65:
            warn = f"{fg(C_ORANGE)} Approaching limit{rst()}"
            wv = 19
        else:
            warn = f"{fg(C_GREEN)} OK{rst()}"
            wv = 3
        lines.append(mrow(l, lv, warn, wv))
        # Row 3: CACHE | Compactions
        cr = fmt_tokens(cc["cache_read"])
        cw_t = fmt_tokens(cc["cache_write"])
        l = f"{fg(C_GRAY)} CACHE R: {cr} | W: {cw_t}{rst()}"
        lv = 10 + len(cr) + 6 + len(cw_t)
        comp = "Yes" if cc["compactions"] else "0"
        r = f"{fg(C_GRAY)} Compactions: {comp}{rst()}"
        rv = 14 + len(comp)
        lines.append(mrow(l, lv, r, rv))
        # Row 4: LATENCY | STYLE
        avg_lat = int(sum(state.latency_history) / max(1, len(state.latency_history))) if state.latency_history else 0
        lat_s = f"{avg_lat}ms" if avg_lat < 10000 else f"{avg_lat // 1000}s"
        lat_spark = sparkline(state.latency_history, 8)
        l = f"{fg(C_GRAY)} LATENCY:{fg(C_PURPLE)} {lat_spark} {fg(C_DIM)}{lat_s}{rst()}"
        lv = 9 + 1 + 8 + 1 + len(lat_s)
        r = f"{fg(C_GRAY)} STYLE: {style}{rst()}"
        rv = 8 + len(style)
        lines.append(mrow(l, lv, r, rv))

        # ── MACHINES | CC INSTANCES ──
        lines.append(grid_join())
        lines.append(sec_hdr("MACHINES", "CC INSTANCES"))
        node_map = [("SW", "seanwhite"), ("LG", "legion"), ("NB", "lenovom82a"), ("DV", "devbox")]
        temp_map = {k: v for k, v in state.hardware_temps.items()}
        mq_status = "CON" if state.mqtt_connected else "DIS"
        mq_c = C_GREEN if state.mqtt_connected else C_RED
        for i, (short, key) in enumerate(node_map):
            nd = state.nodes.get(key, {"status": "unknown", "ts": 0})
            ns = node_status_age(nd) if nd["ts"] > 0 else nd["status"]
            nc = C_GREEN if ns == "online" else C_RED if ns == "offline" else C_ORANGE
            dot = f"{fg(nc)}●{rst()}"
            cpu = nd.get("cpu", 0)
            ram = nd.get("ram_pct", 0)
            # Get temp from HA
            temp_str = ""
            temp_v = 0
            for tname, tval in temp_map.items():
                if key.startswith(tname) or tname.startswith(key[:4]):
                    try:
                        tv = int(float(tval))
                        tc = C_RED if tv > 80 else C_ORANGE if tv > 65 else C_GREEN
                        temp_str = f" {fg(tc)}{tv}°"
                        temp_v = 1 + len(str(tv)) + 1
                    except (ValueError, TypeError):
                        pass
                    break
            # CPU/RAM
            if ns == "online" and (cpu or ram):
                cpu_c = C_RED if cpu > 80 else C_ORANGE if cpu > 60 else C_GREEN
                ram_c = C_RED if ram > 80 else C_ORANGE if ram > 60 else C_GREEN
                hw_str = f" {fg(cpu_c)}{cpu}%{fg(C_DIM)}c {fg(ram_c)}{ram}%{fg(C_DIM)}m"
                hw_v = 1 + len(str(cpu)) + 3 + len(str(ram)) + 2
            else:
                hw_str = ""
                hw_v = 0
            l = f" {fg(C_WHITE)}{short}{rst()} {dot}{temp_str}{hw_str}{rst()}"
            lv = 1 + len(short) + 2 + temp_v + hw_v
            # Right: CC instance info from per-machine tracking
            cc_m = state.cc_per_machine.get(key, {})
            cc_tool = cc_m.get("tool", "")
            cc_desc = cc_m.get("desc", "")
            cc_evt = cc_m.get("event", "")
            cc_ts = cc_m.get("ts", 0)
            cc_age = now - cc_ts if cc_ts > 0 else 999999
            if key == "seanwhite":
                # Local CC — show current tool + cost + context
                tool = cc.get("current_tool") or "idle"
                ctx = f"{ctx_pct}%"
                r = f" {fg(C_CYAN)}{tool[:12]}{fg(C_DIM)} {ctx} {cost_str}{rst()}"
                rv = 1 + min(12, len(tool)) + 1 + len(ctx) + 1 + len(cost_str)
            else:
                # Remote machine — detect live CC via new session + daemon-status scheme
                sessions = cc_m.get("sessions", {})
                live = [s for s, inf in sessions.items()
                        if not s.startswith("ccd") and (now - inf.get("ts", 0)) < 600]
                st = cc_m.get("status", "")
                st_fresh = (now - cc_m.get("status_ts", 0)) < 300 and st in ("idle", "busy", "online")
                if ns == "online" and live:
                    stat = st if st_fresh else ""
                    label = (f"{len(live)} CC {stat}").strip()[:RW - 2]
                    col = C_ORANGE if stat == "busy" else C_CYAN
                    r = f" {fg(col)}{label}{rst()}"
                    rv = 1 + len(label)
                elif ns == "online" and st_fresh:
                    label = (f"CC {st}")[:RW - 2]
                    r = f" {fg(C_CYAN)}{label}{rst()}"
                    rv = 1 + len(label)
                elif cc_age < 120:
                    # Active CC (legacy activity hook)
                    label = cc_desc[:RW - 2] if cc_desc else cc_tool[:RW - 2] if cc_tool else cc_evt[:RW - 2]
                    r = f" {fg(C_CYAN)}{label}{rst()}"
                    rv = 1 + len(label)
                elif cc_age < 600:
                    # Stale — last seen but quiet
                    label = cc_desc[:RW - 8] if cc_desc else cc_tool[:RW - 8] if cc_tool else "idle"
                    mins = int(cc_age // 60)
                    r = f" {fg(C_DIM)}{label} {mins}m ago{rst()}"
                    rv = 1 + len(label) + 1 + len(str(mins)) + 5
                else:
                    r = f" {fg(C_DIM)}(no CC){rst()}"
                    rv = 8
            lines.append(mrow(l, lv, r, rv))
        # MQTT status row
        mi = state.mqtt_msgs_in
        mq_label = f"MQTT:{fg(mq_c)}{mq_status}{rst()}"
        l = f" {fg(C_GRAY)}{mq_label} {fg(C_DIM)}msgs:{mi}{rst()}"
        lv = 6 + len(mq_status) + 6 + len(str(mi))
        subs = list(state.subagents)
        if subs:
            running = sum(1 for s in subs if s["status"] == "run")
            r = f" {fg(C_MINT)}Agents: {running} running{rst()}"
            rv = 9 + len(str(running)) + 8
        else:
            r = f" {fg(C_DIM)}Agents: 0{rst()}"
            rv = 10
        lines.append(mrow(l, lv, r, rv))

        # ── A0 TASK QUEUE | ALERTS ──
        lines.append(grid_join())
        lines.append(sec_hdr("A0 TASK QUEUE", "ALERTS"))
        tasks = list(state.a0_tasks)
        alert_list = list(state.alerts)
        rows_needed = max(1, max(len(tasks), len(alert_list)))
        for i in range(min(rows_needed, 3)):
            # Left: task
            if i < len(tasks):
                t = tasks[-(i + 1)]
                tc = C_GREEN if t["status"] == "done" else C_ORANGE if t["status"] == "run" else C_DIM
                tgt = t['target'][:10]
                l = f"{fg(C_DIM)} > {t['id']} → {fg(C_WHITE)}{tgt} {fg(tc)}({t['status']}){rst()}"
                lv = 3 + len(t['id']) + 3 + len(tgt) + 1 + len(t['status']) + 2
            elif i == 0 and not tasks:
                l = f"{fg(C_DIM)} (no tasks){rst()}"
                lv = 11
            else:
                l = ""
                lv = 0
            # Right: alert
            if i < len(alert_list):
                a = str(alert_list[i])[:RW - 4]
                r = f" {fg(C_RED)}! {fg(C_WHITE)}{a}{rst()}"
                rv = 3 + len(a)
            elif i == 0 and not alert_list:
                r = f"{fg(C_GREEN)} (none){rst()}"
                rv = 7
            else:
                r = ""
                rv = 0
            lines.append(mrow(l, lv, r, rv))

        lines.append(grid_bot())

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
# RENDER LOOP
# ═══════════════════════════════════════════════════════════════════════

def render_loop():
    """Render dashboard every 500ms and write atomically."""
    while not shutdown_event.is_set():
        try:
            detect_alerts()
            ansi = render_dashboard()
            ANSI_TMP.write_text(ansi, encoding="utf-8")
            # Retry rename up to 3 times — Windows can deny access when
            # a CC statusline process has the target file open for reading.
            for attempt in range(3):
                try:
                    os.replace(str(ANSI_TMP), str(ANSI_FILE))
                    break
                except PermissionError:
                    if attempt < 2:
                        import time; time.sleep(0.05)
                    else:
                        # Fall back to direct overwrite (not atomic but won't fail)
                        ANSI_FILE.write_text(ansi, encoding="utf-8")
                        try:
                            ANSI_TMP.unlink(missing_ok=True)
                        except OSError:
                            pass
        except Exception as ex:
            log.error(f"Render failed: {ex}")
        shutdown_event.wait(0.5)


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

def main():
    log.info("Dashboard relay starting...")

    # MQTT — retry initial connection until broker is reachable
    client = setup_mqtt()
    connected = False
    while not connected and not shutdown_event.is_set():
        try:
            client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            connected = True
        except Exception as ex:
            log.error(f"MQTT connect failed ({MQTT_HOST}:{MQTT_PORT}): {ex} — retrying in 5s")
            shutdown_event.wait(5)
    client.loop_start()

    # Background threads
    threads = [
        threading.Thread(target=poll_home_assistant, name="ha-poller", daemon=True),
        threading.Thread(target=poll_syncthing, name="st-poller", daemon=True),
        threading.Thread(target=read_cc_session, name="cc-reader", daemon=True),
        threading.Thread(target=poll_local_hw, name="hw-poller", daemon=True),
        threading.Thread(target=render_loop, name="renderer", daemon=True),
    ]
    for t in threads:
        t.start()

    log.info("All threads started. Dashboard relay running.")

    # Wait for shutdown signal
    def handle_signal(signum, frame):
        log.info(f"Signal {signum} received, shutting down...")
        shutdown_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        while not shutdown_event.is_set():
            shutdown_event.wait(1)
    except KeyboardInterrupt:
        shutdown_event.set()

    log.info("Shutting down...")
    client.loop_stop()
    client.disconnect()
    log.info("Dashboard relay stopped.")


if __name__ == "__main__":
    main()
