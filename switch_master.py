#!/usr/bin/python

# This software manages a bunch of switches and power supplies controlled via serial port.
# It has two main functions. The first is to listen to udev 'add' events and determine
# if any of the switches it monitors has been plugged in. If one has been plugged in, it
# will be configured by this software according to the configuration json file. The second
# function of this software binds to a tcp port and handles telnet sessions. The session
# allows users to manage switches from the console.

# control list
# board_name			switch_alias	depends_on
# --------------------------------------------------------------
# samsung_hcp3			2D		12V_0
# nxp_imx6_adit_abb		-		12V_0
# nxp_imx6_sabreauto		-		5V_0
# renesas_rcar_salv-x		-		12V_0
# hdmi_switcher			2A		-
# xilinx_cyclone-v_pcie		-		-
# nxp_imx6_vist_gen65_cmu	-		12V_0
# ---------------------------------------------------------------
# 12V_0				3A		-
# 5V_0				3B		-
# 5V_1				3C		-
# 3V3_0				3D		-


# tty_device setup
# tty_name		device_pin	active_state	alias
# ---------------------------------------------------------------------------------
# ttySwitchLvl1		D2		A_LO		1A
# ...
# ttySwitchLvl3		D3		A_LO		1B

import json 
import pyudev
import serial
import socket
import threading
import time

HOST = "0.0.0.0"
PORT = 6000
BAUD = 9600
TOUT = 1.5

cmd_help = ["HELP", "RELOAD", "LIST"]

pin_dict = {}
board_dict = {}
port_dict = {}

# udev async monitor function
def udev_tty_device_event(device):
	if device.action == 'add':
		for symlink in device.device_links:
			configure_port(symlink.split('/')[2])
	# else ignore the event

def list_cmds():
	output = ""
	for cmd in cmd_help:
		output += cmd + "\r\n"
	return output

def list_boards():
	output = ""
	for board in board_dict:
		output += " dependencies:\r\n"
		for dependency in board_dict[board]["dependencies"]:
			output += "  " + dependency + "\r\n"
	return output
	

def process_cmd(cmd):
	# process command string
	cmds = cmd.rstrip("\n\r").split(' ')
	if len(cmds) == 1:
		if cmds[0] == "RELOAD":
			reload()
			return "reloading config file"
		elif cmds[0] == "LIST":
			print("Listing available boards and related information")
			return list_boards()
		elif cmds[0] == "HELP":
			print("Available Commands")
			return list_cmds()
		else:
			return "invalid command"
	elif len(cmds) != 2:
		return "invalid command"

	# execute action
	# set dependencies
	try:
		dep_list = board_dict[cmds[0]]["dependencies"]
	except KeyError:
		dep_list = []
	
	print("Board " + cmds[0] + " has dep list of " + str(dep_list))
	if cmds[0] in board_dict:
		if cmds[1] == "ON":
			for switch in dep_list:
				set_pin(board_dict[switch]["switch"], cmds[1])
			# set target switch after 1 second to let PSU ramp up to voltage (soft switching)
			time.sleep(1)
			print("set_pin " + board_dict[cmds[0]]["switch"])
			set_pin(board_dict[cmds[0]]["switch"], cmds[1])
		elif cmds[1] == "OFF":
			# set target switch
			set_pin(board_dict[cmds[0]]["switch"], cmds[1])
			# set dependencies
			for switch in dep_list:
				set_pin(board_dict[switch]["switch"], cmds[1])
		elif cmds[1] == "RESET":
			set_pin(board_dict[cmds[0]]["switch"], "OFF")
			time.sleep(1)
			set_pin(board_dict[cmds[0]]["switch"], "ON")
		elif cmds[1] == "TOGGLE":
			set_pin(board_dict[cmds[0]]["switch"], "ON")
			time.sleep(1)
			set_pin(board_dict[cmds[0]]["switch"], "OFF")
		else:
			return "invalid switch command"
	else:
		return "unknown switch or command"

	return cmds[1]

