#!/usr/bin/env python3
"""
NexusChat Interactive Terminal CLI
===================================
A professional command-line interface to interact with and demonstrate
the NexusChat distributed messaging backend WITHOUT the frontend.

Usage:
    python nchat_cli.py

Controls:
    Send message  :  <your message text>  (just type and press Enter)
    Crash node    :  c1  /  c2  /  c3
    Recover node  :  r1  /  r2  /  r3
    Status        :  s   (or status)
    Messages      :  m   (or msgs)
    Logs          :  l   (or logs)
    Time sync     :  t   (or sync)
    Help          :  h   (or help)
    Quit          :  q   (or quit / exit)
"""

import sys
import os
import time
import textwrap

# ── Try to import colorama for Windows compat ─────────────────────────────────
try:
    from colorama import init as colorama_init, Fore, Style
    colorama_init()
    C = {
        "reset":   Style.RESET_ALL,
        "bold":    Style.BRIGHT,
        "green":   Fore.GREEN,
        "yellow":  Fore.YELLOW,
        "red":     Fore.RED,
        "blue":    Fore.BLUE,
        "cyan":    Fore.CYAN,
        "magenta": Fore.MAGENTA,
        "white":   Fore.WHITE,
        "grey":    Fore.WHITE + Style.DIM,
    }
except ImportError:
    # Fallback: raw ANSI codes (works on Linux/macOS terminals)
    C = {
        "reset":   "\033[0m",
        "bold":    "\033[1m",
        "green":   "\033[92m",
        "yellow":  "\033[93m",
        "red":     "\033[91m",
        "blue":    "\033[94m",
        "cyan":    "\033[96m",
        "magenta": "\033[95m",
        "white":   "\033[97m",
        "grey":    "\033[90m",
    }


def c(color, text):
    return f"{C[color]}{text}{C['reset']}"


def bold(text):
    return f"{C['bold']}{text}{C['reset']}"


# ── Import the actual backend modules ─────────────────────────────────────────
# This CLI runs the real Raft cluster in-process — no HTTP, no Flask needed.
try:
    from server import Server
    from failure_detector import FailureDetector
    from replication_manager import ReplicationManager
    from failover import FailoverManager
    from recovery_sync import RecoverySyncManager
    from time_sync import TimeSyncManager
    from raft_consensus import RaftCluster
except ImportError as e:
    print(f"\n[ERROR] Could not import backend modules: {e}")
    print("Make sure you run this script from the Distributed_Systems_Group_30/ directory.\n")
    sys.exit(1)


# ── ASCII banner ──────────────────────────────────────────────────────────────
BANNER = f"""
{C['magenta']}{C['bold']}
  ███╗   ██╗███████╗██╗  ██╗██╗   ██╗███████╗ ██████╗██╗  ██╗ █████╗ ████████╗
  ████╗  ██║██╔════╝╚██╗██╔╝██║   ██║██╔════╝██╔════╝██║  ██║██╔══██╗╚══██╔══╝
  ██╔██╗ ██║█████╗   ╚███╔╝ ██║   ██║███████╗██║     ███████║███████║   ██║   
  ██║╚██╗██║██╔══╝   ██╔██╗ ██║   ██║╚════██║██║     ██╔══██║██╔══██║   ██║   
  ██║ ╚████║███████╗██╔╝ ██╗╚██████╔╝███████║╚██████╗██║  ██║██║  ██║   ██║   
  ╚═╝  ╚═══╝╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝ ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝   ╚═╝{C['reset']}
{C['magenta']}  Distributed Messaging System — SE2062 Group 30 — Terminal Interface{C['reset']}
"""

