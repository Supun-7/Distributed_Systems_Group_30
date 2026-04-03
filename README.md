# NexusChat 🌌
### Fault-Tolerant Distributed Messaging System
**SE2062 — Distributed Systems | Group 30**

---

## Team Members

| Member | Name | Responsibility | Focus Area |
| :--- | :--- | :--- | :--- |
| 01 | Supun Dharmaratne | Fault Tolerance | Failure Detection & Recovery |
| 02 | Ruchira Lakshan | Data Replication | Quorum Consistency |
| 03 | Sasiru Sithujaya | Time Synchronisation | Berkeley Algorithm |
| 04 | Sachith Asmadala | Consensus | Raft Agreement |

---

## Project Overview
NexusChat is a high-availability distributed messaging system that survives node failures without data loss. It uses a **Raft-based consensus protocol** to maintain a replicated log across nodes. Messages are only committed when a quorum is reached.

---

## Terminal Execution Guide

### Prerequisites
Install dependencies:

```bash
pip install flask flask-cors requests colorama
```

### 1. Cluster Simulator (`nchat_cli.py`)
Runs 3 nodes in one process for testing leader elections and crashes:

```bash
python nchat_cli.py
```

- Shows a real-time ASCII dashboard of node roles and log indices.

### 2. API Terminal Client (`nexuschat_cli.py`)
Connects to a live Flask backend:

1. Start the backend:

```bash
python api.py
```

2. Run the client (in another terminal):

```bash
python nexuschat_cli.py
```

---

## Terminal Commands

| Command | Description |
| :--- | :--- |
| `m <text>` | Broadcast a message to the cluster |
| `c1`, `c2`, `c3` | Crash Node 1, 2, or 3 |
| `r1`, `r2`, `r3` | Recover a crashed node |
| `s` / `status` | Show Leader, Term, and Quorum status |
| `sync` | Execute Berkeley time synchronisation |
| `logs` | View recent system events and heartbeats |

---

## Core Architecture

- **Failure Detection:** Heartbeat timeout (≈5s) to detect node failure.  
- **Replication Manager:** Strong consistency with quorum-based consensus.  
- **Anti-Entropy Recovery:** Syncs logs for rejoining nodes.  
- **Time Management:** Berkeley Algorithm to reduce clock skew.  

---

## References

- **Raft Consensus:** Leader election & log replication (Ongaro & Ousterhout).  
- **Quorum-Based Consistency:** Gifford, 1979.  
- **FLP Impossibility:** Fischer, Lynch, Paterson, 1985.  
- **Berkeley Clock Sync:** Gusella & Zatti, 1989.  

---

*Developed for SE2062 Distributed Systems Module. © 2026 Group 30*