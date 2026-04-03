#!/usr/bin/env python3
"""
nexuschat_cli.py — Interactive terminal client for NexusChat distributed backend.

Usage:
    python nexuschat_cli.py [--host HOST] [--port PORT]

Controls:
    m <text>          Send a message (as current user)
    c1 / c2 / c3      Crash node 1 / 2 / 3
    r1 / r2 / r3      Recover node 1 / 2 / 3
    u <name>          Switch current user (Supun / Ruchira / Sachith / Sasiru)
    sync              Run Berkeley clock synchronisation
    status            Show cluster status
    msgs              Show latest messages
    logs [n]          Show last n system log entries (default 10)
    watch             Toggle live auto-refresh (every 2s)
    clear             Clear terminal screen
    help              Show this help
    quit / exit / q   Exit
"""

import sys
import time
import threading
import argparse
import shutil
import textwrap
import os

try:
    import requests
except ImportError:
    print("ERROR: 'requests' library not found. Install it with:  pip install requests")
    sys.exit(1)

# ── ANSI colour helpers ──────────────────────────────────────────────────────

def _c(code, text):
    return f"\033[{code}m{text}\033[0m"

def green(t):   return _c("32", t)
def red(t):     return _c("31", t)
def yellow(t):  return _c("33", t)
def cyan(t):    return _c("36", t)
def magenta(t): return _c("35", t)
def blue(t):    return _c("34", t)
def bold(t):    return _c("1",  t)
def dim(t):     return _c("2",  t)
def orange(t):  return _c("38;5;208", t)
def white(t):   return _c("97", t)

LOG_TYPE_COLORS = {
    "boot":      green,
    "store":     blue,
    "crash":     red,
    "failover":  yellow,
    "recovery":  magenta,
    "error":     red,
    "heartbeat": green,
    "leader":    yellow,
    "sync":      cyan,
    "consensus": magenta,
    "time":      cyan,
    "pending":   orange,
    "commit":    green,
}

TERM_WIDTH = min(shutil.get_terminal_size((100, 40)).columns, 120)


def _line(char="─"):
    return dim(char * TERM_WIDTH)


def _hdr(title):
    pad  = (TERM_WIDTH - len(title) - 4) // 2
    return dim("─" * pad) + "  " + bold(white(title)) + "  " + dim("─" * pad)


def _clear():
    os.system("cls" if os.name == "nt" else "clear")


# ── HTTP helpers ─────────────────────────────────────────────────────────────

class APIClient:
    def __init__(self, base_url):
        self.base = base_url.rstrip("/")

    def _get(self, path):
        try:
            r = requests.get(self.base + path, timeout=4)
            r.raise_for_status()
            return r.json()
        except requests.ConnectionError:
            return {"_error": "Connection refused — is the backend running?"}
        except Exception as e:
            return {"_error": str(e)}

    def _post(self, path, payload=None):
        try:
            r = requests.post(self.base + path, json=payload or {}, timeout=6)
            return r.status_code, r.json()
        except requests.ConnectionError:
            return 503, {"_error": "Connection refused — is the backend running?"}
        except Exception as e:
            return 500, {"_error": str(e)}

    def status(self):      return self._get("/api/status")
    def messages(self):    return self._get("/api/messages")
    def logs(self):        return self._get("/api/logs")
    def time_report(self): return self._get("/api/time/report")
    def time_sync(self):   return self._post("/api/time/sync")

    def crash(self, node_id):   return self._post(f"/api/servers/{node_id}/crash")
    def recover(self, node_id): return self._post(f"/api/servers/{node_id}/recover")

    def send(self, sender, content):
        return self._post("/api/messages", {"sender": sender, "content": content})


# ── Display functions ─────────────────────────────────────────────────────────

