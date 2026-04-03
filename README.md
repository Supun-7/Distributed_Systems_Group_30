# NexusChat — Distributed Messaging System
**SE2062 — Distributed Systems | Group 30**

---

## Technologies

<div align="center">
  <img src="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/python/python-original.svg" width="50" title="Python"/>
  <img src="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/flask/flask-original.svg" width="50" title="Flask"/>
  <img src="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/docker/docker-original.svg" width="50" title="Docker"/>
</div>

---

## Team Members

| # | Name | Responsibility | Focus Area |
|---|------|----------------|------------|
| 01 | Supun Dharmaratne | Fault Tolerance | Failure Detection & Recovery |
| 02 | Ruchira Lakshan | Data Replication | Quorum Consistency |
| 03 | Sasiru Sithujaya | Time Synchronisation | Berkeley Algorithm |
| 04 | Sachith Asmadala | Consensus | Raft Agreement |

---

## Project Overview
NexusChat is a **high-availability distributed messaging system** designed to survive node failures without data loss. It uses a **Raft-based consensus protocol** to maintain replicated logs across multiple nodes. Messages are committed only after quorum approval.

---

## Quick Start (Python 3)

### Prerequisites
```bash
pip3 install flask flask-cors requests colorama
```

### Cluster Simulator (`nchat_cli.py`)
Runs 3 nodes in a single process for testing leader election, replication, and crashes:

```bash
python3 nchat_cli.py
```

### API Terminal Client (`nexuschat_cli.py`)
Connects to a live Flask backend:

```bash
python3 api.py
python3 nexuschat_cli.py
```

---

## Commands

| Command | Description |
|---------|-------------|
| `m <text>` | Broadcast a message to the cluster |
| `c1`, `c2`, `c3` | Crash Node 1, 2, or 3 |
| `r1`, `r2`, `r3` | Recover a crashed node |
| `s` / `status` | Show Leader, Term, and Quorum status |
| `sync` | Run Berkeley time synchronisation |
| `logs` | View recent events and heartbeats |

---

## Core Architecture

- **Failure Detection:** Heartbeat timeout (~5s)  
- **Replication Manager:** Strong consistency via quorum-based consensus  
- **Anti-Entropy Recovery:** Syncs logs for rejoining nodes  
- **Time Management:** Berkeley Algorithm reduces clock skew  

### Architecture Diagram
```
[Leader Node] ----> [Follower Nodes]
       |                 |
    Replication       Heartbeats
       |                 |
    Log Sync <------ Delta Sync
```

---

## References

- Raft Consensus — Ongaro & Ousterhout  
- Quorum-Based Consistency — Gifford, 1979  
- FLP Impossibility — Fischer, Lynch, Paterson, 1985  
- Berkeley Clock Sync — Gusella & Zatti, 1989  

---

*Developed for SE2062 Distributed Systems Module. © 2026 Group 30*