HELP_TEXT = f"""
{bold('─── COMMANDS ───────────────────────────────────────────────────────────')}
  {c('green', 'c1')} / {c('green', 'c2')} / {c('green', 'c3')}   Crash  Node-01 / Node-02 / Node-03
  {c('cyan',  'r1')} / {c('cyan',  'r2')} / {c('cyan',  'r3')}   Recover Node-01 / Node-02 / Node-03
  {c('yellow','s')}  / status        Show cluster + Raft status
  {c('yellow','m')}  / msgs          List all committed messages
  {c('yellow','l')}  / logs          Show last 15 system log entries
  {c('yellow','t')}  / sync          Run Berkeley clock synchronisation
  {c('yellow','h')}  / help          Show this help
  {c('red',   'q')}  / quit / exit   Exit the CLI

  {bold('Send a message:')} Just type anything that is not a command, then press Enter.
  {bold('Switch user:')}    Use  {c('magenta','u1')} / {c('magenta','u2')} / {c('magenta','u3')} / {c('magenta','u4')}  to switch sender
                   (Supun / Ruchira / Sachith / Sasiru)
{bold('─────────────────────────────────────────────────────────────────────────')}
"""

USERS     = ["Supun", "Ruchira", "Sachith", "Sasiru"]
NODE_NAMES = ["Node-01", "Node-02", "Node-03"]

# Maps short keys to node index (0-based)
CRASH_CMDS   = {"c1": 0, "c2": 1, "c3": 2}
RECOVER_CMDS = {"r1": 0, "r2": 1, "r3": 2}
USER_CMDS    = {"u1": 0, "u2": 1, "u3": 2, "u4": 3}
QUIT_CMDS    = {"q", "quit", "exit"}


def divider(char="─", width=72, color="grey"):
    return c(color, char * width)


def role_color(role):
    colors = {"leader": "yellow", "follower": "blue", "candidate": "cyan"}
    return c(colors.get(role, "grey"), role or "unknown")


def alive_str(alive):
    return c("green", "● ONLINE") if alive else c("red", "✕ CRASHED")


def print_status(cluster, servers):
    cs = cluster.cluster_status()
    leader       = cs["leader"]
    current_term = cs["currentTerm"]
    has_quorum   = cs["hasQuorum"]
    pending      = cs["pendingCount"]
    alive_count  = sum(1 for s in servers if s.is_alive)

    print()
    print(divider())
    print(f"  {bold('CLUSTER STATUS')}  ·  {time.strftime('%H:%M:%S')}")
    print(divider())

    # Quorum status line
    qstr = (
        c("green",  f"✓ QUORUM ({alive_count}/{len(servers)} nodes alive)")
        if has_quorum else
        c("red",    f"✗ NO QUORUM ({alive_count}/{len(servers)} nodes alive)")
    )
    print(f"  Quorum : {qstr}")
    print(f"  Leader : {c('yellow', leader) if leader else c('red', 'none — election in progress')}")
    print(f"  Term   : {c('cyan', str(current_term))}")
    if pending > 0:
        print(f"  Queue  : {c('yellow', str(pending))} message(s) pending commit")

    print()
    print(f"  {'Node':<10}  {'Status':<14}  {'Role':<12}  {'Term':<6}  {'Log':<6}  {'Commit'}")
    print(f"  {'─'*10}  {'─'*14}  {'─'*12}  {'─'*6}  {'─'*6}  {'─'*6}")

    for s in servers:
        ns = cs["nodes"].get(s.name, {})
        print(
            f"  {bold(s.name):<10}  {alive_str(s.is_alive):<23}  "
            f"{role_color(ns.get('role')):<20}  "
            f"{c('grey', str(ns.get('term', 0))):<14}  "
            f"{c('grey', str(ns.get('log_len', 0))):<14}  "
            f"{c('grey', str(ns.get('commit_index', 0)))}"
        )

    print(divider())
    print()


