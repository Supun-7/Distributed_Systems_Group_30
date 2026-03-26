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