import time


class Server:

    def __init__(self, name):
        self.name             = name          # Unique node name, e.g. "Node-01"
        self.is_alive         = True          # False when crashed
        self.message_store    = {}            # {message_id: {content, sender, timestamp, ...}}
        self.last_heartbeat   = time.time()
        self.pending_messages = []            # Messages queued while no quorum / crashed

        print(f"[BOOT] {self.name} has started.")

    # -------------------------------------------------------------------------
    # Heartbeat
    # -------------------------------------------------------------------------

    def send_heartbeat(self):
        if self.is_alive:
            self.last_heartbeat = time.time()
            print(f"[HEARTBEAT] {self.name} is alive.")
        else:
            print(f"[HEARTBEAT] {self.name} is DOWN — no heartbeat sent.")

    # -------------------------------------------------------------------------
    # Message storage
    # -------------------------------------------------------------------------

    def store_message(self, message_id, content, sender="unknown",
                      raft_index=None, raft_term=None, committed=False):
        """
        Store a committed message in the local store.
        Called by Raft _apply() once an entry is committed.
        Always stores (server must be alive for Raft to call this).
        """
        self.message_store[message_id] = {
            "content":    content,
            "sender":     sender,
            "timestamp":  time.time(),
            "raft_index": raft_index,
            "raft_term":  raft_term,
            "committed":  committed,
        }
        print(f"[STORE] {self.name} stored '{message_id}': '{content}'")
        return True

    def queue_pending_message(self, message_id, content, sender="unknown"):
        """
        Queue a message when majority is unavailable (no quorum / no leader).
        The message will be promoted to the Raft log once quorum is restored.
        """
        self.pending_messages.append({
            "id":      message_id,
            "content": content,
            "sender":  sender,
            "queued_at": time.time(),
        })
        print(f"[PENDING] {self.name} queued '{message_id}' (no quorum).")

    def retrieve_message(self, message_id):
        if not self.is_alive:
            print(f"[ERROR] {self.name} is DOWN — cannot retrieve.")
            return None

        message = self.message_store.get(message_id)
        if message:
            print(f"[GET] {self.name} found '{message_id}': '{message['content']}'")
        else:
            print(f"[GET] '{message_id}' NOT found on {self.name}.")
        return message

    # -------------------------------------------------------------------------
    # Crash / Recovery simulation
    # -------------------------------------------------------------------------

    def simulate_crash(self):
        self.is_alive = False
        print(f"[CRASH] {self.name} has CRASHED!")

    def simulate_recovery(self):
        self.is_alive       = True
        self.last_heartbeat = time.time()
        print(f"[RECOVERY] {self.name} is back ONLINE!")
