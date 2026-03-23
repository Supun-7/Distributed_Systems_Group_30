import time                               # For timestamps
from server import Server                 # Server objects to sync messages between

class RecoverySyncManager:

    def __init__(self, servers):
        self.servers = servers              # All server nodes (donors + potential rejoiners)
        self.sync_log = []                  # History of every sync event performed

        print(f"[SYNC] RecoverySyncManager ready. Watching {len(servers)} server(s).\n")

    def sync_server(self, recovering_server):
        # ── Step 1: Confirm the server is actually back online ────────────────
        if not recovering_server.is_alive:
            print(f"[SYNC] ❌ '{recovering_server.name}' is still DOWN — cannot sync yet.")
            return 0                        # Return 0 = no messages transferred

        print(f"\n[SYNC] '{recovering_server.name}' has rejoined — starting sync...")
        print(f"[SYNC] It currently has {len(recovering_server.message_store)} message(s).")

        # ── Step 2: Find donor servers — alive servers that are NOT the rejoiner ──
        donors = [
            s for s in self.servers
            if s.is_alive and s.name != recovering_server.name
        ]

        if not donors:
            # No other servers alive — nothing to sync from, recovering server stays as-is
            print(f"[SYNC] ⚠️  No donor servers available — sync skipped.")
            return 0

        print(f"[SYNC] Donor servers: {[d.name for d in donors]}")

        # ── Step 3: Collect all messages from all donors ──────────────────────
        # We build a combined "master" view of every message that exists
        # across all healthy servers. Key = message_id, value = message data.

        master_store = {}                   # All unique messages seen across all donors

        for donor in donors:
            for msg_id, msg_data in donor.message_store.items():
                if msg_id not in master_store:
                    master_store[msg_id] = msg_data     # Add to master if not already there

        print(f"[SYNC] Total unique messages across all donors: {len(master_store)}")

        # ── Step 4: Find the GAP — messages the rejoiner is missing ──────────
        # Compare the master store against what the recovering server already has.
        # Anything in master but NOT in recovering_server.message_store is a missing message.

        missing_ids = [
            msg_id for msg_id in master_store
            if msg_id not in recovering_server.message_store
        ]

        if not missing_ids:
            print(f"[SYNC] ✅ '{recovering_server.name}' is already up to date — nothing to sync.")
            return 0

        print(f"[SYNC] Gap found: {len(missing_ids)} missing message(s) to transfer.")

        # ── Step 5: Transfer each missing message to the recovering server ────
        transferred = 0

        for msg_id in missing_ids:
            msg_data = master_store[msg_id]

            # Push the message directly into the recovering server's store
            # (bypassing the is_alive check since we already confirmed it's online)
            recovering_server.message_store[msg_id] = {
                "content":   msg_data["content"],
                "sender":    msg_data["sender"],
                "timestamp": msg_data["timestamp"]      # Keep original timestamp — not now
            }

            transferred += 1
            print(f"[SYNC] Transferred '{msg_id}': '{msg_data['content']}' → {recovering_server.name}")

        # ── Step 6: Record this sync event in the log ─────────────────────────
        self.sync_log.append({
            "server":      recovering_server.name,
            "synced_at":   time.strftime('%H:%M:%S'),
            "transferred": transferred,
            "donors_used": [d.name for d in donors]
        })

        print(f"[SYNC] ✅ Sync complete — {transferred} message(s) restored to '{recovering_server.name}'.")
        print(f"[SYNC] '{recovering_server.name}' now has {len(recovering_server.message_store)} message(s) total.")
        return transferred                  # Return count so caller knows how many were synced

    def print_report(self):
        print("\n" + "=" * 50)
        print("       RECOVERY SYNC REPORT")
        print("=" * 50)

        if not self.sync_log:
            print("  No sync events occurred.")
        else:
            print(f"  Total sync events: {len(self.sync_log)}\n")

            for i, event in enumerate(self.sync_log, 1):
                print(f"  [{i}] Server:      {event['server']}")
                print(f"       Synced at:   {event['synced_at']}")
                print(f"       Transferred: {event['transferred']} message(s)")
                print(f"       Donors used: {event['donors_used']}")
                print()

        # Show final state of every server's message store
        print("  Final message counts per server:")
        for s in self.servers:
            status = "alive" if s.is_alive else "DOWN"
            print(f"    {s.name} ({status}): {len(s.message_store)} message(s)")

        print("=" * 50 + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# DEMO — Run this file directly to see recovery sync in action
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":

    # --- Setup: 3 servers ---
    s1 = Server("Server1")
    s2 = Server("Server2")
    s3 = Server("Server3")
    servers = [s1, s2, s3]

    sync_manager = RecoverySyncManager(servers)

    # --- Populate all servers with initial messages (simulating replication) ---
    print("--- Initial state: storing messages on all servers ---")
    for msg_id, content, sender in [
        ("msg_001", "Hello from Alice!", "Alice"),
        ("msg_002", "Meeting at 3pm",   "Bob"),
        ("msg_003", "Server check OK",  "Monitor"),
    ]:
        s1.store_message(msg_id, content, sender)
        s2.store_message(msg_id, content, sender)
        s3.store_message(msg_id, content, sender)

    # --- Crash Server1 ---
    print("\n--- Server1 CRASHES ---")
    s1.simulate_crash()

    # --- New messages arrive while Server1 is DOWN (only stored on S2 and S3) ---
    print("\n--- New messages arrive while Server1 is down ---")
    s2.store_message("msg_004", "Anyone there?",     "Bob")
    s2.store_message("msg_005", "Critical update!",  "System")
    s3.store_message("msg_004", "Anyone there?",     "Bob")
    s3.store_message("msg_005", "Critical update!",  "System")

    # Show the gap — Server1 is missing msg_004 and msg_005
    print(f"\n  Server1 has: {list(s1.message_store.keys())} (stale — 2 messages behind)")
    print(f"  Server2 has: {list(s2.message_store.keys())}")
    print(f"  Server3 has: {list(s3.message_store.keys())}")

    # --- Server1 comes back online ---
    print("\n--- Server1 recovers ---")
    s1.simulate_recovery()          # is_alive = True, processes any pending queue

    # --- Trigger anti-entropy sync: pull missing messages from donors ---
    print("\n--- Running anti-entropy sync for Server1 ---")
    sync_manager.sync_server(s1)

    # --- Verify Server1 is now fully caught up ---
    print("\n--- Verifying Server1 after sync ---")
    for msg_id in ["msg_001", "msg_002", "msg_003", "msg_004", "msg_005"]:
        result = s1.retrieve_message(msg_id)

    # --- Print the full sync report ---
    sync_manager.print_report()