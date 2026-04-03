# NexusChat — Backend
### Fault-Tolerant Distributed Messaging System
**SE2062 Distributed Systems · Group 30**

<div align="center">
  <img src="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/python/python-original.svg" width="48" title="Python"/>
  &nbsp;
  <img src="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/flask/flask-original.svg" width="48" title="Flask"/>
</div>

---

## Table of Contents

- [Project Overview](#project-overview)
- [Team Members](#team-members)
- [Architecture](#architecture)
- [Module Breakdown](#module-breakdown)
- [API Reference](#api-reference)
- [Getting Started](#getting-started)
- [Running the CLI Tools](#running-the-cli-tools)
- [Key Concepts](#key-concepts)
- [References](#references)

---

## Project Overview

NexusChat is a **high-availability distributed messaging system** built for the SE2062 Distributed Systems module. It simulates a real-world cluster of three nodes that replicate messages using the **Raft consensus algorithm**.

Core guarantees:
- Messages are committed only when a **majority (quorum) of nodes** confirms replication
- If a minority of nodes crash, the system stays online and continues accepting messages
- If quorum is lost, messages are **queued locally** and automatically committed once enough nodes recover
- Recovering nodes are synced via **anti-entropy** so they catch up to the current log

---

## Team Members

| # | Name | Registration | Responsibility |
|---|------|--------------|----------------|
| 01 | Supun Dharmaratne | — | Fault Tolerance — Failure Detection & Automatic Recovery |
| 02 | Ruchira Lakshan | — | Data Replication — Quorum-Based Consistency |
| 03 | Sasiru Sithujaya | — | Time Synchronisation — Berkeley Algorithm |
| 04 | Sachith Asmadala | — | Consensus & Agreement — Raft Leader Election |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Flask REST API (api.py)                  │
│  /api/status  /api/messages  /api/servers/:id/crash|recover     │
│  /api/logs    /api/time/sync   /api/time/report                 │
└───────────────────────────┬─────────────────────────────────────┘
                            │
           ┌────────────────▼────────────────┐
           │         RaftCluster             │
           │  (raft_consensus.py)            │
           │                                 │
           │  ┌──────────┐  ┌──────────┐  ┌──────────┐ │
           │  │ Node-01  │  │ Node-02  │  │ Node-03  │ │
           │  │ (Leader) │  │(Follower)│  │(Follower)│ │
           │  └────┬─────┘  └─────┬────┘  └─────┬────┘ │
           │       └──────────────┴──────────────┘       │
           │          InMemoryTransport (RPC)            │
           └─────────────────────────────────────────────┘
                            │
     ┌──────────────────────┼───────────────────────────┐
     │                      │                           │
┌────▼─────┐         ┌──────▼──────┐           ┌───────▼──────┐
│ Failure  │         │ Replication │           │ Time Sync    │
│ Detector │         │ Manager     │           │ Manager      │
│          │         │             │           │ (Berkeley)   │
└──────────┘         └─────────────┘           └──────────────┘
     │                      │
┌────▼─────┐         ┌──────▼──────┐
│ Failover │         │  Recovery   │
│ Manager  │         │  Sync Mgr   │
└──────────┘         └─────────────┘
```

All nodes communicate through `InMemoryTransport`, which simulates network RPC calls and supports injecting partitions for testing.

---

## Module Breakdown

### `server.py` — Node State
The base `Server` class. Each instance represents one distributed node.

| Attribute | Description |
|-----------|-------------|
| `message_store` | Dict of committed messages keyed by `message_id` |
| `pending_messages` | List of messages queued when no quorum exists |
| `is_alive` | Boolean; set to `False` on crash |
| `last_heartbeat` | Timestamp of last heartbeat, used by failure detector |

Key methods: `store_message()`, `queue_pending_message()`, `simulate_crash()`, `simulate_recovery()`

---

### `raft_consensus.py` — Raft Protocol (Member 4 — Sachith)
Implements the full Raft consensus algorithm across three nodes.

**Classes:**
- `RaftNode` — Per-node Raft state machine (follower / candidate / leader)
- `RaftCluster` — Cluster coordinator; exposes the API used by `api.py`
- `InMemoryTransport` — Simulated in-process RPC layer

**How a message is committed:**
1. Client sends POST to `/api/messages`
2. `api.py` calls `raft_cluster.append_message()`
3. The leader appends the entry to its log and replicates to all followers via `AppendEntries` RPC
4. Once a majority (`⌊N/2⌋ + 1`) acknowledges, the leader advances `commit_index`
5. Each node calls `apply_committed_entries()`, which writes to `server.message_store`

**No-quorum path:**
If fewer than majority nodes are alive, the leader's `append_client_message()` detects this before writing and calls `server.queue_pending_message()`. The background worker (`_background_worker`) retries every 2 seconds until quorum is restored.

**Leader election:**
Raft uses randomised election timeouts (1.5–3.0 s). A follower that times out increments its term, becomes a candidate, and sends `RequestVote` RPCs to peers. The first candidate to collect votes from a majority becomes leader.

---

### `failure_detector.py` — Heartbeat Monitoring (Member 1 — Supun)
Polls each node's `last_heartbeat` timestamp and marks nodes as suspected-failed if they exceed the configured timeout (default 5 s).

Key methods: `check_all_servers()`, `get_alive_servers()`, `is_server_failed(name)`

---

### `failover.py` — Automatic Failover (Member 1 — Supun)
Maintains a reference to the current primary and automatically promotes a backup if the primary is detected as failed. Used for direct message delivery outside the Raft path.

Key methods: `get_active_server()`, `send_with_failover(message_id, content, sender)`

---

### `replication_manager.py` — Quorum Replication (Member 2 — Ruchira)
Manages replication factor (RF=3), write/read quorums, deduplication, and read-repair.

| Feature | Detail |
|---------|--------|
| Write quorum | `W = ⌊RF/2⌋ + 1` — majority must acknowledge before commit |
| Read quorum | `R = RF - W + 1` — sufficient copies to guarantee latest value |
| Deduplication | Tracks `sender:content` fingerprint to prevent double-writes |
| Read-repair | On a successful read, any replica missing the message is automatically updated |

---

### `recovery_sync.py` — Anti-Entropy Sync (Member 1 — Supun)
When a node rejoins, `RecoverySyncManager.sync_server()` identifies the gap between the recovering node's store and the union of all donor stores, then transfers every missing message.

---

### `time_sync.py` — Berkeley Clock Sync (Member 3 — Sasiru)
`TimeSyncManager` coordinates clock synchronisation using a simplified Berkeley Algorithm:

1. Master polls every alive node for its local (skewed) time
2. Computes the average as the reference ("master") time
3. Each node receives its correction offset (`master_time − local_time`)
4. Messages are re-sorted by corrected timestamp to restore causal ordering

`ClockSkewSimulator` assigns random ±2 s offsets to each node at startup to simulate real-world clock drift.

---

### `api.py` — REST API Entry Point
Flask app that wires all modules together and exposes the endpoints consumed by the frontend.

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/status` | Cluster status: nodes, leader, term, quorum, pending count |
| `GET` | `/api/messages` | All committed + pending messages, sorted by corrected timestamp |
| `POST` | `/api/messages` | Send a message; returns `201` on commit, `202` if queued (no quorum) |
| `POST` | `/api/servers/<id>/crash` | Crash node by ID (1–3) |
| `POST` | `/api/servers/<id>/recover` | Recover a crashed node and trigger sync |
| `POST` | `/api/time/sync` | Run Berkeley clock synchronisation |
| `GET` | `/api/time/report` | Clock skews and timestamp metadata |
| `GET` | `/api/logs` | Event log (boot, store, crash, recovery, leader, etc.) |

**POST `/api/messages` — request body:**
```json
{ "sender": "Supun", "content": "Hello cluster!" }
```

**Response `201` (committed):**
```json
{ "id": "msg_abc123", "server": "Node-01", "term": 2, "index": 5, "replicas": ["Node-01","Node-02"] }
```

**Response `202` (queued — no quorum):**
```json
{ "id": "msg_xyz", "status": "pending", "stored_on": ["Node-01"], "message": "No Raft quorum — message queued." }
```

---

## Getting Started

### Prerequisites

- Python 3.10+
- pip

### Install dependencies

```bash
pip install flask flask-cors
```

> No other third-party packages are required. All distributed system logic uses the Python standard library.

### Run the backend

```bash
python api.py
```

The server starts on **http://localhost:8000**. You should see:

```
[API] NexusChat backend running at http://localhost:8000
[API] Endpoints:
  GET  /api/status
  GET  /api/messages
  POST /api/messages
  POST /api/servers/<id>/crash
  POST /api/servers/<id>/recover
  GET  /api/logs
```

The cluster auto-elects a leader and seeds two initial messages on startup.

---

## Running the CLI Tools

### In-process cluster simulator

```bash
python nchat_cli.py
```

Runs all three nodes in a single process — useful for quickly testing leader election, replication, and crash/recovery without the full HTTP stack.

### API terminal client

```bash
# Terminal 1 — start the backend
python api.py

# Terminal 2 — interactive CLI
python nexuschat_cli.py
```

**Available commands:**

| Command | Action |
|---------|--------|
| `m <text>` | Broadcast a message to the cluster |
| `c1` / `c2` / `c3` | Crash Node-01 / Node-02 / Node-03 |
| `r1` / `r2` / `r3` | Recover the specified node |
| `s` or `status` | Print leader, term, and quorum info |
| `sync` | Trigger Berkeley time synchronisation |
| `logs` | Display recent system events |
| `q` or `quit` | Exit |

---

## Key Concepts

### Raft Consensus
Raft divides consensus into three sub-problems: leader election, log replication, and safety. A leader is elected per term; all writes go through the leader. An entry is committed once the leader confirms replication to a majority.

### Quorum
For a 3-node cluster, quorum = 2. The cluster tolerates **1 node failure** and remains fully operational. With only 1 node alive, it loses quorum — writes are queued and the cluster becomes read-unavailable until a second node recovers.

### Pending Message Queue
Messages sent during a quorum outage are stored in `server.pending_messages`. The background thread (`_retry_pending_messages`) polls every 2 seconds and retries each pending message through Raft once quorum is restored.

### Berkeley Clock Synchronisation
Addresses clock skew across distributed nodes. The master collects all node times, computes the average, and sends each node its correction offset. Messages are then re-sorted by corrected timestamp to maintain causal ordering.

---

## References

- Ongaro, D. & Ousterhout, J. (2014). *In Search of an Understandable Consensus Algorithm (Raft)*
- Gifford, D. (1979). *Weighted Voting for Replicated Data*
- Fischer, M., Lynch, N. & Paterson, M. (1985). *Impossibility of Distributed Consensus with One Faulty Process (FLP)*
- Gusella, R. & Zatti, S. (1989). *The Accuracy of the Clock Synchronization Achieved by TEMPO in Berkeley UNIX 4.3BSD*

---

*SE2062 Distributed Systems · Group 30 · © 2026*