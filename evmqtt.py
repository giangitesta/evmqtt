#!/usr/bin/env python3
"""
Linux input event to MQTT gateway
https://github.com/odtgit/evmqtt
"""


import os
import signal
import threading
import sys
import datetime
import json
from time import sleep
from time import time
from platform import node as hostname
from pathlib import Path
import evdev
import paho.mqtt.client as mqtt


def log(s):
    sys.stderr.write("[%s] %s\n" % (datetime.datetime.now(), s))
    sys.stderr.flush()


class Watcher:

    def __init__(self):
        self.child = os.fork()
        if self.child == 0:
            return
        else:
            self.watch()

    def watch(self):
        try:
            os.wait()
        except KeyboardInterrupt:
            # I put the capital B in KeyBoardInterrupt so I can
            # tell when the Watcher gets the SIGINT
            log('KeyBoardInterrupt received')
            self.kill()
        sys.exit()

    def kill(self):
        try:
            os.kill(self.child, signal.SIGKILL)
        except OSError:
            pass

# The callback for when the client receives a CONNACK response from the server.


def on_connect(client, userdata, flags, rc):
    log("Connected with result code " + str(rc))
    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe("topic")

    log("LWT Message " + userdata["topic"])
    client.publish(userdata["topic"] + "LWT", "Online", retain=True)


def on_disconnect(client, userdata, rc):
    client.publish(userdata["topic"] + "LWT", "Offline", retain=True)
    log("Disconnected with result code " + str(rc))
# The callback for when a PUBLISH message is received from the server.


def on_message(msg):
    msgpayload = str(msg.payload)
    print(msg.topic + " " + msgpayload)


class MQTTClient(threading.Thread):

    def __init__(self, clientid, mqttcfg):
        super(MQTTClient, self).__init__()
        serverip = mqttcfg["serverip"]
        port = mqttcfg["port"]
        username = mqttcfg["username"]
        password = mqttcfg["password"]
        log("MQTT connecting to %s:%u" % (serverip, port))
        self.mqttclient = mqtt.Client(clientid, protocol=mqtt.MQTTv31)
        self.mqttclient.username_pw_set(username, password)
        self.mqttclient.on_connect = on_connect
        self.mqttclient.on_disconnect = on_disconnect
        self.mqttclient.on_message = on_message
        self.mqttclient.user_data_set(mqttcfg)
        self.mqttclient.will_set(mqttcfg["topic"] + "LWT", "Offline", retain=True)
        self.mqttclient.connect(serverip, port)
        self.mqttclient.loop_start()
        


class InputMonitor(threading.Thread):

    def __init__(self, mqttclient, device, topic, tele_period):
        super(InputMonitor, self).__init__()
        self.mqttclient = mqttclient
        self.device = evdev.InputDevice(device)
        self.topic = topic + self.device.name.replace(" ","_") + "/"
        self.tele_period = tele_period

        # Inizializza lo stato dei tasti
        ak = self.device.active_keys()
        for k in self.device.capabilities()[1]:
            k_topic = self.topic + "KEY_" + str(k)
            if k not in ak: 
                self.mqttclient.publish(k_topic, "up" )
            else:
                self.mqttclient.publish(k_topic, "down" )

        log("Monitoring %s and sending to topic %s" % (device, self.topic))

        

    def run(self):
        #global key_state

        # Grab the input device to avoid keypresses also going to the
        # Linux console (and attempting to login)
        self.device.grab()
        t_start = datetime.datetime.now()

        while(True):
            event = self.device.read_one()
            if event != None: 
                if event.type == evdev.ecodes.EV_KEY:
                    key_code = "KEY_" + str(event.code)
                    
                    if event.value == 0: key_value = 'up'
                    elif event.value == 1: key_value = 'down'
                    elif event.value == 2: key_value = 'hold'
                    else : key_value = 'unknow'
    
                    self.mqttclient.publish(self.topic + key_code, key_value)
                    # log what we publish
                    log("Device '%s', published message %s" %
                                (self.device.path, key_code + "=" +  key_value))
            else:
                t_delta = datetime.datetime.now() - t_start
                if t_delta.total_seconds() >= self.tele_period:
                    t_start = datetime.datetime.now() 

                    ak = self.device.active_keys()
                    for k in self.device.capabilities()[1]:
                        k_topic = self.topic + "KEY_" + str(k)
                        if k not in ak: 
                            self.mqttclient.publish(k_topic, "up" )
                        else:
                            self.mqttclient.publish(k_topic, "down" )
                        sleep(0.100)
                        


        # for event in self.device.read_loop():
        #     if event.type == evdev.ecodes.EV_KEY:
                
        #         key_code = "KEY_" + str(event.code)
                
        #         if event.value == 0: key_value = 'up'
        #         elif event.value == 1: key_value = 'down'
        #         elif event.value == 2: key_value = 'hold'
        #         else : key_value = 'unknow'
 
        #         self.mqttclient.publish(self.topic + key_code, key_value)
        #         # log what we publish
        #         log("Device '%s', published message %s" %
        #                       (self.device.path, key_code + "=" +  key_value))

if __name__ == "__main__":

    try:
        Watcher()


        config_filename = "config.local.json"
        config_file = Path(config_filename)
        if not config_file.is_file():
            config_filename = "config.json"

        log("Loading config from '%s'" % config_filename)
        MQTTCFG = json.load(
            open(config_filename)
        )

        CLIENT = "evmqtt_{hostname}_{time}".format(
            hostname=hostname(), time=time()
        )

        MQ = MQTTClient(CLIENT, MQTTCFG)
        MQ.start()

        if MQTTCFG["topic"][-1] != "/":
            MQTTCFG["topic"] = MQTTCFG["topic"] + "/"

        topic = MQTTCFG["topic"]
        devices = MQTTCFG["devices"]
        tele_period = MQTTCFG["teleperiod"]
        available_devices = [evdev.InputDevice(
            path) for path in evdev.list_devices()]
        log("Found %s available devices:" % len(available_devices))
        for device in available_devices:
            log("Path:'%s', Name: '%s'" % (device.path, device.name))

        IM = [InputMonitor(MQ.mqttclient, device, topic, tele_period) for device in devices]

        for monitor in IM:
            monitor.start()

    except (OSError, KeyError) as er:
        log("Exception: %s" % er)
