import time
import hashlib                  # Used to generate unique message IDs from content
from server import Server       # Import Server so we can store messages on server objects

def generate_message_id(sender, content):
    # Combine sender + content + current time to make it unique every time
    raw = f"{sender}:{content}:{time.time()}"

    # Hash the raw string into a short unique ID
    hash_value = hashlib.md5(raw.encode()).hexdigest()

    # Return only first 8 characters — readable and still unique enough
    return "msg_" + hash_value[:8]

class ReplicationManager:

    def __init__(self, servers, replication_factor=2):
        self.servers = servers                  # All server nodes in the cluster
        self.rf = replication_factor            # How many copies to keep per message
        self.replication_map = {}              # Tracks: message_id → [list of server names that hold it]
        self.total_originals = 0               # Count of unique messages sent
        self.total_copies = 0                  # Count of total copies stored (originals × RF)

        print(f"[REPLICATION] Started with RF={replication_factor}.")
        print(f"[REPLICATION] Every message will be stored on {replication_factor} server(s).\n")


    def replicate_message(self, content, sender="unknown"):
        # Step 1: Generate a unique ID for this message
        message_id = generate_message_id(sender, content)

        # Step 2: Find all servers that are currently alive
        alive_servers = [s for s in self.servers if s.is_alive]

        # Step 3: Check if we have enough alive servers
        if len(alive_servers) == 0:
            print("[REPLICATION] ❌ No servers alive — cannot store message!")
            return None

        if len(alive_servers) < self.rf:
            print(f"[REPLICATION] ⚠️  Only {len(alive_servers)} server(s) alive — storing on all of them.")

        # Step 4: Pick the target servers — first RF alive servers
        targets = alive_servers[:self.rf]

        print(f"\n[REPLICATION] Storing '{message_id}' on {len(targets)} server(s)...")

        # Step 5: Store message on each target server
        stored_on = []                          # Track which servers successfully stored it

        for server in targets:
            success = server.store_message(message_id, content, sender)
            if success:
                stored_on.append(server.name)   # Record this server as holding the message
                self.total_copies += 1          # One more copy exists in the system

        # Step 6: Record in the replication map
        self.replication_map[message_id] = stored_on
        self.total_originals += 1

        print(f"[REPLICATION] ✅ '{message_id}' stored on: {stored_on}")
        return message_id                       # Return the ID so caller can retrieve it later

    
    def retrieve_message(self, message_id):
        # Check if we have any record of this message
        if message_id not in self.replication_map:
            print(f"[REPLICATION] ❌ No record of '{message_id}'.")
            return None

        # Get the list of servers that should hold this message
        servers_with_copy = self.replication_map[message_id]

        print(f"\n[REPLICATION] Retrieving '{message_id}'...")
        print(f"  Copies should exist on: {servers_with_copy}")

        # Try each server in order until we find one that is alive and has it
        for server_name in servers_with_copy:

            # Find the actual server object by matching its name
            server = next((s for s in self.servers if s.name == server_name), None)

            if server and server.is_alive:
                result = server.retrieve_message(message_id)
                if result:
                    print(f"  ✅ Successfully retrieved from {server_name}")
                    return result
            else:
                print(f"  ⚠️  {server_name} is DOWN — trying next copy...")

        print(f"  ❌ All servers holding '{message_id}' are DOWN!")
        return None

    
    def print_storage_report(self):
        print("\n" + "=" * 50)
        print("       REPLICATION STORAGE REPORT")
        print("=" * 50)
        print(f"  Replication Factor:   RF = {self.rf}")
        print(f"  Unique messages sent: {self.total_originals}")
        print(f"  Total copies stored:  {self.total_copies}")

        if self.total_originals > 0:
            # Calculate how much extra storage replication costs
            overhead = ((self.total_copies / self.total_originals) - 1) * 100
            print(f"  Storage overhead:     {overhead:.0f}%")
            print(f"\n  Meaning: for every 1GB of messages,")
            print(f"  you need {self.rf}GB of total storage.")

        print(f"\n  Replication Map:")
        for msg_id, server_list in self.replication_map.items():
            print(f"    {msg_id}  →  {server_list}")

        print("=" * 50 + "\n")


if __name__ == "__main__":

    # --- Setup: 4 servers ---
    s1 = Server("Server1")
    s2 = Server("Server2")
    s3 = Server("Server3")
    s4 = Server("Server4")
    all_servers = [s1, s2, s3, s4]

    # --- Create replication manager with RF=3 ---
    rm = ReplicationManager(all_servers, replication_factor=3)

    # --- Send 3 messages ---
    print("--- Sending messages ---")
    id1 = rm.replicate_message("Hello from Alice!", sender="Alice")
    id2 = rm.replicate_message("Meeting at 3pm", sender="Bob")
    id3 = rm.replicate_message("Server check OK", sender="Monitor")

    # --- Crash Server1 ---
    print("\n--- Crashing Server1 ---")
    s1.simulate_crash()

    # --- Try to retrieve message — should use Server2 or Server3 ---
    print("\n--- Retrieving after crash ---")
    rm.retrieve_message(id1)

    # --- Crash Server2 as well ---
    print("\n--- Crashing Server2 as well ---")
    s2.simulate_crash()

    # --- RF=3 means we still have Server3 ---
    print("\n--- Retrieving after two crashes ---")
    rm.retrieve_message(id1)

    # --- Print the full storage report ---
    rm.print_storage_report()