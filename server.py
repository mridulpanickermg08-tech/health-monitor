#!/usr/bin/env python3
"""
Central UDP Socket Server with Missing-Heartbeat Detector & Priority Alerts
SDN-Socket Programming Mini Project - Deliverable 2

Features:
- Listens for raw UDP socket datagrams on port 5005.
- Decodes JSON payload containing metrics, priority tags, and process lists.
- Background heartbeat watcher detecting lost/crashed nodes (missing heartbeat > 3s).
- Logs high-priority socket alerts with visual indicators.
"""

import socket
import json
import time
import threading

LISTEN_IP = "0.0.0.0"
LISTEN_PORT = 5005
HEARTBEAT_TIMEOUT = 3.0  # Alert if no packet received within 3 seconds

# agent_id -> {"last_seen": timestamp, "ip": ip, "status": "UP"}
active_nodes = {}
nodes_lock = threading.Lock()

def heartbeat_monitor():
    """
    Background worker thread that continually checks for missing heartbeats.
    """
    while True:
        time.sleep(1.0)
        now = time.time()
        with nodes_lock:
            for agent_id, data in active_nodes.items():
                elapsed = now - data["last_seen"]
                if elapsed > HEARTBEAT_TIMEOUT and data["status"] == "UP":
                    data["status"] = "DOWN"
                    print("\n" + "!"*70)
                    print(f"⚠️  [NODE FAILURE DETECTED] Heartbeat lost for {agent_id} ({data['ip']})!")
                    print(f"    Elapsed: {round(elapsed, 1)}s without response (Threshold: {HEARTBEAT_TIMEOUT}s)")
                    print("!"*70 + "\n")
                elif elapsed <= HEARTBEAT_TIMEOUT and data["status"] == "DOWN":
                    data["status"] = "UP"
                    print(f"\n✅ [NODE RECOVERED] {agent_id} ({data['ip']}) resumed heartbeats.\n")

def start_server():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((LISTEN_IP, LISTEN_PORT))

    print("="*70)
    print(f"  SDN SOCKET SERVER ACTIVE ON UDP {LISTEN_IP}:{LISTEN_PORT}")
    print(f"  Heartbeat Failure Threshold: {HEARTBEAT_TIMEOUT} seconds")
    print("="*70)

    # Start background failure detector
    watcher = threading.Thread(target=heartbeat_monitor, daemon=True)
    watcher.start()

    while True:
        try:
            data, addr = sock.recvfrom(4096)
            now = time.time()
            metrics = json.loads(data.decode('utf-8'))

            agent_id = metrics.get('agent_id', 'Unknown')
            seq = metrics.get('seq', 0)
            cpu = metrics.get('cpu_percent', 0.0)
            ram = metrics.get('memory_percent', 0.0)
            priority = metrics.get('priority', 'NORMAL')
            top_procs = metrics.get('top_processes', [])

            # Update heartbeat tracker
            with nodes_lock:
                active_nodes[agent_id] = {
                    "last_seen": now,
                    "ip": addr[0],
                    "status": "UP"
                }

            # Format process string
            proc_summary = ", ".join([f"{p['name']}({p['cpu']}%)" for p in top_procs[:3]])

            if priority == "HIGH" or cpu > 80.0:
                print(f"🔥 [CRITICAL ALERT] [{addr[0]}] {agent_id} (Seq: {seq}) | CPU: {cpu}% [HIGH PRIORITY] | RAM: {ram}%")
                if proc_summary:
                    print(f"    └── Top Processes: [{proc_summary}]")
            else:
                print(f"🔹 [HEALTH UPDATE] [{addr[0]}] {agent_id} (Seq: {seq}) | CPU: {cpu}% | RAM: {ram}% | Top: [{proc_summary}]")

        except json.JSONDecodeError:
            print(f"Received malformed data from {addr}")
        except Exception as e:
            print(f"Error handling packet: {e}")

if __name__ == "__main__":
    start_server()