def fmt_status(data):
    if "_error" in data:
        return red("  ✗ " + data["_error"])

    servers       = data.get("servers", [])
    leader        = data.get("leader")
    current_term  = data.get("currentTerm", 0)
    has_quorum    = data.get("hasQuorum", True)
    pending_count = data.get("pendingCount", 0)
    alive_count   = sum(1 for s in servers if s.get("alive"))

    lines = []
    lines.append(_hdr("CLUSTER STATUS"))

    # Quorum / leader line
    if has_quorum:
        q_str = green(f"✓ QUORUM  ({alive_count}/{len(servers)} nodes alive)")
    else:
        q_str = red(f"✗ NO QUORUM  ({alive_count}/{len(servers)} nodes alive — majority down)")

    lines.append(f"  {q_str}   {dim('Term:')} {yellow(str(current_term))}   {dim('Leader:')} {(cyan(leader) if leader else dim('none'))}")

    if pending_count > 0:
        lines.append(f"  {orange(f'⏳ {pending_count} message(s) in pending queue (waiting for quorum)')}")

    lines.append(_line())

    # Node table header
    col_w = [10, 10, 10, 8, 8, 8]
    hdr   = f"  {'NODE':<{col_w[0]}} {'REGION':<{col_w[1]}} {'STATUS':<{col_w[2]}} {'ROLE':<{col_w[3]}} {'TERM':<{col_w[4]}} {'MSGS':<{col_w[5]}}"
    lines.append(bold(dim(hdr)))
    lines.append(_line("·"))

    for s in servers:
        name   = s.get("name", "?")
        region = s.get("region", "?")
        alive  = s.get("alive", False)
        role   = s.get("role") or "unknown"
        term   = s.get("term", 0)
        msgs   = len(s.get("messages", []))
        is_lead = name == leader

        status_str = green("● Online ") if alive else red("✗ Crashed")

        role_fmt = {
            "leader":    yellow("leader   "),
            "follower":  blue("follower "),
            "candidate": cyan("candidate"),
        }.get(role, dim("unknown  "))

        name_fmt = (bold(yellow(f"👑 {name}")) if is_lead and alive else bold(name)) if alive else dim(name)
        row = f"  {name_fmt:<{col_w[0]+10}} {region:<{col_w[1]}} {status_str}  {role_fmt}  {str(term):<{col_w[4]}} {msgs}"
        lines.append(row)

    lines.append(_line())
    return "\n".join(lines)


def fmt_messages(data, limit=10, current_user=None):
    if "_error" in data:
        return red("  ✗ " + data["_error"])
    if not data:
        return dim("  (no messages yet)")

    # Only show committed messages
    committed = [m for m in data if m.get("status") != "pending"][-limit:]
    lines = [_hdr(f"MESSAGES (last {len(committed)})")]

    for msg in committed:
        sender  = msg.get("sender", "?")
        content = msg.get("content", "")
        ts      = msg.get("time", "??:??:??")
        node    = msg.get("server", "?")
        is_me   = sender == current_user

        name_col = bold(cyan(f"{sender:<10}")) if is_me else bold(f"{sender:<10}")
        meta     = dim(f"[{ts} · {node}]")
        wrapped  = textwrap.fill(content, width=TERM_WIDTH - 26, subsequent_indent=" " * 26)
        arrow    = "→" if is_me else " "
        lines.append(f"  {arrow} {name_col} {wrapped}  {meta}")

    lines.append(_line())
    return "\n".join(lines)


def fmt_logs(data, limit=10):
    if "_error" in data:
        return red("  ✗ " + data["_error"])
    if not data:
        return dim("  (no log entries yet)")

    recent = data[-limit:]
    lines  = [_hdr(f"SYSTEM LOG (last {len(recent)})")]
    for entry in recent:
        typ  = entry.get("type", "info")
        text = entry.get("text", "")
        ts   = entry.get("time", "")
        col  = LOG_TYPE_COLORS.get(typ, dim)
        tag  = col(f"[{typ.upper():<10}]")
        lines.append(f"  {dim(ts)}  {tag}  {text}")
    lines.append(_line())
    return "\n".join(lines)


def fmt_send_result(status_code, data, content, sender):
    if "_error" in data:
        return red(f"  ✗ {data['_error']}")

    if status_code == 201:
        msg_id   = data.get("id", "?")
        node     = data.get("server", "?")
        term     = data.get("term", "?")
        idx      = data.get("index", "?")
        replicas = ", ".join(data.get("replicas", []))
        lines = [
            green(f"  ✓ Message committed successfully"),
            f"    {dim('ID:')}      {msg_id}",
            f"    {dim('Leader:')}  {cyan(node)}",
            f"    {dim('Term:')}    {yellow(str(term))}   {dim('Index:')} {str(idx)}",
            f"    {dim('Replicas:')} {green(replicas)}",
        ]
        return "\n".join(lines)

    if status_code == 202:
        stored_on = ", ".join(data.get("stored_on", []))
        lines = [
            orange(f"  ⏳ No quorum — message queued as PENDING"),
            f"    {dim('Content:')} \"{content}\"",
            f"    {dim('Stored on:')} {stored_on}  (not committed — waiting for majority)",
            f"    Will auto-commit when {bold(str(data.get('need', '?')))} nodes are alive.",
        ]
        return "\n".join(lines)

    error = data.get("error", "unknown")
    return red(f"  ✗ Send failed ({status_code}): {error}")


