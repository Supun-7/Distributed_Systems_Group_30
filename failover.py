import time                               # For timestamps and readable time formatting
from server import Server                 # Server objects to redirect messages to/from
from failure_detector import FailureDetector  # To know which servers are currently down

class FailoverManager:

    def __init__(self, servers, detector):
        self.servers = servers              # All server nodes in the system
        self.detector = detector            # FailureDetector to check who is alive
        self.primary = servers[0]           # First server starts as the primary
        self.failover_log = []              # History of every failover event that happened

        print(f"[FAILOVER] Manager started.")
        print(f"[FAILOVER] Primary server: {self.primary.name}")
        print(f"[FAILOVER] Backups: {[s.name for s in servers[1:]]}\n")

    def get_active_server(self):
        # ── Step 1: Check if the current primary is still alive ──────────────
        # We ask the detector — not the server itself — because a crashed server
        # cannot report its own failure (it's down). The detector is the external observer.

        if not self.detector.is_server_failed(self.primary.name):
            # Primary is healthy — no failover needed, return it directly
            return self.primary

        # ── Step 2: Primary is down — find the first healthy backup ──────────
        print(f"[FAILOVER] ⚠️  Primary '{self.primary.name}' is DOWN — searching for backup...")

        for server in self.servers:
            if server.name == self.primary.name:
                continue                    # Skip the failed primary — we already know it's down

            if not self.detector.is_server_failed(server.name):
                # Found a healthy backup — promote it to primary
                old_primary = self.primary.name
                self.primary = server       # Update internal primary reference

                # Record this failover event for the report
                self.failover_log.append({
                    "from": old_primary,
                    "to": server.name,
                    "at": time.strftime('%H:%M:%S')
                })

                print(f"[FAILOVER] ✅ Switched primary: '{old_primary}' → '{server.name}'")
                return server               # Return the newly promoted primary

        # ── Step 3: All servers are down — total cluster failure ─────────────
        print("[FAILOVER] ❌ ALL servers are DOWN — no failover possible!")
        return None                         # Caller must handle None (cannot send message)

    def send_with_failover(self, message_id, content, sender="unknown"):
        # This is the main entry point — callers use this instead of calling
        # server.store_message() directly. The failover logic is hidden inside here.

        print(f"\n[FAILOVER] Sending '{message_id}' from '{sender}'...")

        # Ask the manager which server should receive this message right now
        target = self.get_active_server()

        if target is None:
            # No server available — message is lost (in a real system we'd queue it externally)
            print(f"[FAILOVER] ❌ Cannot deliver '{message_id}' — no servers available.")
            return False

        # Deliver the message to whichever server was selected (primary or promoted backup)
        success = target.store_message(message_id, content, sender)

        if success:
            print(f"[FAILOVER] ✅ '{message_id}' delivered to '{target.name}'")
        else:
            # The target was alive when selected but failed mid-write (race condition)
            # In production we'd retry — for simulation we just report it
            print(f"[FAILOVER] ❌ Delivery to '{target.name}' failed mid-write.")

        return success

    def print_report(self):
        print("\n" + "=" * 50)
        print("         FAILOVER EVENT REPORT")
        print("=" * 50)

        if not self.failover_log:
            print("  No failovers occurred — primary stayed healthy.")
        else:
            print(f"  Total failovers: {len(self.failover_log)}\n")

            for i, event in enumerate(self.failover_log, 1):
                print(f"  [{i}] Failed:    {event['from']}")
                print(f"       Promoted:  {event['to']}")
                print(f"       At:        {event['at']}")
                print()

        print(f"  Current primary: {self.primary.name}")
        alive = self.detector.get_alive_servers()
        print(f"  Alive servers:   {[s.name for s in alive]}")
        print("=" * 50 + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# DEMO — Run this file directly to see failover in action
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":

    # --- Setup: 3 servers ---
    s1 = Server("Server1")
    s2 = Server("Server2")
    s3 = Server("Server3")
    servers = [s1, s2, s3]

    # Create a failure detector with a 2s timeout (short for demo purposes)
    detector = FailureDetector(servers, timeout_seconds=2)

    # Create the failover manager — Server1 starts as primary
    fm = FailoverManager(servers, detector)

    # --- Round 1: Normal operation — primary is Server1 ---
    print("--- Round 1: Normal operation ---")
    for s in servers:
        s.send_heartbeat()              # All servers prove they are alive
    detector.check_all_servers()       # Detector confirms all OK

    fm.send_with_failover("msg_001", "Hello from Alice!", sender="Alice")

    # --- Crash Server1 (the primary) ---
    print("\n--- Server1 (primary) CRASHES ---")
    s1.simulate_crash()                # Server1 goes down — stops sending heartbeats

    # Wait long enough for the detector to notice the silence
    print("Waiting 3 seconds for detector to notice...")
    time.sleep(3)

    # Only surviving servers send heartbeats
    s2.send_heartbeat()
    s3.send_heartbeat()
    detector.check_all_servers()       # Detector marks Server1 as suspected failed

    # --- Round 2: Send a message — should automatically failover to Server2 ---
    print("\n--- Round 2: Sending after primary crash ---")
    fm.send_with_failover("msg_002", "Meeting at 3pm", sender="Bob")
    fm.send_with_failover("msg_003", "System check OK", sender="Monitor")

    # --- Crash Server2 as well — should failover again to Server3 ---
    print("\n--- Server2 (new primary) CRASHES ---")
    s2.simulate_crash()

    print("Waiting 3 seconds...")
    time.sleep(3)

    s3.send_heartbeat()
    detector.check_all_servers()

    print("\n--- Round 3: Sending after second crash ---")
    fm.send_with_failover("msg_004", "Last server standing!", sender="Alice")

    # --- Print full failover history ---
    fm.print_report()
    detector.print_report()