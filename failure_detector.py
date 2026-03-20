import time
from server import Server  # Import Server class from server.py so we can monitor Server objects

class FailureDetector:

    def __init__(self, servers, timeout_seconds=5):
        self.servers = servers                  # List of Server objects to monitor
        self.timeout = timeout_seconds          # Max seconds allowed between heartbeats
        self.suspected_failures = set()         # Set of server names currently suspected as failed
        self.failure_log = []                   # History list of all failure events recorded

        print(f"[DETECTOR] Watching {len(servers)} server(s).")
        print(f"[DETECTOR] Timeout set to {timeout_seconds} seconds.\n")

    def check_all_servers(self):
        newly_failed = []               # Tracks servers that fail for the FIRST TIME in this check
        current_time = time.time()      # Capture current time once — used for all comparisons below

        print(f"[DETECTOR] Running check at {time.strftime('%H:%M:%S')}...")
        print("-" * 50)

        for server in self.servers:     # Loop through every server we are responsible for monitoring

            # Calculate how many seconds have passed since this server last sent a heartbeat
            time_since_heartbeat = current_time - server.last_heartbeat

            print(f"  {server.name}: last heartbeat {time_since_heartbeat:.1f}s ago")

            if time_since_heartbeat > self.timeout:     # Server has been silent longer than allowed

                if server.name not in self.suspected_failures:  # Only act if this is a NEW failure (avoid duplicates)

                    self.suspected_failures.add(server.name)    # Mark this server as suspected failed
                    newly_failed.append(server.name)            # Add to this round's newly failed list

                    # Record the full failure event details into the log history
                    self.failure_log.append({
                        "server": server.name,
                        "detected_at": time.strftime('%H:%M:%S'),
                        "silent_for": round(time_since_heartbeat, 2)
                    })

                    print(f"  ⚠️  ALERT: {server.name} silent for {time_since_heartbeat:.1f}s — SUSPECTED FAILED!")

            else:   # Server is responding within the timeout window — it is alive

                if server.name in self.suspected_failures:          # Was it previously suspected?
                    self.suspected_failures.discard(server.name)    # Remove it — it has recovered
                    print(f"  ✅ {server.name} recovered — removed from suspect list.")

        print("-" * 50)

        # Final summary for this check round
        if not newly_failed:
            print("[DETECTOR] All servers OK.\n")
        else:
            print(f"[DETECTOR] New failures detected: {newly_failed}\n")

        return newly_failed     # Return list so other parts of system can react to new failures

    def get_alive_servers(self):
        # Return only servers whose names are NOT in the suspected failures set
        alive = [s for s in self.servers if s.name not in self.suspected_failures]
        return alive

    def get_failed_servers(self):
        # Return only servers whose names ARE in the suspected failures set
        failed = [s for s in self.servers if s.name in self.suspected_failures]
        return failed

    def is_server_failed(self, server_name):
        # Quick True/False check — is this specific server currently suspected as failed?
        return server_name in self.suspected_failures

    def print_report(self):
        print("\n" + "=" * 50)
        print("       FAILURE DETECTION REPORT")
        print("=" * 50)

        if not self.failure_log:    # No entries in log means no failures happened
            print("  No failures detected.")
        else:
            print(f"  Total failures detected: {len(self.failure_log)}\n")

            # Loop through each failure event with a counter starting at 1
            for i, event in enumerate(self.failure_log, 1):
                print(f"  [{i}] Server:     {event['server']}")
                print(f"       Detected:   {event['detected_at']}")
                print(f"       Silent for: {event['silent_for']} seconds")
                print()

        # Convert set to list just for clean printing
        currently_failed = list(self.suspected_failures)
        print(f"  Currently suspected: {currently_failed if currently_failed else 'None'}")
        print("=" * 50 + "\n")


if __name__ == "__main__":

    # --- Setup ---
    s1 = Server("Server1")
    s2 = Server("Server2")
    s3 = Server("Server3")
    servers = [s1, s2, s3]

    # Create detector — using 2s timeout so we don't wait long during testing
    detector = FailureDetector(servers, timeout_seconds=2)

    # --- Round 1: Normal operation, all servers alive ---
    print("--- Round 1: All servers alive ---")
    for s in servers:
        s.send_heartbeat()          # Each server updates its last_heartbeat timestamp
    detector.check_all_servers()   # All should pass — no one has been silent

    # --- Simulate Server2 going down ---
    print("--- Server2 CRASHES ---")
    s2.simulate_crash()            # Sets s2.is_alive = False, stops sending heartbeats

    # Wait longer than the timeout so the detector can actually catch the silence
    print("Waiting 3 seconds...")
    time.sleep(3)

    # --- Round 2: Only Server1 and Server3 send heartbeats ---
    print("--- Round 2: After crash ---")
    s1.send_heartbeat()            # Server1 still alive — updates its timestamp
    s3.send_heartbeat()            # Server3 still alive — updates its timestamp
    # Server2 sends nothing — its last_heartbeat stays old → will exceed timeout
    detector.check_all_servers()   # Should detect Server2 as failed

    # Ask detector which servers are currently considered alive
    alive = detector.get_alive_servers()
    print(f"Alive servers: {[s.name for s in alive]}")

    # Print the complete failure history report
    detector.print_report()