def print_messages(servers, time_mgr):
    seen = {}
    for s in servers:
        for mid, data in s.message_store.items():
            if mid not in seen:
                raw_ts = data.get("timestamp", time.time())
                seen[mid] = {
                    "id":      mid,
                    "sender":  data["sender"],
                    "content": data["content"],
                    "time":    time.strftime("%H:%M:%S", time.localtime(raw_ts)),
                    "server":  s.name,
                    "index":   data.get("raft_index", "?"),
                }

    msgs = time_mgr.reorder_messages(list(seen.values()))

    print()
    print(divider())
    print(f"  {bold('COMMITTED MESSAGES')}  ({len(msgs)} total)")
    print(divider())

    if not msgs:
        print(f"  {c('grey', 'No messages yet.')}")
    else:
        for m in msgs:
            idx_str = f"#{m['index']}" if m.get("index") else ""
            server_str = f"[{m['server']}]"
            print(
                f"  {c('grey', m['time'])}  "
                f"{c('cyan', server_str):<20}  "
                f"{c('magenta', m['sender']):<12}  "
                f"{c('grey', idx_str):<8}  "
                f"{m['content']}"
            )

    print(divider())
    print()


def print_logs(logs, n=15):
    LOG_COLORS_MAP = {
        "boot": "green", "store": "blue", "crash": "red",
        "failover": "yellow", "recovery": "magenta", "error": "red",
        "heartbeat": "green", "leader": "yellow", "sync": "cyan",
        "consensus": "magenta", "pending": "yellow", "commit": "green",
    }
    recent = logs[-n:]
    print()
    print(divider())
    print(f"  {bold('SYSTEM LOG')}  (last {len(recent)} events)")
    print(divider())
    for entry in recent:
        col    = LOG_COLORS_MAP.get(entry["type"], "grey")
        tstr   = f"[{entry['type'].upper():<10}]"
        print(
            f"  {c('grey', entry['time'])}  "
            f"{c(col, tstr):<30}  "
            f"{c('white', entry['text'])}"
        )
    print(divider())
    print()


def prompt_line(user, has_quorum, alive_count, total):
    quorum_str = (
        c("green", "quorum") if has_quorum else c("red", "NO QUORUM")
    )
    user_str   = c("magenta", user)
    return (
        f"{c('grey', 'nexuschat')}@"
        f"{c('cyan', 'cluster')} "
        f"[{user_str}] "
        f"[{alive_count}/{total} · {quorum_str}] "
        f"{c('blue', '›')} "
    )


