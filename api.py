from flask import Flask, jsonify, make_response, request
import time

from server import Server
from failure_detector import FailureDetector
from replication_manager import ReplicationManager
from failover import FailoverManager
from recovery_sync import RecoverySyncManager
from time_sync import TimeSyncManager
from raft_consensus import RaftCluster

app = Flask(__name__)


def _ts():
    return time.strftime("%H:%M:%S")


@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


@app.route("/api/<path:dummy>", methods=["OPTIONS"])
def handle_preflight(dummy):
    res = make_response()
    res.headers["Access-Control-Allow-Origin"] = "*"
    res.headers["Access-Control-Allow-Headers"] = "Content-Type"
    res.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return res, 200


# ── Shared in-memory state ────────────────────────────────────────────────────
s1 = Server("Node-01")
s2 = Server("Node-02")
s3 = Server("Node-03")
all_servers = [s1, s2, s3]

detector = FailureDetector(all_servers, timeout_seconds=5)
replicator = ReplicationManager(all_servers, replication_factor=3)  # kept for legacy/reporting compatibility
failover = FailoverManager(all_servers, detector)
sync_mgr = RecoverySyncManager(all_servers)
time_mgr = TimeSyncManager(all_servers)
raft_cluster = RaftCluster(all_servers)
raft_cluster.start_background_processing()
raft_cluster.elect_leader_blocking()

# Region labels shown in the frontend
SERVER_META = {
    "Node-01": {"region": "US-East"},
    "Node-02": {"region": "EU-West"},
    "Node-03": {"region": "AP-South"},
}

event_log = []


def _log(event_type, text):
    event_log.append(
        {
            "id": f"ev_{len(event_log)}_{int(time.time() * 1000)}",
            "type": event_type,
            "text": text,
            "time": _ts(),
        }
    )


def _run_raft(rounds=1, pause=0.0):
    for _ in range(rounds):
        raft_cluster.tick_all()
        if pause > 0:
            time.sleep(pause)


def _record_legacy_replication_view(message_id):
    holders = raft_cluster.get_committed_holders(message_id)
    replicator.replication_map[message_id] = holders
    if holders:
        replicator.total_originals += 1
        replicator.total_copies += len(holders)


for s in all_servers:
    _log("boot", f"{s.name} started. Heartbeat active.")

_initial_leader = raft_cluster.get_leader()
if _initial_leader:
    _log("leader", f"Initial Raft leader elected: {_initial_leader.server.name}")

# Seed two messages via Raft so the UI starts with consensus-committed data.
for sender, content in [
    ("Alice", "Hey team, NexusChat is live!"),
    ("Bob", "Distributed messaging — finally!"),
]:
    result = raft_cluster.append_message(sender=sender, content=content)
    if result.get("ok"):
        _record_legacy_replication_view(result["message_id"])
        time_mgr.timestamp_message(result["message_id"], result["leader"])
        _log(
            "store",
            f"{result['message_id']} committed by {result['leader']} "
            f"(term={result['term']}, index={result['log_index']})",
        )


# ── Helper: serialise a Server object to a plain dict ─────────────────────────
def _server_dict(s):
    node_status = raft_cluster.cluster_status()["nodes"].get(s.name, {})
    return {
        "id": all_servers.index(s) + 1,
        "name": s.name,
        "alive": s.is_alive,
        "region": SERVER_META[s.name]["region"],
        "messages": list(s.message_store.keys()),
        "role": node_status.get("role"),
        "term": node_status.get("term"),
        "commitIndex": node_status.get("commit_index"),
    }


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/api/status")
def status():
    _run_raft(rounds=2)
    detector.check_all_servers()
    alive = [s for s in all_servers if s.is_alive]
    raft_status = raft_cluster.cluster_status()
    return jsonify(
        {
            "servers": [_server_dict(s) for s in all_servers],
            "aliveCount": len(alive),
            "rf": len(alive),
            "raft": raft_status,
        }
    )


@app.route("/api/messages")
def get_messages():
    _run_raft(rounds=2)
    seen = {}
    for s in all_servers:
        for msg_id, data in s.message_store.items():
            if msg_id not in seen:
                raw_ts = data.get("timestamp", time.time())
                seen[msg_id] = {
                    "id": msg_id,
                    "sender": data["sender"],
                    "content": data["content"],
                    "timestamp": raw_ts,
                    "time": time.strftime("%H:%M:%S", time.localtime(raw_ts)),
                    "server": s.name,
                    "raft_index": data.get("raft_index"),
                    "raft_term": data.get("raft_term"),
                    "committed": data.get("committed", False),
                }
    messages = time_mgr.reorder_messages(list(seen.values()))
    return jsonify(messages)


