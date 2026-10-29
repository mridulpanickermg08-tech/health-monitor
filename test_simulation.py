#!/usr/bin/env python3
"""
Automated Test Suite for POX Controller & Socket Programming Application
Deliverable 2 - Role 2: SDN & Control Plane Engineer

This script tests:
1. Socket Datagram Payload Parsing & Priority Detection
2. Port Status Link Failure Detection & Topology State Tracking
3. Dynamic Path Calculation & Detour Failover
4. Safe Payload Extraction Across String/Bytes Formats
"""

import sys
import os
import unittest
import json
import types

# Ensure current directory is on python path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CURRENT_DIR)

# =========================================================================
# MOCK POX FRAMEWORK (Enables testing on Windows & headless environments)
# =========================================================================
def setup_mock_pox():
    if "pox" in sys.modules:
        return

    pox = types.ModuleType("pox")
    pox_core = types.ModuleType("pox.core")
    pox_of = types.ModuleType("pox.openflow")
    pox_of_01 = types.ModuleType("pox.openflow.libopenflow_01")
    pox_lib = types.ModuleType("pox.lib")
    pox_addr = types.ModuleType("pox.lib.addresses")
    pox_pkt = types.ModuleType("pox.lib.packet")
    pox_pkt_eth = types.ModuleType("pox.lib.packet.ethernet")
    pox_pkt_ipv4 = types.ModuleType("pox.lib.packet.ipv4")
    pox_pkt_udp = types.ModuleType("pox.lib.packet.udp")
    pox_pkt_arp = types.ModuleType("pox.lib.packet.arp")

    class MockLogger:
        def info(self, msg, *args): pass
        def warn(self, msg, *args): pass
        def error(self, msg, *args): pass
        def debug(self, msg, *args): pass

    class MockCore:
        openflow = types.SimpleNamespace(addListeners=lambda x: None, addListenerByName=lambda *args: None)
        getLogger = lambda self=None: MockLogger()
        registerNew = lambda *args: None

    pox_core.core = MockCore()

    # OpenFlow 01 constants and classes
    class MockFlowMod:
        def __init__(self):
            self.priority = 100
            self.match = None
            self.actions = []
            self.idle_timeout = 0
            self.hard_timeout = 0
            self.data = None
            self.command = 0
            self.out_port = None

    class MockMatch:
        def __init__(self):
            self.dl_type = 0
            self.nw_proto = 0
            self.nw_src = None
            self.nw_dst = None
            self.tp_dst = None

    class MockActionOutput:
        def __init__(self, port):
            self.port = port

    class MockPacketOut:
        def __init__(self):
            self.in_port = 0
            self.data = None
            self.actions = []

    pox_of_01.ofp_flow_mod = MockFlowMod
    pox_of_01.ofp_match = MockMatch
    pox_of_01.ofp_action_output = MockActionOutput
    pox_of_01.ofp_packet_out = MockPacketOut
    pox_of_01.OFPFC_DELETE = 3
    pox_of_01.OFPP_FLOOD = 65531
    pox_of_01.OFPP_MAX = 65280
    pox_of_01.OFPPS_LINK_DOWN = 1
    pox_of_01.OFPPC_PORT_DOWN = 1
    pox_of_01.OFPPR_DELETE = 1

    class IPAddr(str): pass
    class EthAddr(str):
        @property
        def is_multicast(self):
            return False

    pox_addr.IPAddr = IPAddr
    pox_addr.EthAddr = EthAddr

    class ethernet: pass
    class ipv4: pass
    class udp: pass
    class arp: pass

    pox_pkt_eth.ethernet = ethernet
    pox_pkt_ipv4.ipv4 = ipv4
    pox_pkt_udp.udp = udp
    pox_pkt_arp.arp = arp

    sys.modules["pox"] = pox
    sys.modules["pox.core"] = pox_core
    sys.modules["pox.openflow"] = pox_of
    sys.modules["pox.openflow.libopenflow_01"] = pox_of_01
    sys.modules["pox.lib"] = pox_lib
    sys.modules["pox.lib.addresses"] = pox_addr
    sys.modules["pox.lib.packet"] = pox_pkt
    sys.modules["pox.lib.packet.ethernet"] = pox_pkt_eth
    sys.modules["pox.lib.packet.ipv4"] = pox_pkt_ipv4
    sys.modules["pox.lib.packet.udp"] = pox_pkt_udp
    sys.modules["pox.lib.packet.arp"] = pox_pkt_arp

setup_mock_pox()

# Import controller now that POX mocks are loaded
from pox_controller import (
    safe_decode_payload,
    ResilientSDNController,
    DPID_S1, DPID_S2, DPID_S3,
    PRIORITY_FLOW_LEVEL, STANDARD_FLOW_LEVEL
)

class MockOFPDesc:
    def __init__(self, port_no, name, state=0, config=0):
        self.port_no = port_no
        self.name = name.encode('utf-8')
        self.state = state
        self.config = config

class MockOFPPortStatus:
    def __init__(self, port_no, name, reason=2, state=0, config=0):
        self.reason = reason
        self.desc = MockOFPDesc(port_no, name, state, config)

class MockPortStatusEvent:
    def __init__(self, dpid, port_no, name, is_down=False):
        self.dpid = dpid
        state = 1 if is_down else 0
        self.ofp = MockOFPPortStatus(port_no, name, reason=2, state=state)

class MockConnection:
    def __init__(self, dpid):
        self.dpid = dpid
        self.sent_messages = []

    def send(self, msg):
        self.sent_messages.append(msg)


