import time

from server import Server
from failure_detector import FailureDetector
from recovery_sync import RecoverySyncManager
from replication_manager import ReplicationManager
from raft_consensus import RaftCluster


def divider(title):
    print("\n" + "=" * 70)
    print(f"   {title}")
    print("=" * 70)


def print_raft_status(cluster):
    status = cluster.cluster_status()
    print(f"[RAFT] Leader: {status['leader']}")
    for node_name, node_status in status["nodes"].items():
        print(
            f"  - {node_name}: role={node_status['role']} term={node_status['term']} "
            f"alive={node_status['alive']} commit_index={node_status['commit_index']} "
            f"log_length={node_status['log_length']}"
        )


def record_legacy_replication_view(replicator, cluster, message_id):
    holders = cluster.get_committed_holders(message_id)
    replicator.replication_map[message_id] = holders
    replicator.total_originals += 1
    replicator.total_copies += len(holders)
    return holders


def refresh_legacy_replication_view(replicator, cluster, message_ids):
    replicator.replication_map = {}
    replicator.total_originals = 0
    replicator.total_copies = 0
    for message_id in message_ids:
        if not message_id:
            continue
        holders = cluster.get_committed_holders(message_id)
        replicator.replication_map[message_id] = holders
        replicator.total_originals += 1
        replicator.total_copies += len(holders)


def commit_message(cluster, replicator, sender, content):
    result = cluster.append_message(sender=sender, content=content)
    if result.get("ok"):
        holders = record_legacy_replication_view(replicator, cluster, result["message_id"])
        print(
            f"[CLIENT] '{content}' committed as {result['message_id']} via {result['leader']} "
            f"(term={result['term']}, index={result['log_index']}, replicas={holders})"
        )
    else:
        print(f"[CLIENT] Write failed for '{content}' -> {result}")
    return result


def main():
    divider("PHASE 1: Booting the NexusChat cluster")

    s1 = Server("Server1")
    s2 = Server("Server2")
    s3 = Server("Server3")
    s4 = Server("Server4")
    all_servers = [s1, s2, s3, s4]

    detector = FailureDetector(all_servers, timeout_seconds=2)
    sync_mgr = RecoverySyncManager(all_servers)
    replicator = ReplicationManager(all_servers, replication_factor=3)
    raft_cluster = RaftCluster(all_servers, election_timeout_range=(0.8, 1.5), heartbeat_interval=0.25)
    raft_cluster.start_background_processing(interval=0.08)

    for s in all_servers:
        s.send_heartbeat()
    detector.check_all_servers()

    leader = raft_cluster.elect_leader_blocking()
    print(f"[RAFT] Initial leader elected: {leader.server.name if leader else 'None'}")
    print_raft_status(raft_cluster)

    divider("PHASE 2: Normal operation — consensus-backed message commits")
    r1 = commit_message(raft_cluster, replicator, "Alice", "Hello from Alice!")
    r2 = commit_message(raft_cluster, replicator, "Bob", "Meeting at 3pm")
    r3 = commit_message(raft_cluster, replicator, "Monitor", "Server check OK")
    print_raft_status(raft_cluster)

    divider("PHASE 3: Leader crash — detection + Raft re-election")
    old_leader = raft_cluster.get_leader()
    if old_leader is not None:
        print(f"[TEST] Crashing current leader: {old_leader.server.name}")
        raft_cluster.crash_node(old_leader.server.name)
    time.sleep(2.5)

    for server in all_servers:
        if server.is_alive:
            server.send_heartbeat()
    detector.check_all_servers()

    new_leader = raft_cluster.elect_leader_blocking()
    print(f"[RAFT] New leader after crash: {new_leader.server.name if new_leader else 'None'}")
    post_failover = commit_message(raft_cluster, replicator, "Alice", "System still running after leader crash")
    print_raft_status(raft_cluster)

    divider("PHASE 4: Follower recovery — Raft catch-up + anti-entropy sync")
    crashed_name = old_leader.server.name if old_leader else None
    if crashed_name is not None:
        raft_cluster.recover_node(crashed_name)
        recovered_server = next(s for s in all_servers if s.name == crashed_name)
        recovered = raft_cluster.force_catch_up(crashed_name)
        transferred = sync_mgr.sync_server(recovered_server)
        recovered_server.send_heartbeat()
        detector.check_all_servers()
        print(f"[RECOVERY] {crashed_name} raft catch-up success={recovered}, anti-entropy transferred={transferred}")
    print_raft_status(raft_cluster)

    divider("PHASE 5: Network partition — majority continues, minority catches up later")
    current_leader = raft_cluster.get_leader()
    follower_candidates = [name for name in raft_cluster.nodes if current_leader and name != current_leader.server.name]
    partition_target = follower_candidates[-1] if follower_candidates else None
    during_partition = {}
    if partition_target in raft_cluster.nodes:
        majority_nodes = [name for name in raft_cluster.nodes if name != partition_target]
        raft_cluster.partition(partition_target, majority_nodes)
        print(f"[TEST] Isolated {partition_target} from the rest of the cluster.")
        during_partition = commit_message(raft_cluster, replicator, "Bob", "Majority side still accepts writes")
        print_raft_status(raft_cluster)

        raft_cluster.heal_all_partitions()
        healed = raft_cluster.force_catch_up(partition_target)
        transferred = sync_mgr.sync_server(next(s for s in all_servers if s.name == partition_target))
        print(f"[PARTITION] {partition_target} healed. raft catch-up success={healed}, anti-entropy transferred={transferred}")

    divider("PHASE 6: Retrieve committed messages")
    committed_ids = [
        r1.get("message_id"),
        r2.get("message_id"),
        r3.get("message_id"),
        post_failover.get("message_id") if isinstance(post_failover, dict) else None,
        during_partition.get("message_id") if isinstance(during_partition, dict) else None,
    ]
    committed_ids = [msg_id for msg_id in committed_ids if msg_id]
    refresh_legacy_replication_view(replicator, raft_cluster, committed_ids)
    for msg_id in committed_ids:
        print(f"[VERIFY] Reading {msg_id} through replication layer view...")
        replicator.retrieve_message(msg_id)

    divider("PHASE 7: Final reports")
    detector.print_report()
    replicator.print_storage_report()
    sync_mgr.print_report()
    print_raft_status(raft_cluster)

    raft_cluster.stop_background_processing()
    print("NexusChat simulation complete.\n")


if __name__ == "__main__":
    main()