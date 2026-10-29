#!/usr/bin/env python3
"""
Mininet Network Topology for SDN Health Monitor Network
Deliverable 2 - Role 2: SDN & Control Plane Engineer

Architecture:
  - Supports Resilient Multi-Switch Triangle Topology (default):
      s1: Aggregates Agent 1 (h2) and Agent 2 (h3)
      s2: Connects Central Server (h1)
      s3: Redundant backup switch providing alternate path

      Primary Path:   s1 (Port 2) <======================> s2 (Port 1)
      Alternate Path: s1 (Port 3) <===> s3 <===> s2 (Port 2)

  - Port Mapping:
      s1: Port 1 -> h2 (Agent 1)
          Port 2 -> s2 (Primary trunk)
          Port 3 -> s3 (Backup detour)
          Port 4 -> h3 (Agent 2)
      s2: Port 1 -> s1 (Primary trunk)
          Port 2 -> s3 (Backup detour)
          Port 3 -> h1 (Server)
      s3: Port 1 -> s1 (Backup link)
          Port 2 -> s2 (Backup link)
          Port 3 -> h2 (Dual-homed backup link for Agent 1)

  - Also supports single-switch mode via --single argument.
"""

import sys
import argparse
from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel, info

def create_resilient_topology(controller_ip='127.0.0.1', controller_port=6653):
    """
    Creates a resilient 3-switch topology with redundant paths for dynamic rerouting.
    """
    net = Mininet(controller=RemoteController, switch=OVSKernelSwitch, link=TCLink)

    info('*** Adding POX SDN Remote Controller\n')
    c0 = net.addController('c0', controller=RemoteController, ip=controller_ip, port=controller_port)

    info('*** Adding Switches (s1, s2, s3)\n')
    s1 = net.addSwitch('s1', dpid='0000000000000001')
    s2 = net.addSwitch('s2', dpid='0000000000000002')
    s3 = net.addSwitch('s3', dpid='0000000000000003')

    info('*** Adding End Hosts\n')
    # Server on h1
    server = net.addHost('h1', ip='10.0.0.1/24', mac='00:00:00:00:00:01')
    # Agents on h2 and h3
    agent1 = net.addHost('h2', ip='10.0.0.2/24', mac='00:00:00:00:00:02')
    agent2 = net.addHost('h3', ip='10.0.0.3/24', mac='00:00:00:00:00:03')

    info('*** Wiring Links and Establishing Specific Port Mappings\n')
    # Host links
    # s1 Port 1 <-> h2-eth0
    net.addLink(agent1, s1, port1=0, port2=1)
    # s1 Port 4 <-> h3-eth0
    net.addLink(agent2, s1, port1=0, port2=4)
    # s2 Port 3 <-> h1-eth0 (Server)
    net.addLink(server, s2, port1=0, port2=3)

    # Dual-homed backup link for h2 to s3 (Port 3)
    net.addLink(agent1, s3, port1=1, port2=3)

    # Inter-switch links:
    # PRIMARY LINK: s1 (Port 2) <=====> s2 (Port 1) [100 Mbps]
    net.addLink(s1, s2, port1=2, port2=1, bw=100)

    # DETOUR BACKUP LINKS:
    # s1 (Port 3) <=====> s3 (Port 1) [100 Mbps]
    net.addLink(s1, s3, port1=3, port2=1, bw=100)
    # s3 (Port 2) <=====> s2 (Port 2) [100 Mbps]
    net.addLink(s3, s2, port1=2, port2=2, bw=100)

    info('*** Starting Network\n')
    net.start()

    print("\n" + "="*70)
    print("   SDN HEALTH MONITOR NETWORK - RESILIENT TOPOLOGY ACTIVE")
    print("="*70)
    print(f"  Controller: POX listening on {controller_ip}:{controller_port}")
    print("  Topology Design:")
    print("    h2 (Agent 1: 10.0.0.2) --- [s1] ===== Primary Trunk ===== [s2] --- h1 (Server: 10.0.0.1)")
    print("    h3 (Agent 2: 10.0.0.3) ---   |                              |")
    print("                                 \\===== Backup Detour (s3) =====/")
    print("="*70 + "\n")

    info('*** Running Mininet CLI\n')
    CLI(net)

    info('*** Stopping Network\n')
    net.stop()

def create_single_topology(controller_ip='127.0.0.1', controller_port=6653):
    """
    Creates single-switch topology (compatible with Deliverable 1).
    """
    net = Mininet(controller=RemoteController, switch=OVSKernelSwitch)

    info('*** Adding POX SDN Remote Controller\n')
    c0 = net.addController('c0', controller=RemoteController, ip=controller_ip, port=controller_port)

    info('*** Adding Hosts\n')
    server = net.addHost('h1', ip='10.0.0.1/24')
    agent1 = net.addHost('h2', ip='10.0.0.2/24')
    agent2 = net.addHost('h3', ip='10.0.0.3/24')

    info('*** Adding Switch\n')
    s1 = net.addSwitch('s1')

    info('*** Creating Links\n')
    net.addLink(server, s1)
    net.addLink(agent1, s1)
    net.addLink(agent2, s1)

    info('*** Starting Network\n')
    net.start()

    info('*** Running Mininet CLI\n')
    CLI(net)

    info('*** Stopping Network\n')
    net.stop()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="SDN Health Monitor Topology")
    parser.add_argument('--single', action='store_true', help="Run in single switch mode")
    parser.add_argument('--ip', type=str, default='127.0.0.1', help="Controller IP (default: 127.0.0.1)")
    parser.add_argument('--port', type=int, default=6653, help="Controller Port (default: 6653)")
    args = parser.parse_args()

    setLogLevel('info')
    if args.single:
        create_single_topology(controller_ip=args.ip, controller_port=args.port)
    else:
        create_resilient_topology(controller_ip=args.ip, controller_port=args.port)