class chatServer(threading.Thread):
	def __init__(self, socket_address):
		socket,address = socket_address
		threading.Thread.__init__(self)
		self.socket = socket
		self.address= address

	def run(self):
		lock.acquire()
		clients.append(self)
		lock.release()
		print('%s:%s connected.' % self.address)
		while True:
			data = self.socket.recv(1024)
			if not data:
				break
			output = process_cmd(data) + "OK\r\n"
			self.socket.send(output)
		self.socket.close()
		print('%s:%s disconnected.' % self.address)
		lock.acquire()
		clients.remove(self)
		lock.release()

def set_pin(pin, action):

	if pin == '':
		return

	print("setting " + pin + " " + action)
	ser = port_dict[pin_dict[pin]["port"]]["serial"]

	# configure each pin
	if action == "ON":
		print("setting " + pin_dict[pin]["port"] + ":" + pin_dict[pin]["alias"] + "(" + pin_dict[pin]["pin_name"] + ")")
		str = "c " + pin_dict[pin]["pin_name"] + " 1\r\n"
		ser.write(str.encode())
		print(ser.readline())
	elif action == "OFF":
		str = "c " + pin_dict[pin]["pin_name"] + " 0\r\n"
		ser.write(str.encode())
		print(ser.readline())
	else:
		print("unknown action")

def configure_port(port):
	# attempt to open the serial port
	if port in port_dict:
		dev_port = "/dev/" + port
		print("attempting to open " + dev_port)
		try:
			ser = port_dict[port]["serial"]
			ser.port = dev_port
			ser.open()
			print("port '" + dev_port + "' opened")
			print(ser.readline())
		# abort configuring switch if port is not available
		except (serial.SerialException):
			ser = ""
			print("port '" + dev_port + "' not found");
		# configure each pin
		for pin in port_dict[port]["pins"]: 
			conf_str = "s " + pin["pin_name"] + " " + pin["active"] + "\r\n"
			print("configuring " + pin["pin_name"] + ":" + pin["active"])
			# configure device if found
			if ser:
				ser.write(conf_str.encode())
				print(ser.readline())
		# close up device if found
		if ser:
			# finalize configuration of switch
			ser.write("s FINISH\r\n")
			print(ser.readline())
			# close switch
			#ser.close()
			#print("closing " + dev_port)
	else:
		print("port not defined in board list")

def reload():
	# open our config json file and read in system configuration
	with open('/usr/share/switch_master/switch_master.json') as switches:
		switch_datastore = json.load(switches)
		# loop through all switches and attempt to configure hardware
		for switch in switch_datastore["switches"]:
			# get each switch name
			for key in switch.keys():
				dev_port = "/dev/" + key
				for pin in switch[key]:
					pin_dict[pin["alias"]] = { "port":key, "pin_name":pin["pin_name"], "alias":pin["alias"] }
				ser = serial.Serial(None, BAUD, timeout = TOUT, xonxoff=0, rtscts=0)
				port_dict[key] = {}
				port_dict[key]["pins"] = switch[key]
				port_dict[key]["serial"] = ser
				configure_port(key)
		# end switch configuration

		# read board configuration
		for switch in switch_datastore["boards"]:
			board_dict[switch["name"]] = { "switch": switch["switch"], "dependencies": switch["dependencies"] }


# telnet server
print("opening tcp socket on " + HOST + " at port " + str(PORT))
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.bind((HOST, PORT))
s.listen(4)
clients = [] #list of clients connected
lock = threading.Lock()

# udev monitor
context = pyudev.Context()
monitor = pyudev.Monitor.from_netlink(context)
monitor.filter_by(subsystem='tty')
observer = pyudev.MonitorObserver(monitor, callback=udev_tty_device_event, name='monitor-observer')
observer.daemon

# reload config settings from json file
reload()

# start the udev monitor
observer.start()
# send socket to chatserver and start monitoring
while True:
	chatServer(s.accept()).start()
