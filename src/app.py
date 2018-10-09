#! /usr/bin/env python3

"""

Title: Hoomaluo - Cool
Project: Alternative Cooling Controller
Author: Hoomaluo Labs
Version: 1.0
Date: 08-27-2018

Overview:
* Communicate with a server over MQTT:
    - report temperature and energy
        * store locally if disconnected
        * report local data once back online
    - receive control inputs
* Communicate with STM32 uController over RS232:
    -  send status, temp setpoint, and IR command
    -  receive power (ac and dc) and temperature as json object
* Read GPIO buttons for temperature setpoints
* Device modes:
    1: send ac and dc energy
    2: maybe something here for later

Packages:
* paho-mqtt ; for mqtt communication: https://pypi.python.org/pypi/paho-mqtt/1.1
* neo4j ; for later
* pandas
* numpy
* threading


"""

#import csv
#import pandas as pd
from time import sleep,time
import datetime
import paho.mqtt.client as mqtt
import json
import threading
import os
import sys
#from neo4j.v1 import GraphDatabase
from apscheduler.schedulers.background import BackgroundScheduler
from gpiozero import Button
import serial


""" serial handles
ser = serial.Serial('/dev/ttyUSB0')  # open serial port
ser.write(b'hello')     # write a string
x = ser.read()          # read one byte
s = ser.read(10)        # read up to ten bytes (timeout)
line = ser.readline()   # read a '\n' terminated line
ser.is_open
"""

#def parse_IRcodes(filename):
#    """ Parse IR codes from filename and return a dataframe """
#    return pd.read_csv(filename)

def c2f(c):
    return (9/5)*c+32

def f2c(c):
    return (5/9)*(F-32)

class Container:
    def __init__(self, serialConnection, IRcodesFile, setpoint=55, status=1):
        """ initialize variables """

        self.temperature = []
        self.ts = int(time())
        self.ace_accum = 0
        self.dce_accum = 0
        self.irms = []
        self.vrms = []
        self.watts = []
        self.ser = serialConnection

        with open(IRcodesFile) as json_data:
            self.IRcommands = json.load(json_data)


    def sendIRcode(self, mode, setpoint, endswith="transmit"):
        """ send the IR code """
        outString = ""
        for val in self.IRcommands[mode][setpoint]:
            outString += str(val)+ '?'
        outString += endswith
        self.sendBytesToSTM(outString.encode("utf-8"))


    def sendControls(self, status, tempset):
        """ send the temp setpoint and status """
        outString = str(status) + '?' + str(tempset) + '?control'
        self.sendBytesToSTM(outString.encode("utf-8"))

    def sendBytesToSTM(self, byteArray):
        if self.ser.is_open:
            if debug: print("Serial is open. Sending: ", byteArray)
            self.ser.write(byteArray)
        else:
            try:
                self.ser.open()
                if debug: print("Serial is open. Sending: ", byteArray)
                self.ser.write(byteArray)
            except:
                if debug: print("Cannot open port.")
                """ TODO: need some routine to try again if failed """

    def readSTM(self, ser):
        "read temp and energy from the STM ... comes in as a json object I think"
        while True:
            if ser.is_open:
                self.processReading(ser.read_until(), int(time())) # adjust character based on code
            else:
                try:
                    ser.open()
                    self.processReading(ser.read_until('\n'), int(time())) # adjust character based on code
                except:
                    if debug: print("Cannot read from port .")
                """ TODO: need some routine to try again if failed """

    def processReading(self, reading, ts, serialDebug=False):
        """ update energy accumulators based on power readings and time interval
        Sample:
        readings = u'{"temp":70.00,"temp2":70.00,"awatt":-0.01,"ava":-0.01,"apf":1.00,"avrms":73735.22,"airms":18318.55,"awatt2":-0.01,"ava2":-0.01,"apf2":1.00,"avrms2":18318.55,"bwatt":-0.01,"bva":-0.01,"bpf":1.00,"bvrms":73735.22,"birms":18318.55,"bwatt2":-0.01,"bva2":-0.01,"bpf2":1.00,"birms2":18318.55,"cwatt":-0.01,"cva":-0.01,"cpf":1.00,"cvrms":73735.22,"cirms":18318.55,"cwatt2":-0.01,"cva2":-0.01,"cpf2":1.00,"cirms2":18318.55,"dcp":0.00,"dcv":0.01,"dci":0.00,"dcp2":0.00,"dcv2":0.06,"dci2":0.01}'
        """
        # convert string to json
        if serialDebug:
            print(reading)
            print(type(reading))
        if isinstance(type(reading), str): a = json.loads(reading)
        else: a = json.loads(reading.decode("utf-8")) # turn json string into an object
        if serialDebug: print(a)
        # update temperature
        self.temperature.append(a['temp'])

        # get time interval
        timedelta = ts - self.ts
        self.ts = ts

        # calculate energies
        self.ace_accum = timedelta * (a['awatt'] + a['bwatt']) / 3600.0     # watt-hour
        self.dce_accum = timedelta * (a['dcp']) / 3600.0                    # watt-hour
        self.irms.append(a['airms']+a['birms'])
        self.vrms.append(a['avrms']+a['bvrms'])
        self.watts.append(a['awatt'] + a['bwatt'])

    def resetEnergyAccumulators(self):
        self.ace_accum = 0
        self.dce_accum = 0
        self.irms = []
        self.vrms = []
        self.watts = []

    def resetTempAccumulators(self):
        self.temperature = []


