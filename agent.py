#!/usr/bin/env python3
"""
Low-Level UDP Socket Client Node (Periodic Heartbeat & Process Metrics)
SDN-Socket Programming Mini Project - Deliverable 2

Features:
- Periodic 1-second heartbeat stream over raw UDP sockets (port 5005).
- Collects system metrics (overall CPU, memory, timestamp) and process statistics.
- Dynamically assigns "priority": "HIGH" in JSON socket payload when CPU exceeds 80%
  (or when --simulate-high-cpu is passed for SDN flow prioritization testing).
"""

import socket
import psutil
import json
import time
import sys
import argparse

DEFAULT_SERVER_IP = "10.0.0.1"
DEFAULT_SERVER_PORT = 5005
DEFAULT_AGENT_ID = "Agent-Node-1"
HEARTBEAT_INTERVAL = 1.0  # 1-second heartbeat mechanism

def get_top_processes(count=3):
    """
    Returns the top N CPU-consuming processes on the host.
    """
    procs = []
    for proc in psutil.process_iter(['pid', 'name', 'cpu_percent']):
        try:
            info = proc.info
            cpu = info.get('cpu_percent') or 0.0
            procs.append({
                "pid": info.get('pid'),
                "name": info.get('name') or "unknown",
                "cpu": round(cpu, 1)
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    procs.sort(key=lambda x: x['cpu'], reverse=True)
    return procs[:count]

def start_agent(server_ip=DEFAULT_SERVER_IP, server_port=DEFAULT_SERVER_PORT,
                agent_id=DEFAULT_AGENT_ID, simulate_high_cpu=False, spike_after=None):
    """
    Runs the UDP telemetry agent heartbeat loop.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    print(f"[{agent_id}] Initialized. Sending 1s telemetry heartbeats to {server_ip}:{server_port}...")
    if simulate_high_cpu:
        print(f"[{agent_id}] *** HIGH CPU SIMULATION ENABLED (CPU forced to 92%, priority=HIGH) ***")

    # Prime psutil cpu measurement
    psutil.cpu_percent(interval=None)

    seq_num = 0
    start_time = time.time()

    try:
        while True:
            seq_num += 1
            now = time.time()
            elapsed = now - start_time

            # Gather system metrics
            real_cpu = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory().percent
            top_procs = get_top_processes(count=3)

            # Determine whether CPU exceeds threshold
            is_high = simulate_high_cpu or (spike_after and elapsed >= spike_after) or (real_cpu > 80.0)
            cpu_val = 92.5 if is_high else real_cpu
            priority = "HIGH" if is_high else "NORMAL"

            payload_data = {
                "agent_id": agent_id,
                "seq": seq_num,
                "timestamp": round(now, 3),
                "cpu_percent": round(cpu_val, 1),
                "memory_percent": round(mem, 1),
                "priority": priority,
                "top_processes": top_procs
            }

            raw_payload = json.dumps(payload_data).encode('utf-8')
            sock.sendto(raw_payload, (server_ip, server_port))

            marker = "🔥 [HIGH PRIORITY]" if priority == "HIGH" else "🔹 [NORMAL]"
            top_str = ", ".join([f"{p['name']}({p['cpu']}%)" for p in top_procs])
            print(f"{marker} Seq: {seq_num} | CPU: {payload_data['cpu_percent']}% | RAM: {payload_data['memory_percent']}% | Top: [{top_str}]")

            time.sleep(HEARTBEAT_INTERVAL)

    except KeyboardInterrupt:
        print(f"\n[{agent_id}] Telemetry agent terminating gracefully.")
    finally:
        sock.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SDN Telemetry Health Agent")
    parser.add_argument("--server", type=str, default=DEFAULT_SERVER_IP, help="Monitoring Server IP")
    parser.add_argument("--port", type=int, default=DEFAULT_SERVER_PORT, help="Monitoring Server Port")
    parser.add_argument("--id", type=str, default=DEFAULT_AGENT_ID, help="Agent Node Identifier")
    parser.add_argument("--simulate-high-cpu", action="store_true", help="Force priority: HIGH to test SDN QoS flow rules")
    parser.add_argument("--spike-after", type=float, default=None, help="Automatically trigger high CPU spike after N seconds")

    args = parser.parse_args()
    start_agent(server_ip=args.server, server_port=args.port, agent_id=args.id,
                simulate_high_cpu=args.simulate_high_cpu, spike_after=args.spike_after)