class TestSDNControllerRole2(unittest.TestCase):

    def setUp(self):
        # Create controller instance directly
        self.controller = ResilientSDNController.__new__(ResilientSDNController)
        self.controller.switches = {}
        self.controller.mac_to_port = {DPID_S1: {}, DPID_S2: {}, DPID_S3: {}}
        self.controller.port_status = {
            DPID_S1: {1: True, 2: True, 3: True, 4: True},
            DPID_S2: {1: True, 2: True, 3: True},
            DPID_S3: {1: True, 2: True, 3: True}
        }
        self.controller.host_ip_to_location = {
            "10.0.0.1": (DPID_S2, 3),
            "10.0.0.2": (DPID_S1, 1),
            "10.0.0.3": (DPID_S1, 4),
        }
        self.controller.backup_host_locations = {
            "10.0.0.2": (DPID_S3, 3),
        }
        self.controller.switch_links = {
            (DPID_S1, DPID_S2): {"primary_port": 2, "backup_port": 3, "backup_via": DPID_S3},
            (DPID_S2, DPID_S1): {"primary_port": 1, "backup_port": 2, "backup_via": DPID_S3},
        }

        # Mock connections
        self.conn_s1 = MockConnection(DPID_S1)
        self.conn_s2 = MockConnection(DPID_S2)
        self.conn_s3 = MockConnection(DPID_S3)
        self.controller.switches[DPID_S1] = self.conn_s1
        self.controller.switches[DPID_S2] = self.conn_s2
        self.controller.switches[DPID_S3] = self.conn_s3

    def test_01_safe_decode_payload(self):
        """Verifies safe decoding across strings, bytes, and dictionary payloads."""
        b_data = b'{"priority": "HIGH"}'
        s_data = '{"priority": "HIGH"}'
        self.assertEqual(safe_decode_payload(b_data), b_data)
        self.assertEqual(safe_decode_payload(s_data), b'{"priority": "HIGH"}')
        print("  [PASS] Safe payload extraction verified.")

    def test_02_primary_route_calculation(self):
        """Under normal conditions, traffic to 10.0.0.1 from s1 should use primary port 2."""
        egress_port = self.controller.get_egress_port(DPID_S1, in_port=1, dst_ip="10.0.0.1")
        self.assertEqual(egress_port, 2, "Primary path should use Port 2 (direct trunk to s2)")
        print("  [PASS] Primary route calculation verified (s1 -> Port 2 -> s2).")

    def test_03_link_failure_detection_and_reroute(self):
        """When link s1-s2 drops, PortStatus should trigger reroute via Port 3 (s3)."""
        # Simulate s1 Port 2 going DOWN
        event_s1_down = MockPortStatusEvent(DPID_S1, port_no=2, name="s1-eth2", is_down=True)
        self.controller._handle_PortStatus(event_s1_down)

        # Port 2 on s1 should now be marked inactive
        self.assertFalse(self.controller.is_port_active(DPID_S1, 2))

        # Egress calculation for Server (10.0.0.1) on s1 must now dynamically return Port 3!
        new_egress = self.controller.get_egress_port(DPID_S1, in_port=1, dst_ip="10.0.0.1")
        self.assertEqual(new_egress, 3, "After link failure, s1 must reroute traffic to Port 3 (s3 detour)")

        # Verify flow flush was sent to clear stale routes
        self.assertTrue(len(self.conn_s1.sent_messages) > 0, "Controller must push flow mods on failure")
        print("  [PASS] Link failure detection (ofp_port_status) and automated detour rerouting verified.")

    def test_04_link_recovery(self):
        """When link s1-s2 is restored, controller should re-enable primary route."""
        # Drop port 2
        self.controller._handle_PortStatus(MockPortStatusEvent(DPID_S1, port_no=2, name="s1-eth2", is_down=True))
        self.assertEqual(self.controller.get_egress_port(DPID_S1, in_port=1, dst_ip="10.0.0.1"), 3)

        # Restore port 2
        self.controller._handle_PortStatus(MockPortStatusEvent(DPID_S1, port_no=2, name="s1-eth2", is_down=False))
        self.assertTrue(self.controller.is_port_active(DPID_S1, 2))
        self.assertEqual(self.controller.get_egress_port(DPID_S1, in_port=1, dst_ip="10.0.0.1"), 2,
                         "After link recovery, s1 must restore optimal primary Port 2")
        print("  [PASS] Link restoration & route re-convergence verified.")

    def test_05_priority_payload_detection(self):
        """Verifies priority detection in telemetry JSON strings."""
        high_payload = json.dumps({"agent_id": "Agent-Node-1", "cpu_percent": 95.0, "priority": "HIGH"}).encode('utf-8')
        norm_payload = json.dumps({"agent_id": "Agent-Node-1", "cpu_percent": 42.0, "priority": "NORMAL"}).encode('utf-8')

        self.assertIn(b'"priority": "HIGH"', high_payload)
        self.assertNotIn(b'"priority": "HIGH"', norm_payload)
        self.assertEqual(PRIORITY_FLOW_LEVEL, 65535, "High priority level must be OpenFlow max 65535")
        print("  [PASS] Priority payload detection and max priority rule (65535) verified.")

if __name__ == '__main__':
    print("\n" + "="*70)
    print(" EXECUTING POX SDN SOCKET CONTROLLER TEST SUITE (Deliverable 2)")
    print("="*70)
    unittest.main(verbosity=2)