def main():
    print(BANNER)
    print(f"  {c('grey', 'Initialising backend...')}", end="", flush=True)

    # ── Boot the real backend ─────────────────────────────────────────────────
    s1 = Server("Node-01")
    s2 = Server("Node-02")
    s3 = Server("Node-03")
    all_servers = [s1, s2, s3]

    detector  = FailureDetector(all_servers, timeout_seconds=5)
    replicator = ReplicationManager(all_servers, replication_factor=3)
    failover  = FailoverManager(all_servers, detector)
    sync_mgr  = RecoverySyncManager(all_servers)
    time_mgr  = TimeSyncManager(all_servers)
    cluster   = RaftCluster(all_servers)
    cluster.start_background_processing()
    cluster.elect_leader_blocking()

    logs = []

    def _log(event_type, text):
        logs.append({
            "id":   f"ev_{len(logs)}",
            "type": event_type,
            "text": text,
            "time": time.strftime("%H:%M:%S"),
        })

    # Seed messages
    for sender, content in [
        ("Alice", "Hey team, NexusChat is live!"),
        ("Bob",   "Distributed messaging — finally!"),
    ]:
        result = cluster.append_message(sender=sender, content=content)
        if result.get("ok"):
            time_mgr.timestamp_message(result["message_id"], result["leader"])
            _log("store", f"{result['message_id']} committed by {result['leader']}")

    leader_node = cluster.get_leader()
    print(f"\r  {c('green', '✓')} Backend ready. "
          f"Leader: {c('yellow', leader_node.server.name if leader_node else 'none')}\n")

    print(HELP_TEXT)

    current_user = USERS[0]

    # ── Main REPL ─────────────────────────────────────────────────────────────
    while True:
        cs          = cluster.cluster_status()
        has_quorum  = cs["hasQuorum"]
        alive_count = sum(1 for s in all_servers if s.is_alive)

        try:
            line = input(prompt_line(current_user, has_quorum, alive_count, len(all_servers)))
        except (KeyboardInterrupt, EOFError):
            print(f"\n\n  {c('grey', 'Goodbye.')}\n")
            break

        cmd = line.strip()
        if not cmd:
            continue

        cmd_lower = cmd.lower()

        # ── Quit ──────────────────────────────────────────────────────────────
        if cmd_lower in QUIT_CMDS:
            print(f"\n  {c('grey', 'Shutting down cluster and exiting...')}\n")
            cluster.stop_background_processing()
            break

        # ── Help ──────────────────────────────────────────────────────────────
        elif cmd_lower in ("h", "help"):
            print(HELP_TEXT)

        # ── Status ────────────────────────────────────────────────────────────
        elif cmd_lower in ("s", "status"):
            cluster.tick_all()
            print_status(cluster, all_servers)

        # ── Messages ──────────────────────────────────────────────────────────
        elif cmd_lower in ("m", "msgs", "messages"):
            print_messages(all_servers, time_mgr)

        # ── Logs ──────────────────────────────────────────────────────────────
        elif cmd_lower in ("l", "logs", "log"):
            print_logs(logs)

        # ── Time sync ─────────────────────────────────────────────────────────
        elif cmd_lower in ("t", "sync", "time"):
            print(f"\n  {c('cyan', '⏱  Running Berkeley clock synchronisation...')}")
            master = time_mgr.synchronize()
            _log("sync", f"Clock sync complete. Master: {time.strftime('%H:%M:%S', time.localtime(master))}")
            print(f"  Master time: {c('cyan', time.strftime('%H:%M:%S', time.localtime(master)))}")
            skews = time_mgr.clock_sim.offsets
            for node_name, skew in skews.items():
                direction = c("yellow", "fast") if skew > 0.05 else c("blue", "slow") if skew < -0.05 else c("green", "accurate")
                print(f"  {node_name}: {skew:+.3f}s  [{direction}]")
            print()

        # ── Switch user ───────────────────────────────────────────────────────
        elif cmd_lower in USER_CMDS:
            current_user = USERS[USER_CMDS[cmd_lower]]
            print(f"\n  {c('magenta', '●')} Switched to user: {c('magenta', current_user)}\n")

        # ── Crash node ────────────────────────────────────────────────────────
        elif cmd_lower in CRASH_CMDS:
            idx  = CRASH_CMDS[cmd_lower]
            node = all_servers[idx]
            if not node.is_alive:
                print(f"\n  {c('yellow', '!')} {node.name} is already crashed.\n")
            else:
                cluster.crash_node(node.name)
                _log("crash", f"{node.name} CRASHED!")
                print(f"\n  {c('red', '💥 CRASH:')} {bold(node.name)} has been crashed.")
                time.sleep(0.3)
                cluster.tick_all()
                cs2         = cluster.cluster_status()
                alive2      = sum(1 for s in all_servers if s.is_alive)
                new_leader  = cluster.get_leader()

                if cs2["hasQuorum"] and new_leader:
                    _log("failover", f"Re-election complete. New leader: {new_leader.server.name}")
                    print(f"  {c('yellow', '🏛  Re-election:')} New leader → {c('yellow', new_leader.server.name)}")
                elif cs2["hasQuorum"]:
                    print(f"  {c('yellow', '🏛  Election in progress...')}")
                else:
                    _log("failover", f"NO QUORUM — {alive2}/{len(all_servers)} nodes alive. Messages will queue.")
                    print(f"  {c('red', '⚠  NO QUORUM:')} Only {alive2}/{len(all_servers)} nodes alive.")
                    print(f"  {c('yellow', '   New messages will be QUEUED until majority recovers.')}")
                print()

        # ── Recover node ──────────────────────────────────────────────────────
        elif cmd_lower in RECOVER_CMDS:
            idx  = RECOVER_CMDS[cmd_lower]
            node = all_servers[idx]
            if node.is_alive:
                print(f"\n  {c('yellow', '!')} {node.name} is already online.\n")
            else:
                cluster.recover_node(node.name)
                _log("recovery", f"{node.name} back ONLINE.")
                print(f"\n  {c('green', '✅ RECOVERY:')} {bold(node.name)} is back online.")
                time.sleep(0.3)
                cluster.elect_leader_blocking(rounds=5, pause=0.15)
                caught_up   = cluster.force_catch_up(node.name)
                transferred = sync_mgr.sync_server(node)

                if caught_up:
                    _log("recovery", f"{node.name} caught up to Raft leader.")
                    print(f"  {c('cyan', '📥 Catch-up:')} {node.name} synchronised with cluster log.")
                if transferred > 0:
                    _log("recovery", f"Delta sync: {transferred} message(s) restored on {node.name}.")
                    print(f"  {c('cyan', '📥 Delta sync:')} {transferred} message(s) restored.")

                cs2 = cluster.cluster_status()
                if cs2["hasQuorum"]:
                    new_leader = cluster.get_leader()
                    lead_str   = new_leader.server.name if new_leader else "electing..."
                    _log("leader", f"Quorum restored. Leader: {lead_str}")
                    print(f"  {c('green', '✓ Quorum restored!')} Leader: {c('yellow', lead_str)}")
                    if cs2["pendingCount"] == 0:
                        print(f"  {c('green', '✓ No pending messages — queue is empty.')}")
                    else:
                        pending_msg = f"⏳ {cs2['pendingCount']} pending message(s) still in queue."
                        print(f"  {c('yellow', pending_msg)}")
                print()

        # ── Send message (anything else) ──────────────────────────────────────
        else:
            content = cmd
            result  = cluster.append_message(sender=current_user, content=content)
            cluster.tick_all()

            if result.get("ok"):
                holders = cluster.get_committed_holders(result["message_id"])
                time_mgr.timestamp_message(result["message_id"], result["leader"])
                _log("store",
                     f"{result['message_id']} committed by {result['leader']} "
                     f"(term={result['term']}, index={result['log_index']})")
                print(
                    f"\n  {c('green', '✓ SENT')}  "
                    f"id={c('grey', result['message_id'])}  "
                    f"leader={c('yellow', result['leader'])}  "
                    f"term={result['term']}  "
                    f"index={result['log_index']}  "
                    f"replicas={c('cyan', str(len(holders)))}"
                )
                # Show the message in chat style
                indent = "    "
                wrapped = textwrap.fill(content, width=60, initial_indent=indent, subsequent_indent=indent)
                print(f"  {c('magenta', current_user)}:")
                print(c("white", wrapped))
                print()

            elif result.get("error") in ("no_quorum", "commit_failed", "no_leader") and result.get("queued"):
                alive_n = sum(1 for s in all_servers if s.is_alive)
                need    = (len(all_servers) // 2) + 1
                _log("pending", f"No quorum ({alive_n}/{len(all_servers)}). Message queued.")
                print(
                    f"\n  {c('yellow', '⏳ QUEUED')}  "
                    f"No quorum ({alive_n}/{len(all_servers)} nodes alive, need {need})."
                )
                indent = "    "
                wrapped = textwrap.fill(content, width=60, initial_indent=indent, subsequent_indent=indent)
                print(f"  {c('magenta', current_user)} {c('yellow', '[PENDING]')}:")
                print(c("grey", wrapped))
                print(f"  {c('grey', 'Message is held in queue. Recover a node to commit it.')}")
                print()

            else:
                _log("error", f"Send failed: {result.get('error', 'unknown')}")
                print(f"\n  {c('red', '✗ FAILED')}  {result.get('error', 'unknown error')}\n")


if __name__ == "__main__":
    main()