class Radio:
    def __init__(self, devId, custId, Controller):

        self.devId = devId
        self.custId = custId

        self.controller = Controller
        # subscriptions
        self.subControls = "maluo_1/set/"+custId+"/"+devId+"/ircontrol"
        self.subSettings = "maluo_1/set/"+custId+"/"+devId+"/info"

        # publishing
        self.pubEnergy = "maluo_1/metering/energy/"+custId+"/"+devId
        self.pubTemp = "maluo_1/metering/temperature/"+custId+"/"+devId
        self.pubControls = "maluo_1/set/"+custId+"/"+devId+"/ircontrol"

        self.storeLocalTemp = False
        self.midTemp = 0
        self.lastTempPayload = ""

        self.storeLocalEnergy = False
        self.midEnergy = 0
        self.lastEnergyPayload = ""

        self.storeLocalControls = False
        self.midControls = 0
        self.lastControlsPayload = ""

        # MQTT client
        self.client = mqtt.Client(devId)

        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        # Need to fix this to attempt reconnect
        try:
            self.client.connect("post.redlab-iot.net", 55100, 60)
            if debug: print("connection established")
        except:
            if debug: print("connection failed")

        self.client.loop_start()

    def on_connect(self, client, userdata, flags, rc):
        """ Callback function when client connects """
        # Subscribing in on_connect() means that if we lose the connection and
        # reconnect then subscriptions will be renewed.
        sleep(5)                # quick delay
        self.client.subscribe(self.subControls)
        self.client.subscribe(self.subSettings)

    def on_publish(self, client, userdata, mid):
        """ Callback function that's called on successful delivery (need qos 1 or 2 for this to make sense) """
        if debug: print("on_publish ... \nuserdata: ", userdata, "\nmid: ", mid)

        if mid == self.midTemp:
            self.storeLocalTemp = False
        elif mid == self.midEnergy:
            self.storeLocalEnergy = False
        elif mid == self.midControls:
            self.storeLocalControls = False

    def on_message(self, client, userdata, msg):
        """ Callback function when client receives message """

        data = json.loads(msg.payload.decode("utf-8"))
        if debug: print("topic: ", msg.topic, " payload:", data)
        #print "Received: ", data
        if msg.topic == self.subControls:
            self.controller.setpoint = int(data['temp'])
            status_old = self.controller.status
            if data['mode'] == "auto" or data['mode'] == "cool1" or data['mode'] == "cool2" or data['mode'] == "cool3":
                self.controller.status = 1
            elif data['mode'] == "off":
                self.controller.status = 0
            if status_old and self.controller.status: onoff = False
            elif status_old and not self.controller.status: onoff = True
            elif not status_old and self.controller.status: onoff = True
            else: onoff = False
            self.controller.updateControls(onoff = onoff, radio=False)

        elif msg.topic == self.subSettings :
            self.controller.temp_interval = int(data['temp-res'])
            self.controller.energy_interval = int(data['energy-res'])
            self.controller.updateIntervals()
        else:
            pass

    # update
    def sendTemperature(self):
        """ send measurement to self.pubTemp"""
        if len(self.controller.myContainer.temperature) != 0:
            temp = sum(self.controller.myContainer.temperature) / len(self.controller.myContainer.temperature)
        else:
            temp = 0
        payload = ('{"ts": '+ str(int(time())) +  ', "temp":' + str(temp) +
                    '"data": { "status": ' + str(self.controller.status) + ', "setpoint": '+ str(self.controller.setpoint) + ' }}' )
        res, self.midTemp = self.client.publish(self.pubTemp, payload, qos=1, retain=False)
        if debug: print("Sent: ", payload , "on", self.pubTemp, "mid: ", self.midTemp)
        self.controller.myContainer.resetTempAccumulators()

        filename = self.pubTemp.replace("/", "-") + ".txt"
        if self.storeTempLocal:
            f = open(filename, 'a+')
            f.write(self.lastTempPayload+"\n")
            f.close()
        self.storeLocalTemp = True
        self.lastTempPayload = payload

    def sendEnergy(self):
        """ send availability to self.pubEnergy """
        if len(self.controller.myContainer.vrms) != 0:
            vrms = sum(self.controller.myContainer.vrms) / len(self.controller.myContainer.vrms)
            irms = sum(self.controller.myContainer.irms) / len(self.controller.myContainer.irms)
            watts = sum(self.controller.myContainer.watts) / len(self.controller.myContainer.watts)
        else:
            vrms = irms = watts = 0
        payload = ('{"ts": '+ str(int(time())) +  ', "ace": ' + str(self.controller.myContainer.ace_accum)
                    + ', "dce": ' + str(self.controller.myContainer.dce_accum)+
                    ', "data": { "watt": ' + str(watts) + ', "vrms": '+ str(vrms) + ', "irms": '+ str(irms)  + ' }}' )

        res, self.midEnergy = self.client.publish(self.pubEnergy, payload, qos=1, retain=False)
        if debug: print("Sent: ", payload , "on", self.pubEnergy, "mid: ", self.midEnergy)
        self.controller.myContainer.resetEnergyAccumulators()
        filename = self.pubEnergy.replace("/", "-") + ".txt"
        if self.storeEnergyLocal:
            f = open(filename, 'a+')
            f.write(self.lastEnergyPayload+"\n")
            f.close()
        self.storeLocalEnergy = True
        self.lastEnergyPayload = payload

    def sendControls(self):
        """ send the manual control updates to the server """

        if self.controller.status:
            mode = '"cool3"'
            temp = self.controller.setpoint
        else:
            mode = '"off"'
            temp = self.controller.setpoint

        payload = '{"mode": ' + mode + ', "temp": ' + str(temp) + '}'
        res, self.midControls = self.client.publish(self.pubControls, payload, qos=1, retain=False)
        if debug: print("Sent", payload, "on", self.pubControls, "mid: ", self.midControls)
        filename = self.pubTemp.replace("/", "-") + ".txt"
        if self.storeControlsLocal:
            f = open(filename, 'a+')
            f.write(self.lastControlsPayload+"\n")
            f.close()
        self.storeControlsTemp = True
        self.lastControlsPayload = payload