# ── Help text ─────────────────────────────────────────────────────────────────

HELP_TEXT = f"""
{_hdr("NEXUSCHAT CLI COMMANDS")}

  {bold(cyan('MESSAGING'))}
    {bold('m <text>')}           Send a message as the current user
                        e.g.  m Hello from the terminal!

  {bold(cyan('NODE CONTROL'))}
    {bold('c1')} / {bold('c2')} / {bold('c3')}       Crash node 1 / 2 / 3
    {bold('r1')} / {bold('r2')} / {bold('r3')}       Recover node 1 / 2 / 3

  {bold(cyan('USER'))}
    {bold('u <name>')}           Switch active user
                        Users: Supun, Ruchira, Sachith, Sasiru

  {bold(cyan('CLUSTER INFO'))}
    {bold('status')}             Show node status, leader, quorum
    {bold('msgs')} [n]           Show last n committed messages (default 10)
    {bold('logs')} [n]           Show last n system log entries (default 10)
    {bold('sync')}               Run Berkeley clock synchronisation

  {bold(cyan('DISPLAY'))}
    {bold('watch')}              Toggle auto-refresh every 2 seconds
    {bold('clear')}              Clear the screen
    {bold('help')}               Show this help

  {bold(cyan('EXIT'))}
    {bold('quit')} / {bold('q')} / {bold('exit')}   Exit the CLI

{_line()}
"""


# ── Main REPL ─────────────────────────────────────────────────────────────────

USERS = ["Supun", "Ruchira", "Sachith", "Sasiru"]


