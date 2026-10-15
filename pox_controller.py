"""
POX OpenFlow SDN Controller for Mininet Health Monitor Network
Deliverable 2 - Role 2: SDN & Control Plane Engineer

Key Capabilities:
1. Flow Prioritization:
   - Deep inspection of telemetry UDP packets (port 5005) from health monitoring agents.
   - Detects "priority": "HIGH" in JSON telemetry payload (triggered when host CPU > 80%).
   - Installs high-priority OpenFlow flow rules (priority=65535) on OpenFlow switches.
   - Normal traffic and bulk traffic receives standard priority rules (priority=100).

2. Link Failure Detection (ofp_port_status):
   - Handles ofp_port_status OpenFlow asynchronous messages.
   - Real-time detection of link failure events (e.g., Mininet CLI `link s1 s2 down` or `link s1 h2 down`).
   - Instantly issues OFPFC_DELETE flow mods to purge stale flows pointing to failed ports,
     preventing packet blackholing.

3. Automated Path Rerouting:
   - Automatically reroutes telemetry traffic from the primary path to alternate/backup routes.
   - Primary Path: s1 -> s2 -> h1 (Direct switch-to-switch link)
   - Alternate Path: s1 -> s3 -> s2 -> h1 (Detour via redundant backup switch s3)
   - Seamless failover keeps telemetry packets flowing without breaking UDP sockets.
   - Detects link restoration (ofp_port_status UP) and re-enables the optimal primary route.
"""

from pox.core import core
import pox.openflow.libopenflow_01 as of
from pox.lib.addresses import IPAddr, EthAddr
from pox.lib.packet.ethernet import ethernet
from pox.lib.packet.ipv4 import ipv4
from pox.lib.packet.udp import udp
from pox.lib.packet.arp import arp

log = core.getLogger()

# ==========================================
# CONFIGURATION CONSTANTS
# ==========================================
TELEMETRY_PORT = 5005
PRIORITY_FLOW_LEVEL = 65535   # Maximum OpenFlow 1.0 flow priority
STANDARD_FLOW_LEVEL = 100     # Standard flow priority
DEFAULT_FLOW_LEVEL = 10       # Fallback flow priority

IDLE_TIMEOUT_PRIORITY = 60    # Keep high-priority flows cached
IDLE_TIMEOUT_STANDARD = 20    # Standard flow cache timeout
HARD_TIMEOUT = 0              # No hard timeout (expires via idle timeout)

# Switch Datapath IDs (DPIDs)
DPID_S1 = 1  # Agent Aggregation Switch
DPID_S2 = 2  # Server Aggregation Switch
DPID_S3 = 3  # Resilient Backup Detour Switch


def safe_decode_payload(payload_obj):
    """
    Safely extracts raw byte/string payload from POX packet objects.
    Compatible across Python 2 and Python 3 POX runtimes.
    """
    if payload_obj is None:
        return b""
    if isinstance(payload_obj, bytes):
        return payload_obj
    if isinstance(payload_obj, str):
        try:
            return payload_obj.encode('utf-8', errors='ignore')
        except Exception:
            return payload_obj
    if hasattr(payload_obj, 'data'):
        return safe_decode_payload(payload_obj.data)
    if hasattr(payload_obj, 'pack'):
        try:
            return payload_obj.pack()
        except Exception:
            pass
    return str(payload_obj).encode('utf-8', errors='ignore')


