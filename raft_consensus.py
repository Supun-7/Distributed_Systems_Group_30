import time
import random
import hashlib
import threading
from enum import Enum
from dataclasses import dataclass, field


class NodeRole(Enum):
    FOLLOWER  = "follower"
    CANDIDATE = "candidate"
    LEADER    = "leader"


@dataclass
class LogEntry:
    term:      int
    index:     int
    command:   dict
    timestamp: float = field(default_factory=time.time)


def generate_message_id(sender, content):
    raw    = f"{sender}:{content}:{time.time()}:{random.random()}"
    digest = hashlib.md5(raw.encode()).hexdigest()
    return "msg_" + digest[:10]


# ---------------------------------------------------------------------------
# Transport layer
# ---------------------------------------------------------------------------

class InMemoryTransport:
    """
    Simulates in-process RPC between RaftNodes.
    Supports injecting network partitions (disconnect / reconnect).
    """

    def __init__(self):
        self.nodes      = {}           # name -> RaftNode
        self.drop_rules = set()        # {(src, dst)} partitioned links
        self.lock       = threading.Lock()

    def register(self, node):
        with self.lock:
            self.nodes[node.server.name] = node

    def disconnect(self, src, dst):
        with self.lock:
            self.drop_rules.add((src, dst))

    def reconnect(self, src, dst):
        with self.lock:
            self.drop_rules.discard((src, dst))

    def reconnect_all(self):
        with self.lock:
            self.drop_rules.clear()

    def can_deliver(self, src, dst):
        with self.lock:
            return (src, dst) not in self.drop_rules

    def request_vote(self, src, dst, payload):
        if not self.can_deliver(src, dst):
            return None
        node = self.nodes.get(dst)
        if not node or not node.server.is_alive:
            return None
        return node.handle_request_vote(payload)

    def append_entries(self, src, dst, payload):
        if not self.can_deliver(src, dst):
            return None
        node = self.nodes.get(dst)
        if not node or not node.server.is_alive:
            return None
        return node.handle_append_entries(payload)


# ---------------------------------------------------------------------------
# RaftNode - one per server
# ---------------------------------------------------------------------------

