#!/usr/bin/env python3

import logging
import os
import signal
import threading

import aprslib
import geopy.distance
import requests

from apscheduler.schedulers.background import BlockingScheduler
from datetime import datetime
from urllib.parse import urlparse, urlunparse
from requests.auth import HTTPBasicAuth
import json
import re
from aprs2emoji import aprs2emoji

DEFAULT_APRS_HOST = 'rotate.aprs.net'
DEFAULT_APRS_PORT = 14580
DEFAULT_TRACCAR_HOST = 'http://traccar:8082'
DEFAULT_TRACCAR_KEYWORD = 'aprs'
DEFAULT_TRACCAR_INTERVAL = 60
MSG_FORMATS = ['compressed', 'uncompressed', 'mic-e']

LOGGER = logging.getLogger(__name__)



class AprsPayloadHistory():
    def __init__(self):
        self.hist = {}
        
    def duplicate(self, payload, dt = datetime.now()):

        callsign = payload.split('>')[0]
        payloadpath = payload.split('>')[1].split(':')[0]
        payloaddata = payload.split(':')[1] 
        dict_callsign = self.hist.get(callsign, {})

        # clean old data
        for k in list(dict_callsign.keys()):
            for j in list(dict_callsign[k].keys()):
                # print(j, (dt - dict_callsign[k][j]).total_seconds())
                if (dt - dict_callsign[k][j]).total_seconds() > 1800:
                    del dict_callsign[k][j]
            if not list(dict_callsign[k].keys()):
                del dict_callsign[k]


        dict_path = dict_callsign.get(payloaddata)
        if not dict_path:
            # if payload doesn't exists, it can't be duplicate
            dict_path = {}
            exitstatus = False
        else:
            if dict_path.get(payloadpath):
                # if path exists, payload is not duplicate
                dict_path = {}
                exitstatus = False
            else:
                exitstatus = True
        
        dict_path[payloadpath] = dt
        dict_callsign[payloaddata] = dict_path
        self.hist[callsign] = dict_callsign
        
        return(exitstatus)
        


class AprsListenerThread(threading.Thread):
    # APRS message listener.

    def __init__(self, aprs_callsign: str, aprs_host: str, aprs_filter_dict: dict, traccar_osmand: str):
        # Initialize the class.
        super().__init__()

        self.aprs_callsign = aprs_callsign
        self.aprs_filter_dict = aprs_filter_dict
        self.traccar_osmand = traccar_osmand
        self.ais = aprslib.IS(aprs_callsign, host=aprs_host, port=DEFAULT_APRS_PORT)
        self.aph = AprsPayloadHistory()

    def run(self):
        # Connect to APRS and listen for data.
        self.setfilter(self.aprs_filter_dict)

        try:
            LOGGER.info(f"Opening connection to APRS with callsign {self.aprs_callsign}.")
            self.ais.connect()
            self.ais.consumer(callback=self.rx_msg, immortal=True)
        except OSError:
            LOGGER.info(f"Closing connection to APRS with callsign {self.aprs_callsign}.")

    def stop(self):
        # Close the connection to the APRS network.
        LOGGER.debug(f"stop()")
        self.ais.close()
        # self.join()

    def setfilter(self, aprs_filter_dict: dict):
        self.aprs_filter_dict = aprs_filter_dict
        filter = ("b/%s" % "/".join(sorted(list(aprs_filter_dict.keys()))))
        self.ais.set_filter(filter)

    def gps_accuracy(self, gps, posambiguity: int) -> int:
        # Calculate the GPS accuracy based on APRS posambiguity.
        pos_a_map = {0: 0,
                    1: 1 / 600,
                    2: 1 / 60,
                    3: 1 / 6,
                    4: 1}
        if posambiguity in pos_a_map:
            degrees = pos_a_map[posambiguity]

            gps2 = (gps[0], gps[1] + degrees)
            dist_m = geopy.distance.distance(gps, gps2).m
            accuracy = round(dist_m)
        else:
            message = "APRS position ambiguity must be 0-4, not '{0}'.".format(
                posambiguity)
            raise ValueError(message)
        return accuracy

    def tx_to_traccar(self, query: str):
        # Send position report to Traccar server
        LOGGER.debug(f"tx_to_traccar({query})")
        url = f"{self.traccar_osmand}/?{query}"
        try:
            post = requests.post(url)
            logging.debug(f"POST {post.status_code} {post.reason} - {post.content.decode()}")
            if post.status_code == 400:
                logging.warning(
                    f"{post.status_code}: {post.reason}. Please create device with matching identifier on Traccar server.")
                raise ValueError(400)
            elif post.status_code > 299:
                logging.error(f"{post.status_code} {post.reason} - {post.content.decode()}")
        except OSError:
            logging.exception(f"Error sending to {url}")

    def rx_msg(self, msg: dict):
        # Receive message and process if position.
        LOGGER.debug("APRS message received: %s", str(msg))

        if msg['format'] in MSG_FORMATS:

            if self.aph.duplicate(msg['raw']):
                logging.debug(f"Duplicate position packet: {msg['raw']}")
            else:
                lat = msg['latitude']
                lon = msg['longitude']

                query_string = ""

                if 'posambiguity' in msg:
                    pos_amb = msg['posambiguity']
                    try:
                        query_string += f"&accuracy={self.gps_accuracy((lat, lon), pos_amb)}"
                    except ValueError:
                        LOGGER.warning(f"APRS message contained invalid posambiguity: {pos_amb}")

                for attr in ['altitude', 'speed', 'course']:
                    if attr in msg:
                        #traccar needs bearing instead of course
                        query_string += f"&{attr.replace('course','bearing')}={msg[attr]}"

                # extra attributes
                for attr in ['from', 'to', 'via', 'symbol', 'symbol_table', 'comment']:
                    if attr in msg:
                        query_string += f"&APRS_{attr}={msg[attr]}"
                        
                # path
                for attr in ['path']:
                    if attr in msg:
                        query_string += f"&APRS_{attr}={','.join(msg[attr])}"

                # icon
                query_string += f"&APRS_icon=%s" % aprs2emoji(msg['symbol_table'],msg['symbol'])

                dev_ids = self.aprs_filter_dict.get(msg['from'])
                for dev_id in dev_ids:
                    query_fullstring = f"id={dev_id}&lat={lat}&lon={lon}" + query_string
                    try:
                        self.tx_to_traccar(query_fullstring)
                    except ValueError:
                        logging.warning(f"id={dev_id}")