class NexusChatCLI:
    def __init__(self, base_url):
        self.api          = APIClient(base_url)
        self.current_user = "Supun"
        self._watch       = False
        self._watch_thread = None
        self._running     = True

    # ── Prompt ────────────────────────────────────────────────────────────────

    def _prompt(self):
        status   = self.api.status()
        quorum   = status.get("hasQuorum", True)
        leader   = status.get("leader") or "none"
        term     = status.get("currentTerm", 0)
        alive    = sum(1 for s in status.get("servers", []) if s.get("alive"))
        total    = len(status.get("servers", []))
        pending  = status.get("pendingCount", 0)

        q_mark = green("✓") if quorum else red("✗")
        pend   = orange(f" ⏳{pending}") if pending > 0 else ""

        user_col = cyan(self.current_user)
        node_col = f"{alive}/{total}"
        ldr_col  = cyan(leader)

        return (
            f"\n{_line('═')}\n"
            f"  user={bold(user_col)}  "
            f"nodes={yellow(node_col)}  "
            f"quorum={q_mark}  "
            f"leader={ldr_col}  "
            f"term={yellow(str(term))}"
            f"{pend}\n"
            f"{_line('─')}\n"
            f"  {bold(cyan('nexuschat'))} {dim('›')} "
        )

    # ── Auto-watch ────────────────────────────────────────────────────────────

    def _watch_loop(self):
        while self._watch and self._running:
            time.sleep(2)
            if self._watch:
                print("\n" + fmt_status(self.api.status()))
                print(fmt_messages(self.api.messages(), limit=5, current_user=self.current_user))
                print(f"  {bold(cyan('nexuschat'))} {dim('›')} ", end="", flush=True)

    def _toggle_watch(self):
        self._watch = not self._watch
        if self._watch:
            print(green("  ✓ Watch mode ON — refreshing every 2s. Type any command to act."))
            self._watch_thread = threading.Thread(target=self._watch_loop, daemon=True)
            self._watch_thread.start()
        else:
            print(yellow("  ○ Watch mode OFF."))

    # ── Command dispatcher ────────────────────────────────────────────────────

    def _dispatch(self, raw):
        raw = raw.strip()
        if not raw:
            return

        parts = raw.split(maxsplit=1)
        cmd   = parts[0].lower()
        arg   = parts[1].strip() if len(parts) > 1 else ""

        # SEND MESSAGE
        if cmd == "m":
            if not arg:
                print(red("  Usage: m <message text>"))
                return
            print(f"  Sending as {bold(cyan(self.current_user))}...")
            code, data = self.api.send(self.current_user, arg)
            print(fmt_send_result(code, data, arg, self.current_user))

        # CRASH NODES
        elif cmd in ("c1","c2","c3"):
            nid = int(cmd[1])
            print(f"  Crashing node {nid}...")
            code, data = self.api.crash(nid)
            if code == 200:
                print(red(f"  💥 Node-0{nid} CRASHED"))
            else:
                print(red(f"  ✗ {data.get('error', 'unknown')}"))

        # RECOVER NODES
        elif cmd in ("r1","r2","r3"):
            nid = int(cmd[1])
            print(f"  Recovering node {nid}...")
            code, data = self.api.recover(nid)
            if code == 200:
                caught = data.get("raftCaughtUp", False)
                synced = data.get("synced", 0)
                print(green(f"  ✅ Node-0{nid} RECOVERED"))
                print(f"     Raft catch-up: {green('✓') if caught else yellow('in progress')}   Messages synced: {synced}")
            else:
                print(red(f"  ✗ {data.get('error', 'unknown')}"))

        # SWITCH USER
        elif cmd == "u":
            name = arg.strip().capitalize() if arg else ""
            # Try to match case-insensitively
            match = next((u for u in USERS if u.lower() == arg.lower()), None)
            if match:
                self.current_user = match
                print(green(f"  Switched to {bold(cyan(match))}"))
            else:
                print(red(f"  Unknown user '{arg}'. Choose from: {', '.join(USERS)}"))

        # STATUS
        elif cmd == "status":
            print(fmt_status(self.api.status()))

        # MESSAGES
        elif cmd == "msgs":
            limit = int(arg) if arg.isdigit() else 10
            print(fmt_messages(self.api.messages(), limit=limit, current_user=self.current_user))

        # LOGS
        elif cmd == "logs":
            limit = int(arg) if arg.isdigit() else 10
            print(fmt_logs(self.api.logs(), limit=limit))

        # TIME SYNC
        elif cmd == "sync":
            print("  Running Berkeley clock sync...")
            code, data = self.api.time_sync()
            if "_error" in data:
                print(red(f"  ✗ {data['_error']}"))
            else:
                master = data.get("master_readable") or time.strftime("%H:%M:%S", time.localtime(data.get("master_time", 0)))
                skews  = data.get("skews", {})
                print(green(f"  ✓ Sync complete. Master time: {bold(master)}"))
                for node, skew in skews.items():
                    sign = "+" if skew > 0 else ""
                    col  = orange if skew > 0.05 else (blue if skew < -0.05 else green)
                    print(f"     {node}: {col(sign + f'{skew:.3f}s')}")

        # WATCH
        elif cmd == "watch":
            self._toggle_watch()

        # CLEAR
        elif cmd == "clear":
            _clear()

        # HELP
        elif cmd == "help":
            print(HELP_TEXT)

        # EXIT
        elif cmd in ("quit", "exit", "q"):
            self._running = False

        else:
            print(red(f"  Unknown command: '{cmd}'  — type {bold('help')} for a list of commands."))

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self):
        _clear()
        print(f"""
{_hdr("NEXUSCHAT DISTRIBUTED CLUSTER — TERMINAL CLIENT")}
  Backend: {cyan(self.api.base)}
  Type {bold(cyan('help'))} for commands  ·  {bold(cyan('status'))} to see cluster state  ·  {bold(cyan('q'))} to quit
{_line()}
""")

        # Initial status
        print(fmt_status(self.api.status()))

        while self._running:
            try:
                prompt = self._prompt()
                raw    = input(prompt)
                self._dispatch(raw)
            except (EOFError, KeyboardInterrupt):
                print(f"\n  {dim('Goodbye.')}")
                break
            except Exception as e:
                print(red(f"  ✗ Unexpected error: {e}"))

        self._running = False


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="NexusChat interactive terminal client",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--host", default="localhost", help="Backend host (default: localhost)")
    parser.add_argument("--port", default=8000, type=int, help="Backend port (default: 8000)")
    args = parser.parse_args()

    base_url = f"http://{args.host}:{args.port}"
    cli      = NexusChatCLI(base_url)
    cli.run()


if __name__ == "__main__":
    main()
