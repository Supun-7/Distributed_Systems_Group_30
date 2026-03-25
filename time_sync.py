import time
import threading
from server import Server


class ClockSkewSimulator:
    """Simulates different clock offsets per server (like real distributed systems)."""

    def __init__(self, servers):
        # Each server gets a small random offset in seconds (-2 to +2)
        import random
        self.offsets = {s.name: random.uniform(-2.0, 2.0) for s in servers}
        print("[TIME] Clock offsets assigned:")
        for name, offset in self.offsets.items():
            print(f"  {name}: {offset:+.3f}s")

    def get_local_time(self, server_name):
        """Return the local (skewed) time for this server."""
        return time.time() + self.offsets.get(server_name, 0)

    def get_skew(self, server_name):
        return self.offsets.get(server_name, 0)


class TimeSyncManager:

    def __init__(self, servers):
        self.servers = servers
        self.clock_sim = ClockSkewSimulator(servers)
        self.sync_log = []             # history of sync events
        self.message_timestamps = {}   # message_id → corrected timestamp

        print(f"\n[TIME] TimeSyncManager ready. Watching {len(servers)} server(s).\n")

    # ── TASK 1 ────────────────────────────────────────────────────────────────
    # Implement Berkeley Algorithm (simplified).
    # Steps:
    #   1. Get local time from each alive server using clock_sim.get_local_time()
    #   2. Calculate the MASTER time = average of all alive server times
    #   3. For each server, calculate the adjustment = master_time - server_local_time
    #   4. Print each server's skew and required adjustment
    #   5. Log the sync event in self.sync_log
    #   6. Return the master_time
    #
    # Theory: Berkeley Algorithm — one master collects times from all nodes,
    # calculates average, sends each node its required adjustment.
    def synchronize(self):
        # Step 1: Get local time from each alive server
        alive_servers = [s for s in self.servers if s.is_alive]

        if not alive_servers:
            print("[TIME] No alive servers to synchronise.")
            return time.time()

        print(f"[TIME] Synchronising {len(alive_servers)} servers...")

        server_times = {}
        for server in alive_servers:
            local_time = self.clock_sim.get_local_time(server.name)
            server_times[server.name] = local_time

        # Step 2: Calculate master time = average of all alive server times
        master_time = sum(server_times.values()) / len(server_times)

        # Step 3 & 4: Calculate adjustment for each server and print
        for server in alive_servers:
            local_t = server_times[server.name]
            adjustment = master_time - local_t
            readable = time.strftime('%H:%M:%S', time.localtime(local_t))
            print(f"  {server.name} local: {readable}  adjustment: {adjustment:+.3f}s")

        # Step 5: Log the sync event
        self.sync_log.append({
            "timestamp": time.time(),
            "master_time": master_time,
            "server_count": len(alive_servers),
            "adjustments": {
                name: master_time - t for name, t in server_times.items()
            }
        })

        # Step 6: Return the master time
        return master_time
    # ── TASK 2 ────────────────────────────────────────────────────────────────
    # Assign a corrected timestamp to a message.
    # Steps:
    #   1. Get the server's local time using clock_sim.get_local_time(server_name)
    #   2. Apply a correction if needed (use get_skew to find drift)
    #   3. Store in self.message_timestamps[message_id]
    #   4. Return the corrected timestamp
    #
    # Theory: Logical clocks — ensure message timestamps are consistent
    # even if server clocks have drifted.
    def timestamp_message(self, message_id, server_name):
        # Step 1: Get the server's local (skewed) time
        local_time = self.clock_sim.get_local_time(server_name)

        # Step 2: Apply correction — subtract the skew offset to get true time
        skew = self.clock_sim.get_skew(server_name)
        corrected_timestamp = local_time - skew  # corrected = true wall-clock time

        # Step 3: Store the corrected timestamp
        self.message_timestamps[message_id] = corrected_timestamp

        readable = time.strftime('%H:%M:%S', time.localtime(corrected_timestamp))
        print(f"[TIME] Message '{message_id}' from {server_name} timestamped: {readable} (skew corrected: {-skew:+.3f}s)")

        # Step 4: Return the corrected timestamp
        return corrected_timestamp