@app.route("/api/messages", methods=["POST"])
def send_message():
    body = request.get_json() or {}
    sender = body.get("sender", "unknown")
    content = body.get("content", "").strip()

    if not content:
        return jsonify({"error": "Empty message"}), 400

    alive = [s for s in all_servers if s.is_alive]
    if not alive:
        _log("error", "All nodes DOWN — message cannot be delivered!")
        return jsonify({"error": "All nodes are down"}), 503

    result = raft_cluster.append_message(sender=sender, content=content)
    _run_raft(rounds=3, pause=0.02)

    if result.get("ok"):
        _record_legacy_replication_view(result["message_id"])
        time_mgr.timestamp_message(result["message_id"], result["leader"])
        holders = raft_cluster.get_committed_holders(result["message_id"])
        _log(
            "store",
            f"{result['message_id']} committed by leader {result['leader']} "
            f"(term={result['term']}, index={result['log_index']}, replicas={holders})",
        )
        return (
            jsonify(
                {
                    "id": result["message_id"],
                    "server": result["leader"],
                    "term": result["term"],
                    "index": result["log_index"],
                    "replicas": holders,
                }
            ),
            201,
        )

    _log("error", f"Raft write failed: {result.get('error', 'unknown_error')}")
    return jsonify(result), 503


@app.route("/api/servers/<int:server_id>/crash", methods=["POST"])
def crash_server(server_id):
    if server_id < 1 or server_id > len(all_servers):
        return jsonify({"error": "Invalid server id"}), 404

    s = all_servers[server_id - 1]
    if not s.is_alive:
        return jsonify({"error": f"{s.name} is already down"}), 400

    raft_cluster.crash_node(s.name)
    _log("crash", f"{s.name} CRASHED! Failure detected via heartbeat timeout.")

    detector.check_all_servers()
    time.sleep(0.25)
    new_leader = raft_cluster.elect_leader_blocking()

    alive = [sv for sv in all_servers if sv.is_alive]
    if alive and new_leader:
        _log("failover", f"Raft re-election complete. New leader → {new_leader.server.name}")
    elif alive:
        _log("failover", "Cluster still alive but leader election is in progress.")
    else:
        _log("error", "CRITICAL: All nodes are DOWN. System unavailable.")

    return jsonify({"status": "crashed", "server": s.name})


@app.route("/api/servers/<int:server_id>/recover", methods=["POST"])
def recover_server(server_id):
    if server_id < 1 or server_id > len(all_servers):
        return jsonify({"error": "Invalid server id"}), 404

    s = all_servers[server_id - 1]
    if s.is_alive:
        return jsonify({"error": f"{s.name} is already alive"}), 400

    raft_cluster.recover_node(s.name)
    _log("recovery", f"{s.name} back ONLINE. Running Raft catch-up + anti-entropy sync...")

    raft_cluster.elect_leader_blocking()
    caught_up = raft_cluster.force_catch_up(s.name)
    transferred = sync_mgr.sync_server(s)

    s.send_heartbeat()
    detector.check_all_servers()

    if caught_up:
        _log("recovery", f"{s.name} caught up to the current Raft leader.")
    if transferred > 0:
        _log(
            "recovery",
            f"Delta sync complete. {transferred} message(s) restored on {s.name}.",
        )
    elif not caught_up:
        _log("recovery", f"{s.name} recovered but may still be syncing remaining entries.")
    else:
        _log("recovery", f"{s.name} was already consistent — no extra messages to sync.")

    return jsonify(
        {
            "status": "recovered",
            "server": s.name,
            "synced": transferred,
            "raftCaughtUp": caught_up,
        }
    )


@app.route("/api/time/sync", methods=["POST"])
def trigger_sync():
    master = time_mgr.synchronize()
    _log(
        "store",
        f"Clock sync complete. Master time: {time.strftime('%H:%M:%S', time.localtime(master))}",
    )
    return jsonify({"master_time": master, "status": "synced"})


@app.route("/api/time/report")
def time_report():
    return jsonify(
        {
            "sync_events": len(time_mgr.sync_log),
            "skews": time_mgr.clock_sim.offsets,
            "timestamped_messages": len(time_mgr.message_timestamps),
        }
    )


@app.route("/api/logs")
def get_logs():
    return jsonify(event_log)


if __name__ == "__main__":
    print("\n[API] NexusChat backend running at http://localhost:8000")
    print("[API] Endpoints:")
    print("  GET  /api/status")
    print("  GET  /api/messages")
    print("  POST /api/messages")
    print("  POST /api/servers/<id>/crash")
    print("  POST /api/servers/<id>/recover")
    print("  GET  /api/logs\n")
    app.run(debug=True, host="0.0.0.0", port=8000)