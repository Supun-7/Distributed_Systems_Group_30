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