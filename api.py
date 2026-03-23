from flask import Flask, jsonify, request, make_response
import time

from server import Server
from failure_detector import FailureDetector
from replication import ReplicationManager
from failover import FailoverManager
from recovery_sync import RecoverySyncManager

app = Flask(__name__)

@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"]  = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response

@app.route("/api/status",                    methods=["OPTIONS"])
@app.route("/api/messages",                  methods=["OPTIONS"])
@app.route("/api/logs",                      methods=["OPTIONS"])
@app.route("/api/servers/<int:sid>/crash",   methods=["OPTIONS"])
@app.route("/api/servers/<int:sid>/recover", methods=["OPTIONS"])
def preflight(sid=None):
    res = make_response("", 200)
    res.headers["Access-Control-Allow-Origin"]  = "*"
    res.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    res.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return res

s1 = Server("Node-01")
s2 = Server("Node-02")
s3 = Server("Node-03")
all_servers = [s1, s2, s3]

detector   = FailureDetector(all_servers, timeout_seconds=5)
replicator = ReplicationManager(all_servers, replication_factor=3)
failover   = FailoverManager(all_servers, detector)
sync_mgr   = RecoverySyncManager(all_servers)

SERVER_META = {
    "Node-01": {"region": "US-East"},
    "Node-02": {"region": "EU-West"},
    "Node-03": {"region": "AP-South"},
}

event_log = []

def _ts():
    return time.strftime('%H:%M:%S')

def _log(event_type, text):
    event_log.append({
        "id":   f"ev_{len(event_log)}_{int(time.time()*1000)}",
        "type": event_type,
        "text": text,
        "time": _ts(),
    })

for s in all_servers:
    _log("boot", f"{s.name} started. Heartbeat active.")

_id1 = replicator.replicate_message("Hey team, NexusChat is live!", sender="Alice")
if _id1:
    _log("store", f"{_id1} replicated to: {', '.join(replicator.replication_map.get(_id1, []))}")

_id2 = replicator.replicate_message("Distributed messaging — finally!", sender="Bob")
if _id2:
    _log("store", f"{_id2} replicated to: {', '.join(replicator.replication_map.get(_id2, []))}")

def _server_dict(s):
    return {
        "id":       all_servers.index(s) + 1,
        "name":     s.name,
        "alive":    s.is_alive,
        "region":   SERVER_META[s.name]["region"],
        "messages": list(s.message_store.keys()),
    }

@app.route("/api/status")
def status():
    alive = [s for s in all_servers if s.is_alive]
    return jsonify({
        "servers":    [_server_dict(s) for s in all_servers],
        "aliveCount": len(alive),
        "rf":         len(alive),
    })

@app.route("/api/messages")
def get_messages():
    seen = {}
    for s in all_servers:
        for msg_id, data in s.message_store.items():
            if msg_id not in seen:
                seen[msg_id] = {
                    "id":      msg_id,
                    "sender":  data["sender"],
                    "content": data["content"],
                    "time":    time.strftime('%H:%M:%S', time.localtime(data["timestamp"])),
                    "server":  s.name,
                }
    messages = sorted(seen.values(), key=lambda m: m["time"])
    return jsonify(messages)

@app.route("/api/messages", methods=["POST"])
def send_message():
    body    = request.get_json()
    sender  = body.get("sender", "unknown")
    content = body.get("content", "").strip()
    if not content:
        return jsonify({"error": "Empty message"}), 400
    alive = [s for s in all_servers if s.is_alive]
    if not alive:
        _log("error", "All nodes DOWN — message cannot be delivered!")
        return jsonify({"error": "All nodes are down"}), 503
    msg_id = replicator.replicate_message(content, sender=sender)
    if msg_id:
        targets = replicator.replication_map.get(msg_id, [])
        _log("store", f"{msg_id} stored. Replicated to: {', '.join(targets)}")
        _log("heartbeat", f"{alive[0].name} → primary. RF={len(alive)}")
        return jsonify({"id": msg_id, "server": alive[0].name}), 201
    else:
        _log("error", "Replication failed.")
        return jsonify({"error": "Replication failed"}), 500

@app.route("/api/servers/<int:server_id>/crash", methods=["POST"])
def crash_server(server_id):
    if server_id < 1 or server_id > len(all_servers):
        return jsonify({"error": "Invalid server id"}), 404
    s = all_servers[server_id - 1]
    if not s.is_alive:
        return jsonify({"error": f"{s.name} is already down"}), 400
    s.simulate_crash()
    _log("crash", f"{s.name} CRASHED! Failure detected via heartbeat timeout.")
    detector.check_all_servers()
    alive = [sv for sv in all_servers if sv.is_alive]
    if alive:
        _log("failover", f"Failover triggered. New primary → {alive[0].name}")
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
    s.simulate_recovery()
    _log("recovery", f"{s.name} back ONLINE. Running anti-entropy sync...")
    transferred = sync_mgr.sync_server(s)
    donors = [sv.name for sv in all_servers if sv.is_alive and sv.name != s.name]
    if transferred > 0:
        _log("recovery", f"Delta sync from {donors[0] if donors else 'donors'} complete. "
                         f"{transferred} message(s) restored. {s.name} is consistent.")
    else:
        _log("recovery", f"{s.name} was already consistent — no messages to sync.")
    s.send_heartbeat()
    detector.check_all_servers()
    return jsonify({"status": "recovered", "server": s.name, "synced": transferred})

@app.route("/api/logs")
def get_logs():
    return jsonify(event_log)

if __name__ == "__main__":
    print("\n[API] NexusChat backend running at http://localhost:5000\n")
    app.run(debug=True, host="0.0.0.0", port=8000)