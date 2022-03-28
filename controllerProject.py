from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.app.wsgi import WSGIApplication
from ryu.lib.packet import packet
from ryu.lib.packet import ether_types
from ryu.lib.packet import ethernet
from ryu.lib.packet import ipv4
import os	#to execute cli command
from ryu.lib import hub		#threads

class MyFirstApp(app_manager.RyuApp):
	OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

	def __init__(self, *args, **kwargs):
		super(MyFirstApp, self).__init__(*args, **kwargs)
		self.mac_to_port = {}
		self.datapaths = {}							#it stores the switches' id
		self.ports = {}								#here we keep track of the active interfaces
		self.stats_thread = hub.spawn(self.send_port_desc_stats_request)	#the thread that periodically asks for statistics

	#---------------------------------------------------
	#-----------------------PERIODIC PORT STATS---------
	#this generates a Port stats request (from controller to switches)
	def send_port_desc_stats_request(self):
		while True:
			for dp in self.datapaths.values():
				ofp_parser = dp.ofproto_parser
				req = ofp_parser.OFPPortDescStatsRequest(dp, 0)
				dp.send_msg(req)
			hub.sleep(10)

	#Handling port stats reply
	@set_ev_cls(ofp_event.EventOFPPortDescStatsReply, MAIN_DISPATCHER)
	def port_desc_stats_reply_handler(self, ev):
		ports = []
		counter = 0
		switchName = ""
		for p in ev.msg.body:
			counter = counter + 1
			if counter == 1 and ev.msg.body[0].name not in self.ports:	#adding the Switch in the dictionary as key
				switchName = ev.msg.body[0].name
				self.ports.setdefault(switchName, [])
			elif ev.msg.body[0].name in self.ports:				#if it is already inside let's check if all interfaces has been saved
				switchName = ev.msg.body[0].name

			ports.append('hw_addr=%s name=%s config=0x%08x state=0x%08x' %
				     (p.hw_addr,
				      p.name, p.config,
				      p.state))
			if counter != 1 and p.name not in self.ports[switchName] and p.state == 4:  #if the received interface is enabled + not alredy in the list put it
				self.ports[switchName].append(p.name)
		self.logger.info('Current active links: %s\n', self.ports)

	#On port status changing
	@set_ev_cls(ofp_event.EventOFPPortStatus, MAIN_DISPATCHER)
	def _port_status_handler(self, ev):
		datapath = ev.msg.datapath
		msg = ev.msg
		reason = msg.reason
		port_no = msg.desc.port_no

		if msg.desc.state != 4:
			self.logger.info("Switch %s port %s disabled", datapath.id, port_no)
			self.onLinkFailed(datapath.id, msg.desc.name, datapath)
		else:
			self.logger.info("Switch %s port %s enabled", datapath.id, port_no)
			#here i don't have to handle when the link will be back again: with the periodic link status messages i can added it again
	
	#---------------------------------------------------
	#-----------------------END PERIODIC PORT STATS-----



	#---------------------------------------------------
	#-------------------ON LINK FAIL ALGORITHM----------
	def onLinkFailed(self, switchId, port, datapath):
		self.logger.info("Handling switch %s port %s", switchId, port)
		#remove that port from self.ports
		if port in self.ports["s"+str(switchId)]:
			self.ports["s"+str(switchId)].remove(port)	#removing the dead port from the current active link

			#if we do not consider the link that brings us to hosts, now we have the working topology to reach the destination
			#update the flow rules of the only involved switches in the failure
			out_port = self.ports["s"+str(switchId)]
			if "s"+str(switchId)+"-eth3" in out_port:
				out_port.remove("s"+str(switchId)+"-eth3")
			out_port = out_port[0]

			#getting the number port from its name
			lastIndexOfH = out_port.index("h")+1
			out_port = out_port[lastIndexOfH::]
			lastIndexOfH = port.index("h")+1
			port = port[lastIndexOfH::]

			out_port = int(out_port)
			port = int(port)

			req=self.buildGroup(datapath, port, out_port, 51)
			datapath.send_msg(req)
			req=self.buildGroup(datapath, out_port, port, 52)
			datapath.send_msg(req)

			#deleting all pre-existing flows
			ofproto = datapath.ofproto
			parser = datapath.ofproto_parser
			empty_match = parser.OFPMatch()
			instructions = []
			flow_mod = self.remove_table_flows(datapath, 0, empty_match, instructions)
			datapath.send_msg(flow_mod)

			#installing new flow: if the in_port is the one we use to send back the packets (so the in_port), if the second one is available send to it, 							otherwise send it back	(if the packet is ip and the destination either h1 or h2 to avoid loops)
			actions = [parser.OFPActionGroup(51)]
			match = parser.OFPMatch(in_port=out_port, eth_type=ether_types.ETH_TYPE_IP, ipv4_dst="10.0.2.2")
			self.add_flow(datapath, 1, match, actions)
			actions = [parser.OFPActionGroup(51)]
			match = parser.OFPMatch(in_port=out_port, eth_type=ether_types.ETH_TYPE_IP, ipv4_dst="10.0.1.2")
			self.add_flow(datapath, 1, match, actions)
			
			#installing new flow: (viceversa)
			actions = [parser.OFPActionGroup(52)]
			match = parser.OFPMatch(in_port=port, eth_type=ether_types.ETH_TYPE_IP, ipv4_dst="10.0.2.2")	#if the in_port is the one we use to send back the packets (so the in_port)
			self.add_flow(datapath, 1, match, actions)
			actions = [parser.OFPActionGroup(52)]
			match = parser.OFPMatch(in_port=port, eth_type=ether_types.ETH_TYPE_IP, ipv4_dst="10.0.1.2")	#if the in_port is the one we use to send back the packets (so the in_port)
			self.add_flow(datapath, 1, match, actions)

			if switchId == 1:
				actions = [parser.OFPActionSetField(eth_dst="00:00:00:00:00:01"), parser.OFPActionOutput(3)]
				match = parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IP, ipv4_dst="10.0.1.2")
				self.add_flow(datapath, 100, match, actions)

				#GROUP FOR H1
				actions1 = [parser.OFPActionOutput(1)]
				actions2 = [parser.OFPActionOutput(2)]

				watch_port1 = 1
				watch_port2 = 2

				bucket1 = parser.OFPBucket(watch_port=watch_port1, actions=actions1)
				bucket2 = parser.OFPBucket(watch_port=watch_port2, actions=actions2)
			
				req = parser.OFPGroupMod(datapath, ofproto.OFPGC_ADD, ofproto.OFPGT_FF, 53, [bucket1, bucket2])
				datapath.send_msg(req)
				
				#FLOW FOR H1
				actions = [parser.OFPActionGroup(53)]
				match = parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IP, ipv4_src="10.0.1.2")
				self.add_flow(datapath, 1, match, actions)
				
				
			elif switchId == 5:
				actions = [parser.OFPActionSetField(eth_dst="00:00:00:00:00:02"), parser.OFPActionOutput(3)]
				match = parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IP, ipv4_dst="10.0.2.2")
				self.add_flow(datapath, 100, match, actions)

				#GROUP FOR H2
				actions1 = [parser.OFPActionOutput(1)]
				actions2 = [parser.OFPActionOutput(2)]

				watch_port1 = 1
				watch_port2 = 2

				bucket1 = parser.OFPBucket(watch_port=watch_port1, actions=actions1)
				bucket2 = parser.OFPBucket(watch_port=watch_port2, actions=actions2)
			
				req = parser.OFPGroupMod(datapath, ofproto.OFPGC_ADD, ofproto.OFPGT_FF, 53, [bucket1, bucket2])
				datapath.send_msg(req)
				
				#FLOW FOR H2
				actions = [parser.OFPActionGroup(53)]
				match = parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IP, ipv4_src="10.0.2.2")
				self.add_flow(datapath, 1, match, actions)

			#installing table miss
			match = parser.OFPMatch()
			actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)]
			self.add_flow(datapath, 0, match, actions)

	#---------------------------------------------------
	#----------------END ON LINK FAIL ALGORITHM---------

	def buildGroup(self, datapath, port, out_port, groupId):
		ofp = datapath.ofproto
		ofp_parser = datapath.ofproto_parser

		actions1 = [ofp_parser.OFPActionOutput(port)]
		actions2 = [ofp_parser.OFPActionOutput(ofp.OFPP_IN_PORT)]

		watch_port1 = port
		watch_port2 = out_port

		self.logger.info("watch_port1: %s, watch_port2: %s", watch_port1, watch_port2)
		bucket1 = ofp_parser.OFPBucket(watch_port=watch_port1, actions=actions1)
		bucket2 = ofp_parser.OFPBucket(watch_port=watch_port2, actions=actions2)
	
		req = ofp_parser.OFPGroupMod(datapath, ofp.OFPGC_ADD, ofp.OFPGT_FF, groupId, [bucket1, bucket2])

		return req

	def remove_table_flows(self, datapath, table_id, match, instructions):
		#Create OFP flow mod message to remove flows from table
		ofproto = datapath.ofproto
		flow_mod = datapath.ofproto_parser.OFPFlowMod(datapath, 0, 0,
			table_id, ofproto.OFPFC_DELETE, 0, 0, 1, ofproto.OFPCML_NO_BUFFER, ofproto.OFPP_ANY, ofproto.OFPG_ANY, 0, match, instructions)
		return flow_mod
    #----------------------------------------------------------
    # Write the function: Add a flow in the switch flow table
    #----------------------------------------------------------

	def add_flow(self, datapath, priority, match, actions, buffer_id=None):
		ofproto = datapath.ofproto
		parser = datapath.ofproto_parser

		# construct flow_mod message and send it.
		inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS,
					                actions)]
		if buffer_id:
			mod = parser.OFPFlowMod(datapath=datapath, buffer_id=buffer_id,
					    priority=priority, match=match,
					    instructions=inst)
		else:
			mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
				    match=match, instructions=inst)
		datapath.send_msg(mod)


    #--------------------------------------------------------------------
    # Write the function: Upon a switch feature reply, add the table miss
    #--------------------------------------------------------------------

	@set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
	def switch_features_handler(self, ev):
		datapath = ev.msg.datapath
		ofproto = datapath.ofproto
		parser = datapath.ofproto_parser

		if datapath not in self.datapaths:
			self.datapaths[datapath.id] = datapath

		# install the table-miss flow entry.
		match = parser.OFPMatch()
		actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
					          ofproto.OFPCML_NO_BUFFER)]
		self.add_flow(datapath, 0, match, actions)
		self.logger.info("Table miss installed for switch: %s", datapath.id)

		# installing basic flow rules
		#all switches must install these rules:
		#sudo ovs-ofctl add-flow s1 priority=1,ip,in_port=1,ip4,ipv4_dest=10.0.2.2 OR 10.0.1.2 actions=output:2 -O OpenFlow13
		actions = [parser.OFPActionOutput(2)]
		match = parser.OFPMatch(in_port=1, eth_type=ether_types.ETH_TYPE_IP, ipv4_dst="10.0.2.2")
		self.add_flow(datapath, 1, match, actions)
		actions = [parser.OFPActionOutput(2)]
		match = parser.OFPMatch(in_port=1, eth_type=ether_types.ETH_TYPE_IP, ipv4_dst="10.0.1.2")
		self.add_flow(datapath, 1, match, actions)
		#sudo ovs-ofctl add-flow s1 priority=1,ip,in_port=2,ip4,ipv4_dest=10.0.2.2 OR 10.0.1.2 actions=output:1 -O OpenFlow13
		actions = [parser.OFPActionOutput(1)]
		match = parser.OFPMatch(in_port=2, eth_type=ether_types.ETH_TYPE_IP, ipv4_dst="10.0.2.2")
		self.add_flow(datapath, 1, match, actions)
		actions = [parser.OFPActionOutput(1)]
		match = parser.OFPMatch(in_port=2, eth_type=ether_types.ETH_TYPE_IP, ipv4_dst="10.0.1.2")
		self.add_flow(datapath, 1, match, actions)

		#for the switches havin a host connected
		if datapath.id == 1:
			#sudo ovs-ofctl add-flow s1 priority=100,ip,nw_dst=10.0.1.2,actions=mod_dl_dst:00:00:00:00:00:01,output:3 -O OpenFlow13
			actions = [parser.OFPActionSetField(eth_dst="00:00:00:00:00:01"), parser.OFPActionOutput(3)]
			match = parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IP, ipv4_dst="10.0.1.2")
			self.add_flow(datapath, 100, match, actions)
			#sudo ovs-ofctl add-flow s1 priority=1,ip,in_port=3,actions=output:1 -O OpenFlow13
			actions = [parser.OFPActionOutput(1)]
			match = parser.OFPMatch(in_port=3)
			self.add_flow(datapath, 1, match, actions)
		elif datapath.id == 5:
			#sudo ovs-ofctl add-flow s5 priority=100,ip,nw_dst=10.0.2.2,actions=mod_dl_dst:00:00:00:00:00:02,output:3 -O OpenFlow13
			actions = [parser.OFPActionSetField(eth_dst="00:00:00:00:00:02"), parser.OFPActionOutput(3)]
			match = parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IP, ipv4_dst="10.0.2.2")
			self.add_flow(datapath, 100, match, actions)
			#sudo ovs-ofctl add-flow s5 priority=1,ip,in_port=3,actions=output:1 -O OpenFlow13
			actions = [parser.OFPActionOutput(1)]
			match = parser.OFPMatch(in_port=3)
			self.add_flow(datapath, 1, match, actions)
		#--------------------------------------------------
	#----------------------------------------------
	# Write the function handle a packet-in request
	#----------------------------------------------

	@set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
	def _packet_in_handler(self, ev):
		msg = ev.msg
		datapath = msg.datapath
		ofproto = datapath.ofproto
		parser = datapath.ofproto_parser

		# get Datapath ID to identify OpenFlow switches.
		dpid = datapath.id
		self.mac_to_port.setdefault(dpid, {})

		# analyse the received packets using the packet library.
		pkt = packet.Packet(msg.data)
		eth_pkt = pkt.get_protocol(ethernet.ethernet)
		dst = eth_pkt.dst
		src = eth_pkt.src

		if eth_pkt.ethertype == ether_types.ETH_TYPE_LLDP:
			# ignore lldp packet
			return
		if eth_pkt.ethertype == ether_types.ETH_TYPE_IPV6:
			# ignore ipv6 packet
			return

		# get the received port number from packet_in message.
		in_port = msg.match['in_port']

		self.logger.info("packet in switch: %s, from host: %s, to host: %s, from port: %s", dpid, src, dst, in_port)

		if eth_pkt.ethertype == ether_types.ETH_TYPE_ARP:
			# learn a mac address to avoid FLOOD next time.
			if src not in self.mac_to_port[dpid]:            
				self.mac_to_port[dpid][src] = in_port

			# if the destination mac address is already learned,
			# decide which port to output the packet, otherwise FLOOD.
			if dst in self.mac_to_port[dpid]:
				out_port = self.mac_to_port[dpid][dst]
			else:
				out_port = ofproto.OFPP_FLOOD

			self.logger.info("forwarding table: %s", self.mac_to_port)
			
			# construct action list.
			actions = [parser.OFPActionOutput(out_port)]
			self.logger.info("actions: %s", actions)


			# install a flow to avoid packet_in next time.
			if out_port != ofproto.OFPP_FLOOD:
				match = parser.OFPMatch(in_port=in_port, eth_dst=dst)
				self.add_flow(datapath, 1, match, actions)

			# construct packet_out message and send it.
			out = parser.OFPPacketOut(datapath=datapath,
						  buffer_id=ofproto.OFP_NO_BUFFER,
						  in_port=in_port, actions=actions,
						  data=msg.data)
			datapath.send_msg(out)

app_manager.require_app('ryu.app.ws_topology')
app_manager.require_app('ryu.app.ofctl_rest')
app_manager.require_app('ryu.app.gui_topology.gui_topology')	