class APRS2Traccar():
    def __init__(self, conf: dict):
        # Initialize the class.
        super().__init__()

        self.TraccarHost = conf.get("TraccarHost")
        self.TraccarUser = conf.get("TraccarUser")
        self.TraccarPassword = conf.get("TraccarPassword")
        self.TraccarKeyword = conf.get("TraccarKeyword")
        self.AprsCallsign = conf.get("AprsCallsign")
        self.AprsHost = conf.get("AprsHost")
        self.TraccarOsmand = conf.get("TraccarOsmand")

        self.ALT = None
        self.lastfilterdict = []
    
    def poll(self):
        page = requests.get(self.TraccarHost + "/api/devices?all=true", auth = HTTPBasicAuth(self.TraccarUser, self.TraccarPassword))
        if page.status_code != 200:
            LOGGER.info("Traccar auth failed")
            return

        filterdict={}
        for j in json.loads(page.content):
            # print(json.dumps(j, indent=2))
            if not j["disabled"]:
                attributes = j["attributes"]

                for att, value in attributes.items():
                    if re.search("^" + self.TraccarKeyword + "[0-9]{0,1}$", att.lower()):
                        callsign = value.upper().strip()
                        if re.search("^[A-Z]{1,2}[0-9][A-Z]{1,3}(-[A-Z0-9]{1,2}){0,1}$", callsign):
                            unid = j["uniqueId"]
                            filterdict[callsign] = filterdict.get(callsign, []) + [unid]

        LOGGER.debug(f"Attributes: {filterdict}")

        # check if it's running
        if self.ALT is None or not self.ALT.is_alive():
            if filterdict:
                # if it's not running and must run, start it
                self.ALT = AprsListenerThread(self.AprsCallsign, self.AprsHost, filterdict, self.TraccarOsmand)
                self.ALT.start()   
        else:
            if filterdict:
                # if it's running and must run, check the filter
                if filterdict != self.lastfilterdict:
                    self.ALT.setfilter(filterdict)

            else:
                # if it's running and mustn't run, stop it
                self.ALT.stop()
                self.ALT = None

        self.lastfilterdict = filterdict
    





if __name__ == '__main__':
    log_level = os.environ.get("LOG_LEVEL", "INFO")

    logging.basicConfig(level=log_level)


    def sig_handler(sig_num, frame):
        logging.debug(f"Caught signal {sig_num}: {frame}")
        logging.info("Exiting program.")
        exit(0)

    signal.signal(signal.SIGTERM, sig_handler)
    signal.signal(signal.SIGINT, sig_handler)

    def OsmandURL(url):
        u = urlparse(url)
        u = u._replace(scheme="http", netloc=u.hostname+":5055", path = "")
        return(urlunparse(u))

    config = {}
    config["TraccarHost"] = os.environ.get("TRACCAR_HOST", DEFAULT_TRACCAR_HOST)
    config["TraccarUser"] = os.environ.get("TRACCAR_USER", "")
    config["TraccarPassword"] = os.environ.get("TRACCAR_PASSWORD", "")
    config["TraccarKeyword"] = os.environ.get("TRACCAR_KEYWORD", DEFAULT_TRACCAR_KEYWORD)
    config["TraccarInterval"] = int(os.environ.get("TRACCAR_INTERVAL", DEFAULT_TRACCAR_INTERVAL))
    config["AprsCallsign"] = os.environ.get("APRS_CALLSIGN")
    config["AprsHost"] = os.environ.get("APRS_HOST", DEFAULT_APRS_HOST)
    config["TraccarOsmand"] = os.environ.get("TRACCAR_OSMAND", OsmandURL(config["TraccarHost"]))
    
    if not config["AprsCallsign"]:
        logging.fatal("Please provide your callsign to login to the APRS server.")
        exit(1)

    A2T = APRS2Traccar(config)

    logging.getLogger('apscheduler.executors.default').setLevel(logging.WARNING)
    sched = BlockingScheduler()
    sched.add_job(A2T.poll, 'interval', next_run_time=datetime.now(), seconds=config["TraccarInterval"])
    sched.start()



