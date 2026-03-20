import time  # Built-in Python module — gives us time.time() for timestamps and time.strftime() for readable time

class Server:

    def __init__(self, name):
        self.name = name                        # Unique name to identify this server node (e.g. "Server1")
        self.is_alive = True                    # Boolean flag — True means running, False means crashed
        self.message_store = {}                 # Dictionary acting as local message database — key: message_id, value: message data
        self.last_heartbeat = time.time()       # Records current time in seconds — updated every heartbeat to prove server is alive
        self.pending_messages = []              # List of messages that arrived while server was down — processed on recovery

        print(f"[BOOT] {self.name} has started.")

    def send_heartbeat(self):
        if self.is_alive:                               # Only send heartbeat if server is currently running
            self.last_heartbeat = time.time()           # Update timestamp to NOW — tells detector "I am still alive at this moment"
            print(f"[HEARTBEAT] {self.name} is alive.")
        else:
            # Server is down — cannot send heartbeat, detector will notice the silence
            print(f"[HEARTBEAT] {self.name} is DOWN — no heartbeat sent.")

    def store_message(self, message_id, content, sender="unknown"):
        if not self.is_alive:                   # Server is crashed — cannot store properly right now
            self.pending_messages.append({      # Queue the message instead of dropping it — will be processed on recovery
                "id": message_id,               # Unique label for this message (e.g. "msg_001")
                "content": content,             # The actual message text
                "sender": sender                # Who sent it
            })
            print(f"[QUEUED] {self.name} is DOWN. Message '{message_id}' queued.")
            return False                        # Return False = storage failed, message is only queued not stored

        # Server is alive — store the message properly in the local database
        self.message_store[message_id] = {
            "content": content,                 # The message text
            "sender": sender,                   # Who sent it
            "timestamp": time.time()            # Exact time it was stored — used for ordering and recovery sync later
        }
        print(f"[STORE] {self.name} stored '{message_id}': '{content}'")
        return True                             # Return True = stored successfully

    def retrieve_message(self, message_id):
        if not self.is_alive:                   # Cannot read from a crashed server
            print(f"[ERROR] {self.name} is DOWN — cannot retrieve.")
            return None                         # Return None = nothing to give back

        # Look up the message_id in the dictionary — returns None if not found instead of crashing
        message = self.message_store.get(message_id, None)

        if message:                             # Message was found in the store
            print(f"[GET] {self.name} found '{message_id}': '{message['content']}'")
        else:                                   # message_id does not exist on this server
            print(f"[GET] '{message_id}' NOT found on {self.name}.")

        return message                          # Return the message dict, or None if not found

    def simulate_crash(self):
        self.is_alive = False                   # Flip the flag — all other methods check this before acting
        print(f"[CRASH] ⚠️  {self.name} has CRASHED!")

    def simulate_recovery(self):
        self.is_alive = True                            # Server is back online — re-enable all functionality
        self.last_heartbeat = time.time()               # Reset heartbeat to NOW so detector sees it as alive immediately

        print(f"[RECOVERY] ✅ {self.name} is back ONLINE!")

        if self.pending_messages:               # Check if any messages arrived during the downtime
            print(f"[RECOVERY] Processing {len(self.pending_messages)} queued messages...")

            for msg in self.pending_messages:   # Go through each queued message one by one
                # Now that server is alive, store each queued message properly into message_store
                self.store_message(msg["id"], msg["content"], msg["sender"])

            self.pending_messages.clear()       # Empty the queue — all messages have been processed


if __name__ == "__main__":

    # --- Setup: create two server nodes ---
    s1 = Server("Server1")
    s2 = Server("Server2")

    # --- Normal operation: both servers alive and sending heartbeats ---
    s1.send_heartbeat()     # s1 proves it is alive — updates last_heartbeat timestamp
    s2.send_heartbeat()     # s2 proves it is alive — updates last_heartbeat timestamp

    # --- Store the same message on both servers (this is replication — two copies for safety) ---
    s1.store_message("msg_001", "Hello from Alice!", sender="Alice")
    s2.store_message("msg_001", "Hello from Alice!", sender="Alice")

    # --- Retrieve the message from Server1 ---
    s1.retrieve_message("msg_001")

    # --- Simulate Server1 going down ---
    print("\n--- Crashing Server1 ---")
    s1.simulate_crash()                                         # is_alive becomes False

    # --- Try to send a message while Server1 is down — goes into pending queue ---
    s1.store_message("msg_002", "Anyone there?", sender="Bob")

    # --- Server1 comes back online — processes the queued message ---
    print("\n--- Server1 Recovers ---")
    s1.simulate_recovery()  # is_alive = True, then processes pending_messages list