import time

from server import Server
from failure_detector import FailureDetector
from replication import ReplicationManager
from failover import FailoverManager
from recovery_sync import RecoverySyncManager

def divider(title):
    print("\n" + "=" * 60)
    print(f"   {title}")
    print("=" * 60)


def main():

    # ── PHASE 1: Boot the cluster ─────────────────────────────────────────────
    divider("PHASE 1: Booting the NexusChat cluster")

    s1 = Server("Server1")
    s2 = Server("Server2")
    s3 = Server("Server3")
    s4 = Server("Server4")
    all_servers = [s1, s2, s3, s4]

    # All components share the same server list
    detector  = FailureDetector(all_servers, timeout_seconds=2)
    replicator = ReplicationManager(all_servers, replication_factor=3)
    failover   = FailoverManager(all_servers, detector)
    sync_mgr   = RecoverySyncManager(all_servers)

    # All servers start alive — send initial heartbeats
    for s in all_servers:
        s.send_heartbeat()
    detector.check_all_servers()

    # ── PHASE 2: Normal operation — replicate messages across the cluster ─────
    divider("PHASE 2: Normal operation — sending messages")

    id1 = replicator.replicate_message("Hello from Alice!", sender="Alice")
    id2 = replicator.replicate_message("Meeting at 3pm",   sender="Bob")
    id3 = replicator.replicate_message("Server check OK",  sender="Monitor")

    print("\n--- Retrieving messages (all servers alive) ---")
    replicator.retrieve_message(id1)
    replicator.retrieve_message(id2)

    # ── PHASE 3: Server1 crashes — failure detection + automatic failover ─────
    divider("PHASE 3: Server1 crashes — detection + failover")

    s1.simulate_crash()
    print("\nWaiting for failure detector to notice silence...")
    time.sleep(3)

    # Only alive servers send heartbeats
    for s in [s2, s3, s4]:
        s.send_heartbeat()
    detector.check_all_servers()       # Should flag Server1 as suspected failed

    # Failover: messages now route around the failed server
    print("\n--- Sending messages after Server1 crash (auto-failover) ---")
    failover.send_with_failover("msg_live_001", "System still running!", sender="Alice")
    failover.send_with_failover("msg_live_002", "Failover confirmed",    sender="Bob")

    # Replication also skips the crashed server automatically
    id4 = replicator.replicate_message("Post-crash message", sender="Monitor")

    # ── PHASE 4: Server1 recovers — anti-entropy sync ─────────────────────────
    divider("PHASE 4: Server1 recovers — anti-entropy sync")

    s1.simulate_recovery()

    print(f"\n  Before sync — Server1 has: {list(s1.message_store.keys())}")
    print(f"  Server2 has:               {list(s2.message_store.keys())}")

    # Pull all missed messages from healthy donors
    sync_mgr.sync_server(s1)

    print(f"\n  After sync — Server1 has: {list(s1.message_store.keys())}")

    # ── PHASE 5: Retrieve messages — even after crash + recovery ─────────────
    divider("PHASE 5: Verifying data integrity after recovery")

    print("--- Retrieving all original messages ---")
    for msg_id in [id1, id2, id3, id4]:
        replicator.retrieve_message(msg_id)

    # ── PHASE 6: Final reports from all components ────────────────────────────
    divider("PHASE 6: Full system reports")

    detector.print_report()
    replicator.print_storage_report()
    failover.print_report()
    sync_mgr.print_report()

    print("NexusChat simulation complete.\n")


if __name__ == "__main__":
    main()