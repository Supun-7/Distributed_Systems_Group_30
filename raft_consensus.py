import time
import random
import hashlib
import threading
from enum import Enum
from dataclasses import dataclass, field


class NodeRole(Enum):
    FOLLOWER = "follower"
    CANDIDATE = "candidate"
    LEADER = "leader"


@dataclass
class LogEntry:
    term: int
    index: int
    command: dict
    timestamp: float = field(default_factory=time.time)


def generate_message_id(sender, content):
    raw = f"{sender}:{content}:{time.time()}:{random.random()}"
    digest = hashlib.md5(raw.encode()).hexdigest()
    return "msg_" + digest[:10]


class InMemoryTransport:
    def __init__(self):
        self.nodes = {}
        self.lock = threading.Lock()

    def register(self, node):
        with self.lock:
            self.nodes[node.server.name] = node

    def request_vote(self, src, dst, payload):
        node = self.nodes.get(dst)
        if not node or not node.server.is_alive:
            return None
        return node.handle_request_vote(payload)

    def append_entries(self, src, dst, payload):
        node = self.nodes.get(dst)
        if not node or not node.server.is_alive:
            return None
        return node.handle_append_entries(payload)


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
        self.server = server
        self.peer_names = peers
        self.transport = transport

        self.role = NodeRole.FOLLOWER
        self.current_term = 0
        self.voted_for = None
        self.log = []

        self.commit_index = 0
        self.last_applied = 0

        self.next_index = {}
        self.match_index = {}

        self.current_leader = None
        self.votes_received = set()

        self.election_timeout_range = election_timeout_range
        self.heartbeat_interval = heartbeat_interval
        self.batch_size = batch_size

        self.last_heartbeat_at = time.time()
        self.election_deadline = self._next_election_deadline()

        self._lock = threading.RLock()

    def _next_election_deadline(self):
        return time.time() + random.uniform(*self.election_timeout_range)

    def _majority_count(self):
        cluster_size = len(self.peer_names) + 1
        return (cluster_size // 2) + 1

    def _last_log_index(self):
        return len(self.log)

    def _last_log_term(self):
        if not self.log:
            return 0
        return self.log[-1].term

    def _reset_election_timer(self):
        self.last_heartbeat_at = time.time()
        self.election_deadline = self._next_election_deadline()

    def status(self):
        with self._lock:
            return {
                "node": self.server.name,
                "alive": self.server.is_alive,
                "role": self.role.value,
                "term": self.current_term,
                "leader": self.current_leader,
                "log_len": len(self.log),
                "commit_index": self.commit_index,
                "last_applied": self.last_applied,
            }
        
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

    def start_election(self):
        with self._lock:
            self.role = NodeRole.CANDIDATE
            self.current_term += 1
            self.voted_for = self.server.name
            self.votes_received = {self.server.name}
            self.current_leader = None
            self._reset_election_timer()

            payload = {
                "term": self.current_term,
                "candidate_id": self.server.name,
                "last_log_index": self._last_log_index(),
                "last_log_term": self._last_log_term(),
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
            candidate_term = payload["term"]
            candidate_id = payload["candidate_id"]
            candidate_last_index = payload["last_log_index"]
            candidate_last_term = payload["last_log_term"]

            if candidate_term < self.current_term:
                return {
                    "term": self.current_term,
                    "vote_granted": False,
                    "source": self.server.name,
                }

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
                return {
                    "term": self.current_term,
                    "vote_granted": True,
                    "source": self.server.name,
                }

            return {
                "term": self.current_term,
                "vote_granted": False,
                "source": self.server.name,
            }

    def _become_leader(self):
        self.role = NodeRole.LEADER
        self.current_leader = self.server.name

        next_idx = self._last_log_index() + 1
        self.next_index = {peer: next_idx for peer in self.peer_names}
        self.match_index = {peer: 0 for peer in self.peer_names}

        self.send_heartbeats()

    def _step_down(self, new_term):
        self.role = NodeRole.FOLLOWER
        self.current_term = new_term
        self.voted_for = None
        self.votes_received = set()
        self.current_leader = None
        self._reset_election_timer() 

    def send_heartbeats(self):
        for peer in self.peer_names:
            self.replicate_to_peer(peer, heartbeat_only=True)

    def replicate_to_peer(self, peer, heartbeat_only=False):
        with self._lock:
            if self.role != NodeRole.LEADER:
                return False

            next_idx = self.next_index.get(peer, 1)
            prev_log_index = next_idx - 1
            prev_log_term = 0
            if prev_log_index > 0:
                prev_log_term = self.log[prev_log_index - 1].term

            entries = []
            if not heartbeat_only:
                entries = self.log[next_idx - 1: next_idx - 1 + self.batch_size]

            payload = {
                "term": self.current_term,
                "leader_id": self.server.name,
                "prev_log_index": prev_log_index,
                "prev_log_term": prev_log_term,
                "entries": [
                    {
                        "term": entry.term,
                        "index": entry.index,
                        "command": entry.command,
                        "timestamp": entry.timestamp,
                    }
                    for entry in entries
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
                    self.next_index[peer] = response["match_index"] + 1
                self._advance_commit_index()
                return True

            self.next_index[peer] = max(1, self.next_index.get(peer, 1) - 1)
            return False

    def handle_append_entries(self, payload):
        with self._lock:
            leader_term = payload["term"]

            if leader_term < self.current_term:
                return {
                    "term": self.current_term,
                    "success": False,
                    "match_index": self._last_log_index(),
                }

            if leader_term > self.current_term or self.role != NodeRole.FOLLOWER:
                self._step_down(leader_term)

            self.current_leader = payload["leader_id"]
            self._reset_election_timer()

            prev_log_index = payload["prev_log_index"]
            prev_log_term = payload["prev_log_term"]

            if prev_log_index > len(self.log):
                return {
                    "term": self.current_term,
                    "success": False,
                    "match_index": self._last_log_index(),
                }

            if prev_log_index > 0:
                local_prev_term = self.log[prev_log_index - 1].term
                if local_prev_term != prev_log_term:
                    self.log = self.log[:prev_log_index - 1]
                    return {
                        "term": self.current_term,
                        "success": False,
                        "match_index": self._last_log_index(),
                    }

            incoming_entries = payload["entries"]
            for entry_data in incoming_entries:
                idx = entry_data["index"]

                if idx <= len(self.log):
                    local_entry = self.log[idx - 1]
                    if local_entry.term != entry_data["term"]:
                        self.log = self.log[:idx - 1]

                if idx > len(self.log):
                    self.log.append(
                        LogEntry(
                            term=entry_data["term"],
                            index=entry_data["index"],
                            command=entry_data["command"],
                            timestamp=entry_data["timestamp"],
                        )
                    )

            leader_commit = payload["leader_commit"]
            if leader_commit > self.commit_index:
                self.commit_index = min(leader_commit, len(self.log))

            self.apply_committed_entries()

            return {
                "term": self.current_term,
                "success": True,
                "match_index": self._last_log_index(),
            }

    def _advance_commit_index(self):
        for idx in range(len(self.log), self.commit_index, -1):
            if self.log[idx - 1].term != self.current_term:
                continue

            replicated = 1
            for peer in self.peer_names:
                if self.match_index.get(peer, 0) >= idx:
                    replicated += 1

            if replicated >= self._majority_count():
                self.commit_index = idx
                self.apply_committed_entries()
                break

    def append_client_message(self, sender, content):
        with self._lock:
            if self.role != NodeRole.LEADER:
                return {
                    "ok": False,
                    "redirect": self.current_leader,
                    "error": "not_leader",
                }

            message_id = generate_message_id(sender, content)
            command = {
                "op": "store_message",
                "message_id": message_id,
                "sender": sender,
                "content": content,
                "client_ts": time.time(),
            }

            entry = LogEntry(
                term=self.current_term,
                index=len(self.log) + 1,
                command=command,
            )
            self.log.append(entry)

        success_count = 1
        successful_replicas = [self.server.name]

        for peer in self.peer_names:
            replicated = self.replicate_until_match(peer)
            if replicated:
                success_count += 1
                successful_replicas.append(peer)

        with self._lock:
            self._advance_commit_index()

            if self.commit_index >= entry.index:
                return {
                    "ok": True,
                    "message_id": message_id,
                    "leader": self.server.name,
                    "log_index": entry.index,
                    "term": self.current_term,
                    "replicated_to": success_count,
                    "replicas": successful_replicas,
                }

            return {
                "ok": False,
                "error": "commit_failed",
                "leader": self.server.name,
                "replicated_to": success_count,
            }

    def replicate_until_match(self, peer, max_attempts=10):
        for _ in range(max_attempts):
            if self.replicate_to_peer(peer, heartbeat_only=False):
                return True
        return False

    def apply_committed_entries(self):
        while self.last_applied < self.commit_index:
            self.last_applied += 1
            entry = self.log[self.last_applied - 1]
            self._apply(entry.command)

    def _apply(self, command):
        if command["op"] == "store_message":
            message_id = command["message_id"]
            if message_id not in self.server.message_store:
                self.server.store_message(
                    message_id,
                    command["content"],
                    sender=command["sender"],
                )       


class RaftCluster:
    def __init__(self, servers):
        self.servers = servers
        self.transport = InMemoryTransport()
        self.nodes = {}

        names = [s.name for s in servers]
        for server in servers:
            peers = [n for n in names if n != server.name]
            node = RaftNode(server=server, peers=peers, transport=self.transport)
            self.nodes[server.name] = node
            self.transport.register(node)

    def cluster_status(self):
        return {name: node.status() for name, node in self.nodes.items()}
    
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

    def start_election(self):
        with self._lock:
            self.role = NodeRole.CANDIDATE
            self.current_term += 1
            self.voted_for = self.server.name
            self.votes_received = {self.server.name}
            self.current_leader = None
            self._reset_election_timer()

            payload = {
                "term": self.current_term,
                "candidate_id": self.server.name,
                "last_log_index": self._last_log_index(),
                "last_log_term": self._last_log_term(),
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
            candidate_term = payload["term"]
            candidate_id = payload["candidate_id"]
            candidate_last_index = payload["last_log_index"]
            candidate_last_term = payload["last_log_term"]

            if candidate_term < self.current_term:
                return {
                    "term": self.current_term,
                    "vote_granted": False,
                    "source": self.server.name,
                }

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
                return {
                    "term": self.current_term,
                    "vote_granted": True,
                    "source": self.server.name,
                }

            return {
                "term": self.current_term,
                "vote_granted": False,
                "source": self.server.name,
            }

    def _become_leader(self):
        self.role = NodeRole.LEADER
        self.current_leader = self.server.name

        next_idx = self._last_log_index() + 1
        self.next_index = {peer: next_idx for peer in self.peer_names}
        self.match_index = {peer: 0 for peer in self.peer_names}

        self.send_heartbeats()

    def _step_down(self, new_term):
        self.role = NodeRole.FOLLOWER
        self.current_term = new_term
        self.voted_for = None
        self.votes_received = set()
        self.current_leader = None
        self._reset_election_timer()
        
    def tick_all(self):
        for node in self.nodes.values():
            node.tick()

    def elect_leader_blocking(self, rounds=10, pause=0.2):
        for _ in range(rounds):
            self.tick_all()
            leader = self.get_leader()
            if leader:
                return leader
            time.sleep(pause)
        return None

    def get_leader(self):
        leaders = [
            node for node in self.nodes.values()
            if node.server.is_alive and node.role == NodeRole.LEADER
        ]
        return leaders[0] if leaders else None    
    
    def append_message(self, sender, content):
        leader = self.get_leader()
        if not leader:
            leader = self.elect_leader_blocking()

        if not leader:
            return {"ok": False, "error": "no_leader"}

        result = leader.append_client_message(sender, content)

        if not result["ok"] and result.get("redirect"):
            redirected = self.nodes[result["redirect"]]
            if redirected.server.is_alive:
                return redirected.append_client_message(sender, content)

        return result