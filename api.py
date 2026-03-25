from flask import Flask, jsonify, request, make_response, make_response
import time

from server import Server
from failure_detector import FailureDetector
from replication_manager import ReplicationManager
from failover import FailoverManager
from recovery_sync import RecoverySyncManager

app = Flask(__name__)

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
# All modules are initialised once and share the same server list.

s1 = Server("Node-01")
s2 = Server("Node-02")
s3 = Server("Node-03")
all_servers = [s1, s2, s3]

detector   = FailureDetector(all_servers, timeout_seconds=5)
replicator = ReplicationManager(all_servers, replication_factor=3)
failover   = FailoverManager(all_servers, detector)
sync_mgr   = RecoverySyncManager(all_servers)

# Region labels shown in the frontend
SERVER_META = {
    "Node-01": {"region": "US-East"},
    "Node-02": {"region": "EU-West"},
    "Node-03": {"region": "AP-South"},
}

# Unified event log — appended by every operation so the frontend can show it
event_log = [
    {"id": "l1", "type": "boot", "text": "Node-01 started. Heartbeat active.", "time": _ts()} if False else None,
]
event_log = []  # reset; we'll fill on boot below

def _ts():
    return time.strftime('%H:%M:%S')

def _log(event_type, text):
    event_log.append({
        "id":   f"ev_{len(event_log)}_{int(time.time()*1000)}",
        "type": event_type,
        "text": text,
        "time": _ts(),
    })

# Boot log entries
for s in all_servers:
    _log("boot", f"{s.name} started. Heartbeat active.")

# Seed two initial messages so the chat isn't empty
_seed1 = replicator.replicate_message("Hey team, NexusChat is live!", sender="Alice")
if _seed1:
    _log("store", f"{_seed1} replicated to: {', '.join(replicator.replication_map.get(_seed1, []))}")

_seed2 = replicator.replicate_message("Distributed messaging — finally!", sender="Bob")
if _seed2:
    _log("store", f"{_seed2} replicated to: {', '.join(replicator.replication_map.get(_seed2, []))}")


# ── Helper: serialise a Server object to a plain dict ─────────────────────────

def _server_dict(s):
    return {
        "id":       all_servers.index(s) + 1,
        "name":     s.name,
        "alive":    s.is_alive,
        "region":   SERVER_META[s.name]["region"],
        "messages": list(s.message_store.keys()),
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/api/status")
def status():
    """Return current cluster state — server list, RF, alive count."""
    alive = [s for s in all_servers if s.is_alive]
    return jsonify({
        "servers":    [_server_dict(s) for s in all_servers],
        "aliveCount": len(alive),
        "rf":         len(alive),   # RF = number of alive servers (dynamic)
    })


@app.route("/api/messages")
def get_messages():
    """Return all unique messages stored across the cluster."""
    # Build a deduplicated list from all alive servers' stores
    seen = {}
    for s in all_servers:
        for msg_id, data in s.message_store.items():
            if msg_id not in seen:
                seen[msg_id] = {
                    "id":      msg_id,
                    "sender":  data["sender"],
                    "content": data["content"],
                    "time":    time.strftime('%H:%M:%S', time.localtime(data["timestamp"])),
                    "server":  s.name,   # Which server we read it from
                }
    # Sort by stored timestamp
    messages = sorted(seen.values(), key=lambda m: m["time"])
    return jsonify(messages)


@app.route("/api/messages", methods=["POST"])
def send_message():
    """
    Receive a message from the frontend, replicate it, and log the event.
    Body: { "sender": "Alice", "content": "Hello!" }
    """
    body    = request.get_json()
    sender  = body.get("sender", "unknown")
    content = body.get("content", "").strip()

    if not content:
        return jsonify({"error": "Empty message"}), 400

    alive = [s for s in all_servers if s.is_alive]
    if not alive:
        _log("error", "All nodes DOWN — message cannot be delivered!")
        return jsonify({"error": "All nodes are down"}), 503

    # Use ReplicationManager to store across alive servers
    msg_id = replicator.replicate_message(content, sender=sender)

    if msg_id:
        targets = replicator.replication_map.get(msg_id, [])
        _log("store", f"{msg_id} stored. Replicated to: {', '.join(targets)}")
        _log("heartbeat", f"{alive[0].name} → primary. RF={len(alive)}")
        return jsonify({"id": msg_id, "server": alive[0].name}), 201
    else:
        _log("error", "Replication failed — no alive servers accepted the write.")
        return jsonify({"error": "Replication failed"}), 500


@app.route("/api/servers/<int:server_id>/crash", methods=["POST"])
def crash_server(server_id):
    """Crash a server by its 1-based index."""
    if server_id < 1 or server_id > len(all_servers):
        return jsonify({"error": "Invalid server id"}), 404

    s = all_servers[server_id - 1]
    if not s.is_alive:
        return jsonify({"error": f"{s.name} is already down"}), 400

    s.simulate_crash()
    _log("crash", f"{s.name} CRASHED! Failure detected via heartbeat timeout.")

    # Run a detector check so suspected_failures set is updated
    detector.check_all_servers()

    # Log failover target if any alive server remains
    alive = [sv for sv in all_servers if sv.is_alive]
    if alive:
        _log("failover", f"Failover triggered. New primary → {alive[0].name}")
    else:
        _log("error", "CRITICAL: All nodes are DOWN. System unavailable.")

    return jsonify({"status": "crashed", "server": s.name})


@app.route("/api/servers/<int:server_id>/recover", methods=["POST"])
def recover_server(server_id):
    """Recover a crashed server, then run anti-entropy sync."""
    if server_id < 1 or server_id > len(all_servers):
        return jsonify({"error": "Invalid server id"}), 404

    s = all_servers[server_id - 1]
    if s.is_alive:
        return jsonify({"error": f"{s.name} is already alive"}), 400

    s.simulate_recovery()
    _log("recovery", f"{s.name} back ONLINE. Running anti-entropy sync...")

    # Run the actual sync — pull missed messages from donor servers
    transferred = sync_mgr.sync_server(s)

    donors = [sv.name for sv in all_servers if sv.is_alive and sv.name != s.name]
    if transferred > 0:
        _log("recovery", f"Delta sync from {donors[0] if donors else 'donors'} complete. "
                         f"{transferred} message(s) restored. {s.name} is consistent.")
    else:
        _log("recovery", f"{s.name} was already consistent — no messages to sync.")

    # Update detector so it clears this server from suspected_failures
    s.send_heartbeat()
    detector.check_all_servers()

    return jsonify({"status": "recovered", "server": s.name, "synced": transferred})


@app.route("/api/logs")
def get_logs():
    """Return all system log events."""
    return jsonify(event_log)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n[API] NexusChat backend running at http://localhost:5000")
    print("[API] Endpoints:")
    print("  GET  /api/status")
    print("  GET  /api/messages")
    print("  POST /api/messages")
    print("  POST /api/servers/<id>/crash")
    print("  POST /api/servers/<id>/recover")
    print("  GET  /api/logs\n")
    app.run(debug=True, host="0.0.0.0", port=8000)