class Controller:
    def __init__(self):

        #devId = os.environ["devId"]
        self.devId = "101"  # temporary
        self.custId = "101" # temporary
        self.serPort = "/dev/ttyACM0" # python -m serial.tools.list_ports
        self.ser = serial.Serial(self.serPort)  # open serial port

        self.status = 1                 # will be updated on restart
        self.setpoint = 55              # will be updated on restart
        self.temp_interval = 1     # 15 min
        self.energy_interval = 1      # 15 min

        self.myContainer = Container(self.ser, "IRJSON.json", self.setpoint, self.status)
        self.myRadio = Radio(self.devId, self.custId, self)

        self.scheduler = BackgroundScheduler({'apscheduler.timezone': 'UTC',})
        self.addJobs()
        self.scheduler.start()

    def addJobs(self):
        if debug: print("added jobs")
        self.tempReporter = self.scheduler.add_job(self.myRadio.sendTemperature,
                                'interval',
                                minutes=1)
        self.energyReporter = self.scheduler.add_job(self.myRadio.sendEnergy,
                                'interval',
                                minutes=self.energy_interval)


    def updateControls(self, onoff=False, radio=True):
        """ update the control settings """
        self.myContainer.sendControls(self.status, self.setpoint)
        if onoff and self.status: self.myContainer.sendIRcode("cool3", "62")
        elif onoff and not self.status: self.myContainer.sendIRcode("off", "0")
        if radio:
            self.myRadio.sendControls()

    def updateIntervals(self):
        """ update the intervals for sending temperature and energy """
        for job in self.scheduler.get_jobs():
            job.remove()
        self.addJobs()

    def buttonUpPushed(self):
        if debug: print("Up button pushed!")
        self.setpoint += 1
        self.updateControls()

    def buttonDownPushed(self):
        if debug: print("Down button pushed!")
        self.setpoint -= 1
        self.updateControls()

    def buttonOnPushed(self):
        if debug: print("On button pushed")
        self.status = abs(self.status - 1)
        self.updateControls(True)

def main():
    global debug
    debug = True
    myController = Controller()

    onButton = Button(5)
    upButton = Button(11)
    downButton = Button(9)
    onButton.when_pressed = myController.buttonOnPushed
    upButton.when_pressed = myController.buttonUpPushed
    downButton.when_pressed = myController.buttonDownPushed

    ser_thread = threading.Thread(target=myController.myContainer.readSTM, args = [myController.ser])
    print("start serial read thread")
    ser_thread.start()


    try:
        while True:
            sleep(10)

    except (KeyboardInterrupt, SystemExit):
        # Not strictly necessary if daemonic mode is enabled but should be done if possible
        myController.scheduler.shutdown()

if __name__ == "__main__":
    main()
