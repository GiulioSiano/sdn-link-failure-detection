from mininet.net import Mininet
from mininet.node import Controller, RemoteController
from mininet.cli import CLI
from mininet.log import setLogLevel, info
from mininet.link import TCLink

def myNetwork():
	
	net = Mininet(topo=None, build=False, link=TCLink)
	net.addController(name='c0',controller=RemoteController, ip='127.0.0.1', port=6633)

	h1=net.addHost('h1', ip='10.0.1.2/24', mac='00:00:00:00:00:01', defaultRoute='via 10.0.1.1')
	h2=net.addHost('h2', ip='10.0.2.2/24', mac='00:00:00:00:00:02', defaultRoute='via 10.0.2.1')

	s1=net.addSwitch('s1')
	s2=net.addSwitch('s2')
	s3=net.addSwitch('s3')
	s4=net.addSwitch('s4')
	s5=net.addSwitch('s5')
	s6=net.addSwitch('s6')

	net.addLink(s1, s2)
	net.addLink(s2, s3)
	net.addLink(s3, s4)
	net.addLink(s4, s5)
	net.addLink(s5, s6)
	net.addLink(s6, s1)

	net.addLink(h1, s1)
	net.addLink(h2, s5)

	info('*** Starting network\n') 
	net.start()
	h1.cmd('arp -s 10.0.1.1 00:00:00:00:00:11')
	h2.cmd('arp -s 10.0.2.1 00:00:00:00:00:12')

	#Avvio la CLI per la rete
	CLI(net)
	net.stop()

if __name__ == '__main__':
	setLogLevel('info')
	myNetwork()
		

