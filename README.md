# NexusChat
### Fault-Tolerant Distributed Messaging System
**SE2062 — Distributed Systems | Group Project**

![Python](https://img.shields.io/badge/Python-3.x-3776AB?style=flat&logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-REST_API-000000?style=flat&logo=flask)
![React](https://img.shields.io/badge/React-Frontend-61DAFB?style=flat&logo=react)
![Port](https://img.shields.io/badge/Port-8000-059669?style=flat)

---

## 👥 Team Members

| Member | Name | Responsibility |
|--------|------|----------------|
| **01** | Supun Dharmaratne | Fault Tolerance|
| **02** | Ruchira Lakshan | Data Replication & Consistency |
| **03** | Sasiru Sithujaya | Time Synchronization |
| **04** | Sachith Asmadala | Consensus & Agreement Algorithms |

---

## 📖 Project Overview

NexusChat is a fault-tolerant distributed messaging system where clients send messages to each other through a set of distributed server nodes. The system guarantees real-time message delivery, storage, and retrieval even when servers crash — through **replication**, **automatic failover**, and **anti-entropy recovery sync**.

---

## 📁 Project Structure

```
├── server.py               # Core server node — store, retrieve, crash, recover
├── failure_detector.py     # Heartbeat-based failure detection
├── replication.py          # Message replication (classic)
├── replication_manager.py  # Quorum-based message replication & strong consistency
├── failover.py             # Automatic primary-backup failover
├── recovery_sync.py        # Anti-entropy sync when a node rejoins
├── mian.py                 # End-to-end simulation of all modules
└── api.py                  # Flask REST API — connects backend to frontend
```

---

## 🚀 How to Run

### Step 1 — Install dependencies

```bash
pip3 install flask flask-cors
```

> **macOS users:** Port 5000 is used by AirPlay Receiver. Go to  
> **System Settings → General → AirDrop & Handoff → AirPlay Receiver → OFF**  
> This project runs on **port 8000** to avoid that conflict.

---

### Step 2 — Start the backend

```bash
python api.py
```

You should see:
```
[API] NexusChat backend running at http://localhost:8000
 * Running on http://0.0.0.0:8000
```

---

### Step 3 — Start the frontend

Open a **second terminal**:

```bash
cd Nexus_Chat_Frontend
npm install
npm run dev
```

Then open **http://localhost:5173** in your browser. 🎉

---

## 🔌 API Endpoints

Base URL: `http://localhost:8000/api`

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/status` | Returns cluster state — all nodes, alive count, RF |
| `GET` | `/api/messages` | Returns all messages stored across the cluster |
| `POST` | `/api/messages` | Send a message — triggers replication across alive nodes |
| `POST` | `/api/servers/<id>/crash` | Crash a server node — triggers failover to backup |
| `POST` | `/api/servers/<id>/recover` | Recover a node — triggers anti-entropy sync |
| `GET` | `/api/logs` | Returns all system log events |

---

## ⚙️ How the System Works

### 1. Normal Operation
- All 3 nodes boot and begin sending heartbeats every 2 seconds
- Messages are replicated using Quorum-based consensus in `ReplicationManager`
- Frontend polls `/api/status`, `/api/messages`, `/api/logs` every 2 seconds

### 2. When a Node Crashes
- `FailureDetector` notices the heartbeat silence after 5 seconds
- The node is added to the `suspected_failures` set
- `FailoverManager` automatically promotes the next alive node as primary
- New messages continue to flow to the surviving nodes

### 3. When a Node Recovers
- `simulate_recovery()` brings the node back online
- `RecoverySyncManager` compares the rejoining node's store against donors
- All missed messages are transferred (anti-entropy sync)
- Node rejoins the cluster fully consistent

---

## 📚 Theories & Standards Used

| File | Theory | Source |
|------|--------|--------|
| `failure_detector.py` | Heartbeat timeout model | Chandra & Toueg, 1996 |
| `failure_detector.py` | FLP Impossibility | Fischer, Lynch, Paterson, 1985 |
| `replication_manager.py` | Quorum-based Consistency | Gifford, 1979 |
| `replication_manager.py` | Replication Factor (RF) | Apache Cassandra / HDFS |
| `failover.py` | Primary-Backup failover | Gray & Reuter, 1992 |
| `recovery_sync.py` | Anti-Entropy synchronisation | Demers et al., 1987 |

---

## 🧪 Running the Simulation Only (no frontend)

If you just want to test the backend logic without the frontend:

```bash
python mian.py
```

This runs a full 6-phase simulation — boot, replication, crash, failover, recovery sync, and reports.

---

## ⚠️ Common Errors & Fixes

| Error | Cause | Fix |
|-------|-------|-----|
| `Port 5000 is in use` | macOS AirPlay Receiver | Turn off AirPlay in System Settings |
| `403 Preflight error` | Flask binding to IPv4 only | Use `host="0.0.0.0"` in `app.run()` |
| `npm error ENOENT` | No `package.json` found | Run `npm create vite@latest` first |
| `Module not found` | Missing pip packages | Run `pip3 install flask flask-cors` |

---

## 📝 Notes

- The system incorporates **Quorum-Based Replication** providing **Strong Consistency** by ensuring Write Quorum + Read Quorum > Replication Factor.
- The system includes a 'Read-Repair' mechanism to actively enforce consistency if anomalies/misses are detected across replica nodes.
- All Python files must be in the **same folder** to run correctly.