class RaftNode:
    def __init__(
        self,
        server,
        peers,
        transport,
        election_timeout_range=(1.5, 3.0),
        heartbeat_interval=0.5,
        batch_size=64,
    ):
        self.server     = server
        self.peer_names = peers
        self.transport  = transport

        self.role           = NodeRole.FOLLOWER
        self.current_term   = 0
        self.voted_for      = None
        self.log            = []

        self.commit_index = 0
        self.last_applied = 0

        self.next_index  = {}
        self.match_index = {}

        self.current_leader = None
        self.votes_received = set()

        self.election_timeout_range = election_timeout_range
        self.heartbeat_interval     = heartbeat_interval
        self.batch_size             = batch_size

        self.last_heartbeat_at = time.time()
        self.election_deadline = self._next_election_deadline()

        self._lock = threading.RLock()

    # -- helpers -------------------------------------------------------------

    def _next_election_deadline(self):
        return time.time() + random.uniform(*self.election_timeout_range)

    def _majority_count(self):
        cluster_size = len(self.peer_names) + 1
        return (cluster_size // 2) + 1

    def _last_log_index(self):
        return len(self.log)

    def _last_log_term(self):
        return self.log[-1].term if self.log else 0

    def _reset_election_timer(self):
        self.last_heartbeat_at = time.time()
        self.election_deadline = self._next_election_deadline()

    def status(self):
        with self._lock:
            return {
                "node":         self.server.name,
                "alive":        self.server.is_alive,
                "role":         self.role.value,
                "term":         self.current_term,
                "leader":       self.current_leader,
                "log_len":      len(self.log),
                "commit_index": self.commit_index,
                "last_applied": self.last_applied,
            }

    # -- tick ----------------------------------------------------------------

    def tick(self):
        if not self.server.is_alive:
            return

        now = time.time()
        with self._lock:
            if self.role == NodeRole.LEADER:
                if now - self.last_heartbeat_at >= self.heartbeat_interval:
                    self.send_heartbeats()
                    self.last_heartbeat_at = now
            else:
                if now >= self.election_deadline:
                    self.start_election()

            self.apply_committed_entries()

    # -- election ------------------------------------------------------------

    def start_election(self):
        with self._lock:
            self.role           = NodeRole.CANDIDATE
            self.current_term  += 1
            self.voted_for      = self.server.name
            self.votes_received = {self.server.name}
            self.current_leader = None
            self._reset_election_timer()

            payload = {
                "term":           self.current_term,
                "candidate_id":   self.server.name,
                "last_log_index": self._last_log_index(),
                "last_log_term":  self._last_log_term(),
            }

        for peer in self.peer_names:
            response = self.transport.request_vote(self.server.name, peer, payload)
            if response is None:
                continue
            self._process_vote_response(response)

    def _process_vote_response(self, response):
        with self._lock:
            if response["term"] > self.current_term:
                self._step_down(response["term"])
                return
            if self.role != NodeRole.CANDIDATE:
                return
            if response.get("vote_granted"):
                self.votes_received.add(response["source"])
            if len(self.votes_received) >= self._majority_count():
                self._become_leader()

    def handle_request_vote(self, payload):
        with self._lock:
            candidate_term       = payload["term"]
            candidate_id         = payload["candidate_id"]
            candidate_last_index = payload["last_log_index"]
            candidate_last_term  = payload["last_log_term"]

            if candidate_term < self.current_term:
                return {"term": self.current_term, "vote_granted": False, "source": self.server.name}

            if candidate_term > self.current_term:
                self._step_down(candidate_term)

            up_to_date = (
                candidate_last_term > self._last_log_term()
                or (
                    candidate_last_term == self._last_log_term()
                    and candidate_last_index >= self._last_log_index()
                )
            )
            can_vote = self.voted_for is None or self.voted_for == candidate_id

            if can_vote and up_to_date:
                self.voted_for = candidate_id
                self._reset_election_timer()
                return {"term": self.current_term, "vote_granted": True,  "source": self.server.name}

            return {"term": self.current_term, "vote_granted": False, "source": self.server.name}

    def _become_leader(self):
        self.role           = NodeRole.LEADER
        self.current_leader = self.server.name
        next_idx            = self._last_log_index() + 1
        self.next_index     = {peer: next_idx for peer in self.peer_names}
        self.match_index    = {peer: 0        for peer in self.peer_names}
        self.send_heartbeats()

    def _step_down(self, new_term):
        self.role           = NodeRole.FOLLOWER
        self.current_term   = new_term
        self.voted_for      = None
        self.votes_received = set()
        self.current_leader = None
        self._reset_election_timer()

    # -- replication ---------------------------------------------------------

    def send_heartbeats(self):
        for peer in self.peer_names:
            self.replicate_to_peer(peer, heartbeat_only=True)

    def replicate_to_peer(self, peer, heartbeat_only=False):
        with self._lock:
            if self.role != NodeRole.LEADER:
                return False

            next_idx       = self.next_index.get(peer, 1)
            prev_log_index = next_idx - 1
            prev_log_term  = 0
            if prev_log_index > 0:
                prev_log_term = self.log[prev_log_index - 1].term

            entries = []
            if not heartbeat_only:
                entries = self.log[next_idx - 1: next_idx - 1 + self.batch_size]

            payload = {
                "term":           self.current_term,
                "leader_id":      self.server.name,
                "prev_log_index": prev_log_index,
                "prev_log_term":  prev_log_term,
                "entries": [
                    {
                        "term":      e.term,
                        "index":     e.index,
                        "command":   e.command,
                        "timestamp": e.timestamp,
                    }
                    for e in entries
                ],
                "leader_commit": self.commit_index,
            }

        response = self.transport.append_entries(self.server.name, peer, payload)
        if response is None:
            return False
        return self._process_append_response(peer, response, len(entries))

    def _process_append_response(self, peer, response, entries_sent):
        with self._lock:
            if response["term"] > self.current_term:
                self._step_down(response["term"])
                return False
            if self.role != NodeRole.LEADER:
                return False
            if response["success"]:
                if entries_sent > 0:
                    self.match_index[peer] = response["match_index"]
                    self.next_index[peer]  = response["match_index"] + 1
                self._advance_commit_index()
                return True
            self.next_index[peer] = max(1, self.next_index.get(peer, 1) - 1)
            return False

    def handle_append_entries(self, payload):
        with self._lock:
            leader_term = payload["term"]

            if leader_term < self.current_term:
                return {"term": self.current_term, "success": False, "match_index": self._last_log_index()}

            if leader_term > self.current_term or self.role != NodeRole.FOLLOWER:
                self._step_down(leader_term)

            self.current_leader = payload["leader_id"]
            self._reset_election_timer()

            prev_log_index = payload["prev_log_index"]
            prev_log_term  = payload["prev_log_term"]

            if prev_log_index > len(self.log):
                return {"term": self.current_term, "success": False, "match_index": self._last_log_index()}

            if prev_log_index > 0:
                local_prev_term = self.log[prev_log_index - 1].term
                if local_prev_term != prev_log_term:
                    self.log = self.log[:prev_log_index - 1]
                    return {"term": self.current_term, "success": False, "match_index": self._last_log_index()}

            for entry_data in payload["entries"]:
                idx = entry_data["index"]
                if idx <= len(self.log):
                    if self.log[idx - 1].term != entry_data["term"]:
                        self.log = self.log[:idx - 1]
                if idx > len(self.log):
                    self.log.append(
                        LogEntry(
                            term=      entry_data["term"],
                            index=     entry_data["index"],
                            command=   entry_data["command"],
                            timestamp= entry_data["timestamp"],
                        )
                    )

            leader_commit = payload["leader_commit"]
            if leader_commit > self.commit_index:
                self.commit_index = min(leader_commit, len(self.log))

            self.apply_committed_entries()
            return {"term": self.current_term, "success": True, "match_index": self._last_log_index()}

    def _advance_commit_index(self):
        for idx in range(len(self.log), self.commit_index, -1):
            if self.log[idx - 1].term != self.current_term:
                continue
            replicated = 1  # leader itself
            for peer in self.peer_names:
                if self.match_index.get(peer, 0) >= idx:
                    replicated += 1
            if replicated >= self._majority_count():
                self.commit_index = idx
                self.apply_committed_entries()
                break

    # -- client write (quorum-aware) -----------------------------------------

    def append_client_message(self, sender, content):
        """
        Accepts a client message only if Raft majority is reachable.
        If majority is down, queues the message as PENDING on the server
        and returns ok=False / error='no_quorum'.
        """
        with self._lock:
            if self.role != NodeRole.LEADER:
                return {"ok": False, "redirect": self.current_leader, "error": "not_leader"}

        # Quorum pre-check: count reachable alive peers before writing
        alive_peers = [
            peer for peer in self.peer_names
            if (node := self.transport.nodes.get(peer)) and node.server.is_alive
        ]
        total_alive = 1 + len(alive_peers)   # self + alive peers
        majority    = self._majority_count()  # 2 for a 3-node cluster

        if total_alive < majority:
            # Majority down -> store as PENDING, do NOT commit
            message_id = generate_message_id(sender, content)
            self.server.queue_pending_message(message_id, content, sender)
            return {
                "ok":         False,
                "error":      "no_quorum",
                "message_id": message_id,
                "queued":     True,
                "alive":      total_alive,
                "need":       majority,
            }

        with self._lock:
            message_id = generate_message_id(sender, content)
            command = {
                "op":         "store_message",
                "message_id": message_id,
                "sender":     sender,
                "content":    content,
                "client_ts":  time.time(),
            }
            entry = LogEntry(
                term=    self.current_term,
                index=   len(self.log) + 1,
                command= command,
            )
            self.log.append(entry)

        success_count       = 1
        successful_replicas = [self.server.name]

        for peer in self.peer_names:
            if self.replicate_until_match(peer):
                success_count += 1
                successful_replicas.append(peer)

        with self._lock:
            self._advance_commit_index()

            if self.commit_index >= entry.index:
                return {
                    "ok":            True,
                    "message_id":    message_id,
                    "leader":        self.server.name,
                    "log_index":     entry.index,
                    "term":          self.current_term,
                    "replicated_to": success_count,
                    "replicas":      successful_replicas,
                }

            # Race: node failed mid-write -> queue
            self.server.queue_pending_message(message_id, content, sender)
            return {
                "ok":            False,
                "error":         "commit_failed",
                "leader":        self.server.name,
                "replicated_to": success_count,
                "message_id":    message_id,
                "queued":        True,
            }

    def replicate_until_match(self, peer, max_attempts=10):
        for _ in range(max_attempts):
            if self.replicate_to_peer(peer, heartbeat_only=False):
                return True
        return False

    # -- log application -----------------------------------------------------

    def apply_committed_entries(self):
        while self.last_applied < self.commit_index:
            self.last_applied += 1
            entry = self.log[self.last_applied - 1]
            self._apply(entry.command, entry)

    def _apply(self, command, entry=None):
        if command["op"] == "store_message":
            message_id = command["message_id"]
            if message_id not in self.server.message_store:
                self.server.store_message(
                    message_id,
                    command["content"],
                    sender=     command["sender"],
                    raft_index= entry.index if entry else None,
                    raft_term=  entry.term  if entry else None,
                    committed=  True,
                )

    def on_server_recovered(self):
        with self._lock:
            self._reset_election_timer()


# ---------------------------------------------------------------------------
# RaftCluster - manages a set of RaftNodes
# ---------------------------------------------------------------------------

class RaftCluster:
    """
    Owns all RaftNodes and exposes the API consumed by api.py.
    """

    def __init__(self, servers):
        self.servers   = servers
        self.transport = InMemoryTransport()
        self.nodes     = {}

        names = [s.name for s in servers]
        for server in servers:
            peers = [n for n in names if n != server.name]
            node  = RaftNode(server=server, peers=peers, transport=self.transport)
            self.nodes[server.name] = node
            self.transport.register(node)

        self._bg_running = False
        self._bg_thread  = None
    
    def _background_worker(self):           
        while self._bg_running:
            try:
                status = self.cluster_status()

                if status["hasQuorum"] and status["leader"] is not None:
                    self._retry_pending_messages()

                time.sleep(2.0)

            except Exception as e:
                print("[BG ERROR]", e)

    def _retry_pending_messages(self):
        for node in self.nodes.values():
            server = node.server

            # skip dead nodes
            if not server.is_alive:
                continue

            pending = list(server.pending_messages)

            for msg in pending:
                print(f"[RETRY] Trying {msg['id']} from {server.name}")

                result = self.append_message(
                    sender=msg["sender"],
                    content=msg["content"]
                )

                if result.get("ok"):
                    server.pending_messages.remove(msg)
                    print(f"[RECOVERED] {msg['id']} committed")

    # -- background tick -----------------------------------------------------

    def start_background_processing(self, interval=0.1):
        if self._bg_running:
            return
        self._bg_running = True

        def _run():
            while self._bg_running:
                self.tick_all()
                time.sleep(interval)

        self._bg_thread = threading.Thread(target=_run, daemon=True)
        self._bg_thread.start()
        
        self._retry_thread = threading.Thread(target=self._background_worker, daemon=True)
        self._retry_thread.start()

    def stop_background_processing(self):
        self._bg_running = False

    def tick_all(self):
        for node in self.nodes.values():
            node.tick()

    # -- leader management ---------------------------------------------------

    def get_leader(self):
        leaders = [
            node for node in self.nodes.values()
            if node.server.is_alive and node.role == NodeRole.LEADER
        ]
        return leaders[0] if leaders else None

    def elect_leader_blocking(self, rounds=10, pause=0.2):
        for _ in range(rounds):
            self.tick_all()
            leader = self.get_leader()
            if leader:
                return leader
            time.sleep(pause)
        return None

    # -- client write --------------------------------------------------------

    def append_message(self, sender, content):
        leader = self.get_leader()
        if not leader:
            leader = self.elect_leader_blocking()

        if not leader:
            # No alive majority -> queue on any alive server
            for s in self.servers:
                if s.is_alive:
                    message_id = generate_message_id(sender, content)
                    s.queue_pending_message(message_id, content, sender)
                    return {
                        "ok":         False,
                        "error":      "no_leader",
                        "message_id": message_id,
                        "queued":     True,
                    }
            return {"ok": False, "error": "no_leader"}

        result = leader.append_client_message(sender, content)

        # Redirect once if the leader stepped down mid-call
        if not result["ok"] and result.get("redirect"):
            redirected = self.nodes.get(result["redirect"])
            if redirected and redirected.server.is_alive:
                result = redirected.append_client_message(sender, content)

        return result

    # -- status --------------------------------------------------------------

    def cluster_status(self):
        """Returns the dict shape that api.py /api/status expects."""
        alive_count   = sum(1 for s in self.servers if s.is_alive)
        majority      = (len(self.servers) // 2) + 1
        has_quorum    = alive_count >= majority
        leader_node   = self.get_leader()
        leader_name   = leader_node.server.name if leader_node else None
        current_term  = max((n.current_term for n in self.nodes.values()), default=0)
        pending_count = sum(len(s.pending_messages) for s in self.servers)

        node_statuses = {}
        for name, node in self.nodes.items():
            st = node.status()
            node_statuses[name] = {**st, "logLen": st["log_len"]}

        return {
            "nodes":        node_statuses,
            "leader":       leader_name,
            "currentTerm":  current_term,
            "hasQuorum":    has_quorum,
            "pendingCount": pending_count,
        }

    # -- helpers for api.py --------------------------------------------------

    def get_committed_holders(self, message_id):
        return [
            s.name for s in self.servers
            if s.is_alive and message_id in s.message_store
        ]

    def force_catch_up(self, node_name, rounds=5, pause=0.1):
        leader = self.get_leader()
        if not leader:
            return False
        for _ in range(rounds):
            leader.replicate_to_peer(node_name)
            self.tick_all()
            time.sleep(pause)
        recovering = self.nodes.get(node_name)
        return recovering and recovering.commit_index >= leader.commit_index

    # -- node lifecycle ------------------------------------------------------

    def crash_node(self, node_name):
        self.nodes[node_name].server.simulate_crash()

    def recover_node(self, node_name):
        node = self.nodes[node_name]
        node.server.simulate_recovery()
        node.on_server_recovered()

    def partition(self, isolated_node, other_nodes):
        for other in other_nodes:
            self.transport.disconnect(isolated_node, other)
            self.transport.disconnect(other, isolated_node)

    def heal_all_partitions(self):
        self.transport.reconnect_all()