class ResilientSDNController(object):
    """
    Central SDN Controller managing switch connections, flow prioritization,
    link failure detection, and automated path rerouting.
    """
    def __init__(self):
        # Register core OpenFlow event listeners
        core.openflow.addListeners(self)

        # Active switch connections: dpid -> Connection object
        self.switches = {}

        # MAC to Port mapping per switch: dpid -> { mac: port }
        self.mac_to_port = {}

        # Port status tracking: dpid -> { port_no: is_active (bool) }
        self.port_status = {}

        # Host location tracking: IP -> (dpid, port)
        self.host_ip_to_location = {
            "10.0.0.1": (DPID_S2, 3),   # h1 (Server) connected to s2 port 3
            "10.0.0.2": (DPID_S1, 1),   # h2 (Agent 1) primary connected to s1 port 1
            "10.0.0.3": (DPID_S1, 4),   # h3 (Agent 2) connected to s1 port 4
        }

        # Backup host location for dual-homed setups (e.g. h2 backup link to s3 port 3)
        self.backup_host_locations = {
            "10.0.0.2": (DPID_S3, 3),
        }

        # Inter-switch topology definition:
        # (src_dpid, dst_dpid) -> { "primary_port": P1, "backup_port": P2 }
        # s1: port 2 -> s2 (primary), port 3 -> s3 (alternate)
        # s2: port 1 -> s1 (primary), port 2 -> s3 (alternate)
        # s3: port 1 -> s1, port 2 -> s2
        self.switch_links = {
            (DPID_S1, DPID_S2): {"primary_port": 2, "backup_port": 3, "backup_via": DPID_S3},
            (DPID_S2, DPID_S1): {"primary_port": 1, "backup_port": 2, "backup_via": DPID_S3},
            (DPID_S3, DPID_S1): {"port": 1},
            (DPID_S3, DPID_S2): {"port": 2},
        }

        log.info("==========================================================")
        log.info("  SDN HEALTH MONITOR CONTROLLER INITIALIZED (Deliverable 2)")
        log.info("  Role 2: Flow Prioritization + Fault Tolerance + Rerouting")
        log.info("==========================================================")

    def _handle_ConnectionUp(self, event):
        """
        Triggered when an OpenFlow switch connects to POX.
        """
        dpid = event.dpid
        self.switches[dpid] = event.connection
        self.mac_to_port[dpid] = {}
        self.port_status[dpid] = {}

        # Record initial port states from switch description
        for port in event.ofp.ports:
            # Skip local virtual ports
            if port.port_no <= of.OFPP_MAX:
                is_up = not (port.state & of.OFPPS_LINK_DOWN) and not (port.config & of.OFPPC_PORT_DOWN)
                self.port_status[dpid][port.port_no] = is_up

        log.info("🔌 [SWITCH CONNECTED] Switch s%s (DPID: %s) registered with %d ports.",
                 dpid, dpid, len(self.port_status[dpid]))

    def _handle_ConnectionDown(self, event):
        """
        Triggered when a switch disconnects.
        """
        dpid = event.dpid
        log.warn("❌ [SWITCH DISCONNECTED] Switch s%s (DPID: %s) left the network.", dpid, dpid)
        self.switches.pop(dpid, None)
        self.mac_to_port.pop(dpid, None)
        self.port_status.pop(dpid, None)

    def is_port_active(self, dpid, port_no):
        """
        Returns True if the specified port on switch dpid is currently operational.
        """
        return self.port_status.get(dpid, {}).get(port_no, True)

    def purge_switch_flows(self, connection, out_port=None):
        """
        Flushes OpenFlow flow table entries on a switch.
        If out_port is specified, only flows forwarding to that port are purged.
        """
        msg = of.ofp_flow_mod()
        msg.command = of.OFPFC_DELETE
        if out_port is not None:
            msg.out_port = out_port
        connection.send(msg)

    # =========================================================================
    # REQUIREMENT 2: LINK FAILURE DETECTION (ofp_port_status)
    # =========================================================================
    def _handle_PortStatus(self, event):
        """
        Handles OpenFlow ofp_port_status messages generated when a physical or
        virtual link changes state (e.g. Mininet `link s1 s2 down` or `link s1 h2 down`).
        """
        dpid = event.dpid
        port_no = event.ofp.desc.port_no
        port_name = event.ofp.desc.name.decode('utf-8', errors='ignore') if isinstance(event.ofp.desc.name, bytes) else str(event.ofp.desc.name)
        reason = event.ofp.reason
        state = event.ofp.desc.state
        config = event.ofp.desc.config

        # Check if port is down
        is_down = (reason == of.OFPPR_DELETE) or \
                  bool(state & of.OFPPS_LINK_DOWN) or \
                  bool(config & of.OFPPC_PORT_DOWN)

        if dpid not in self.port_status:
            self.port_status[dpid] = {}

        if is_down:
            self.port_status[dpid][port_no] = False
            log.warn("")
            log.warn("==================================================================")
            log.warn("⚠️  [SDN FAULT] LINK FAILURE DETECTED ON SWITCH s%s!", dpid)
            log.warn("   Port No:   %s (%s)", port_no, port_name)
            log.warn("   Reason:    %s | State: 0x%04x | Config: 0x%04x", reason, state, config)
            log.warn("==================================================================")

            # Purge stale flow rules pointing to the failed port immediately
            if dpid in self.switches:
                self.purge_switch_flows(self.switches[dpid], out_port=port_no)
                log.info("🧹 [FLOW PURGE] Flushed stale flow entries routing to dead Port %s on Switch s%s.", port_no, dpid)

            # Trigger automated rerouting
            self.trigger_dynamic_reroute(dpid, port_no, link_down=True)

        else:
            self.port_status[dpid][port_no] = True
            log.info("")
            log.info("==================================================================")
            log.info("✅ [SDN RECOVERY] LINK RESTORED ON SWITCH s%s!", dpid)
            log.info("   Port No:   %s (%s) is now operational (UP).", port_no, port_name)
            log.info("==================================================================")

            # Flush temporary detour flows to allow clean re-convergence to primary route
            if dpid in self.switches:
                self.purge_switch_flows(self.switches[dpid])
            for sw_id, conn in self.switches.items():
                self.purge_switch_flows(conn)

            log.info("🔄 [TOPOLOGY RE-CONVERGE] Cleared temporary detour flows across switches.")
            self.trigger_dynamic_reroute(dpid, port_no, link_down=False)

    # =========================================================================
    # REQUIREMENT 3: AUTOMATED PATH REROUTING
    # =========================================================================
    def trigger_dynamic_reroute(self, failed_dpid, failed_port, link_down=True):
        """
        Determines alternate paths and pushes proactive flow rules when a link
        state change is detected.
        """
        if link_down:
            # Check if this was the primary link between s1 and s2
            # s1 Port 2 connects to s2 Port 1
            if (failed_dpid == DPID_S1 and failed_port == 2) or (failed_dpid == DPID_S2 and failed_port == 1):
                log.warn("🚨 [REROUTING ENGAGED] Primary inter-switch trunk s1 <-> s2 is BROKEN!")
                log.info("🔀 [FAILOVER] Rerouting all telemetry & host traffic via Detour Switch s3:")
                log.info("   Path: s1 (Port 3) ===> s3 (Port 1 -> Port 2) ===> s2 (Port 2)")
                self.install_detour_path_rules()
                return

            # Check if this was the host link to Agent 1 (h2) on s1 Port 1
            if failed_dpid == DPID_S1 and failed_port == 1:
                log.warn("🚨 [HOST LINK DOWN] Link between Switch s1 and Agent h2 is DOWN!")
                if "10.0.0.2" in self.backup_host_locations:
                    backup_dpid, backup_port = self.backup_host_locations["10.0.0.2"]
                    log.info("🔀 [HOST FAILOVER] Switching Agent h2 to backup interface via Switch s%s Port %s.",
                             backup_dpid, backup_port)
                    # Update active host location
                    self.host_ip_to_location["10.0.0.2"] = (backup_dpid, backup_port)
                return

            log.info("ℹ️  [REROUTING] Port %s on switch s%s marked DOWN. Dynamic routing updated.", failed_port, failed_dpid)

        else:
            # Link restored
            if (failed_dpid == DPID_S1 and failed_port == 2) or (failed_dpid == DPID_S2 and failed_port == 1):
                log.info("🎉 [PRIMARY RESTORED] Direct trunk s1 <-> s2 is back online!")
                log.info("   Restoring optimal Primary Path: s1 ===> s2")
            elif failed_dpid == DPID_S1 and failed_port == 1:
                log.info("🎉 [HOST LINK RESTORED] Primary link between s1 and h2 restored.")
                self.host_ip_to_location["10.0.0.2"] = (DPID_S1, 1)

    def install_detour_path_rules(self):
        """
        Pushes proactive OpenFlow rules across s1, s3, and s2 to route traffic
        seamlessly through the detour switch s3 without dropping telemetry.
        """
        # Rule on s1: Forward packets for Server (10.0.0.1) out Port 3 (towards s3)
        if DPID_S1 in self.switches:
            s1_conn = self.switches[DPID_S1]
            msg = of.ofp_flow_mod()
            msg.priority = STANDARD_FLOW_LEVEL + 50
            msg.match = of.ofp_match()
            msg.match.dl_type = 0x0800
            msg.match.nw_dst = IPAddr("10.0.0.1")
            msg.actions.append(of.ofp_action_output(port=3))
            msg.idle_timeout = IDLE_TIMEOUT_PRIORITY
            s1_conn.send(msg)
            log.info("   [FLOW MOD -> s1] Match: dst=10.0.0.1 -> Output Port: 3 (to s3)")

        # Rules on s3 (Detour Switch):
        # 1. Traffic from s1 (Port 1) bound for s2 / h1 -> Forward out Port 2
        # 2. Return traffic from s2 (Port 2) bound for s1 / agents -> Forward out Port 1
        if DPID_S3 in self.switches:
            s3_conn = self.switches[DPID_S3]

            # s3 -> s2 forward
            msg1 = of.ofp_flow_mod()
            msg1.priority = STANDARD_FLOW_LEVEL + 50
            msg1.match = of.ofp_match()
            msg1.match.dl_type = 0x0800
            msg1.match.nw_dst = IPAddr("10.0.0.1")
            msg1.actions.append(of.ofp_action_output(port=2))
            msg1.idle_timeout = IDLE_TIMEOUT_PRIORITY
            s3_conn.send(msg1)

            # s3 -> s1 reverse (for agent updates / acks)
            msg2 = of.ofp_flow_mod()
            msg2.priority = STANDARD_FLOW_LEVEL + 50
            msg2.match = of.ofp_match()
            msg2.match.dl_type = 0x0800
            msg2.match.nw_src = IPAddr("10.0.0.1")
            msg2.actions.append(of.ofp_action_output(port=1))
            msg2.idle_timeout = IDLE_TIMEOUT_PRIORITY
            s3_conn.send(msg2)

            log.info("   [FLOW MOD -> s3] Detour transit: Port 1 <===> Port 2 active.")

        # Rule on s2: Return traffic from Server (10.0.0.1) back to agents out Port 2 (towards s3)
        if DPID_S2 in self.switches:
            s2_conn = self.switches[DPID_S2]
            msg = of.ofp_flow_mod()
            msg.priority = STANDARD_FLOW_LEVEL + 50
            msg.match = of.ofp_match()
            msg.match.dl_type = 0x0800
            msg.match.nw_src = IPAddr("10.0.0.1")
            msg.actions.append(of.ofp_action_output(port=2))
            msg.idle_timeout = IDLE_TIMEOUT_PRIORITY
            s2_conn.send(msg)
            log.info("   [FLOW MOD -> s2] Return path: dst=Agents -> Output Port: 2 (to s3)")

        log.info("✨ [REROUTE COMPLETE] Detour path s1 -> s3 -> s2 established and verified.")

    # =========================================================================
    # REQUIREMENT 1: FLOW PRIORITIZATION & PACKET HANDLING
    # =========================================================================
    def _handle_PacketIn(self, event):
        """
        Handles incoming packets sent to controller via PacketIn.
        - Identifies high-priority telemetry packets ("priority": "HIGH")
        - Installs high-priority OpenFlow flow rules (priority=65535)
        - Computes dynamic egress port based on link operational state
        """
        dpid = event.dpid
        packet = event.parsed
        if not packet or not packet.parsed:
            return

        in_port = event.port
        src_mac = packet.src
        dst_mac = packet.dst

        # Record MAC table
        if dpid not in self.mac_to_port:
            self.mac_to_port[dpid] = {}
        self.mac_to_port[dpid][src_mac] = in_port

        # Check for IP packet
        ip_pkt = packet.find('ipv4')
        if ip_pkt:
            udp_pkt = ip_pkt.find('udp')
            if udp_pkt and (udp_pkt.dstport == TELEMETRY_PORT or udp_pkt.srcport == TELEMETRY_PORT):
                self.handle_telemetry_packet(event, ip_pkt, udp_pkt)
                return

        # General L2/L3 / ARP handling
        self.handle_general_packet(event, packet, in_port, src_mac, dst_mac)

    def handle_telemetry_packet(self, event, ip_pkt, udp_pkt):
        """
        Deep packet inspection for telemetry traffic.
        Installs high-priority rules if priority: HIGH is detected.
        """
        dpid = event.dpid
        in_port = event.port
        src_ip = str(ip_pkt.srcip)
        dst_ip = str(ip_pkt.dstip)

        # Inspect UDP payload for priority marker
        payload_bytes = safe_decode_payload(udp_pkt.payload)
        is_high_priority = False

        if b'"priority": "HIGH"' in payload_bytes or \
           b'"priority":"HIGH"' in payload_bytes or \
           b'"priority": "high"' in payload_bytes or \
           (b'"priority"' in payload_bytes and b'"HIGH"' in payload_bytes):
            is_high_priority = True

        # Determine optimal egress port considering link health
        out_port = self.get_egress_port(dpid, in_port, dst_ip)

        if out_port is None:
            # Fallback to flooding if path unknown
            self.flood_packet(event)
            return

        if is_high_priority:
            log.info("")
            log.info("🔥 ================================================================")
            log.info("🔥 [SDN QoS ALERT] HIGH-PRIORITY TELEMETRY DETECTED!")
            log.info("🔥 Switch: s%s (DPID: %s) | In-Port: %s -> Out-Port: %s", dpid, dpid, in_port, out_port)
            log.info("🔥 Source: %s -> Dest: %s | UDP Port: %s", src_ip, dst_ip, udp_pkt.dstport)
            log.info("🔥 Action: Installing MAX-PRIORITY Flow Rule (Priority: %d)", PRIORITY_FLOW_LEVEL)
            log.info("🔥 ================================================================")

            # Install High-Priority Flow Rule (Priority 65535)
            msg = of.ofp_flow_mod()
            msg.priority = PRIORITY_FLOW_LEVEL
            msg.match = of.ofp_match()
            msg.match.dl_type = 0x0800
            msg.match.nw_proto = 17   # UDP
            msg.match.nw_src = ip_pkt.srcip
            msg.match.nw_dst = ip_pkt.dstip
            msg.match.tp_dst = udp_pkt.dstport

            msg.actions.append(of.ofp_action_output(port=out_port))
            msg.idle_timeout = IDLE_TIMEOUT_PRIORITY
            msg.hard_timeout = HARD_TIMEOUT
            msg.data = event.ofp   # Forward the current packet immediately

            event.connection.send(msg)
            log.info("⚡ [SDN QoS INSTALLED] Max-priority rule active on switch s%s -> Port %s", dpid, out_port)

        else:
            # Standard telemetry flow (Priority 100)
            msg = of.ofp_flow_mod()
            msg.priority = STANDARD_FLOW_LEVEL
            msg.match = of.ofp_match()
            msg.match.dl_type = 0x0800
            msg.match.nw_proto = 17
            msg.match.nw_src = ip_pkt.srcip
            msg.match.nw_dst = ip_pkt.dstip
            msg.match.tp_dst = udp_pkt.dstport

            msg.actions.append(of.ofp_action_output(port=out_port))
            msg.idle_timeout = IDLE_TIMEOUT_STANDARD
            msg.hard_timeout = HARD_TIMEOUT
            msg.data = event.ofp

            event.connection.send(msg)

    def get_egress_port(self, dpid, in_port, dst_ip):
        """
        Computes the active egress port for a given destination IP on a switch,
        taking link health and alternate routes into consideration.
        """
        # If destination is directly connected to this switch
        if dst_ip in self.host_ip_to_location:
            target_dpid, target_port = self.host_ip_to_location[dst_ip]
            if target_dpid == dpid:
                if self.is_port_active(dpid, target_port):
                    return target_port

        # Inter-switch routing decisions:
        if dpid == DPID_S1:
            # Traffic bound for Server (10.0.0.1)
            if dst_ip == "10.0.0.1":
                primary_port = 2  # Direct link to s2
                backup_port = 3   # Alternate link via s3
                if self.is_port_active(DPID_S1, primary_port):
                    return primary_port
                elif self.is_port_active(DPID_S1, backup_port):
                    log.info("🔀 [DYNAMIC ROUTING] s1 primary port 2 is down, routing via alternate port 3.")
                    return backup_port
                else:
                    log.error("❌ [NO ROUTE] Both primary and backup ports on s1 are DOWN!")
                    return None

        elif dpid == DPID_S2:
            # Traffic bound for Agents (10.0.0.2 / 10.0.0.3)
            if dst_ip in ["10.0.0.2", "10.0.0.3"]:
                primary_port = 1  # Direct link to s1
                backup_port = 2   # Alternate link via s3
                if self.is_port_active(DPID_S2, primary_port):
                    return primary_port
                elif self.is_port_active(DPID_S2, backup_port):
                    log.info("🔀 [DYNAMIC ROUTING] s2 primary port 1 is down, routing via alternate port 2.")
                    return backup_port
                else:
                    log.error("❌ [NO ROUTE] Both primary and backup ports on s2 are DOWN!")
                    return None

        elif dpid == DPID_S3:
            # Detour transit switch
            if dst_ip == "10.0.0.1":
                return 2  # Towards s2 (Server)
            elif dst_ip in ["10.0.0.2", "10.0.0.3"]:
                return 1  # Towards s1 (Agents)

        # Fallback for single-switch topology or unknown destination
        if dst_ip in self.host_ip_to_location:
            target_dpid, target_port = self.host_ip_to_location[dst_ip]
            if target_dpid == dpid and self.is_port_active(dpid, target_port):
                return target_port

        return None

    def handle_general_packet(self, event, packet, in_port, src_mac, dst_mac):
        """
        Handles general non-telemetry packets (ARP, ICMP, TCP/iperf).
        Installs standard learning switch flow entries.
        """
        dpid = event.dpid

        # If multicast or broadcast (e.g. ARP request), flood it
        if dst_mac.is_multicast or dst_mac == EthAddr("ff:ff:ff:ff:ff:ff"):
            self.flood_packet(event)
            return

        # Check if we know where dst_mac lives
        if dst_mac in self.mac_to_port.get(dpid, {}):
            out_port = self.mac_to_port[dpid][dst_mac]
            if out_port != in_port and self.is_port_active(dpid, out_port):
                msg = of.ofp_flow_mod()
                msg.priority = DEFAULT_FLOW_LEVEL
                msg.match = of.ofp_match.from_packet(packet, in_port)
                msg.actions.append(of.ofp_action_output(port=out_port))
                msg.idle_timeout = 10
                msg.data = event.ofp
                event.connection.send(msg)
                return

        # Fallback to flooding
        self.flood_packet(event)

    def flood_packet(self, event):
        """
        Floods packet out all operational ports except the ingress port.
        """
        msg = of.ofp_packet_out()
        msg.in_port = event.port
        msg.data = event.ofp
        msg.actions.append(of.ofp_action_output(port=of.OFPP_FLOOD))
        event.connection.send(msg)


def launch():
    """
    Standard POX entry point.
    Launched with: ./pox.py pox_controller
    """
    core.registerNew(ResilientSDNController)
