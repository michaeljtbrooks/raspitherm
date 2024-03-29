#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
    Raspitherm - HTTP Listener
    
    
    GPIO 22    Input    Detect hot water state
    GPIO 27    Input    Detect central heating state
    GPIO 5     Output   Toggle hot water
    GPIO 26    Output   Toggle central heating

        Listens on HTTP port 9090 for commands. Passes them on to any classes
        running. 
    
        @requires: twisted
"""
from __future__ import unicode_literals
from utils import Odict2int, SmartRequest, get_matching_pids

try:
    #python2
    from urllib import urlencode
except ImportError:
    #python3
    from urllib.parse import urlencode
import configparser
import logging
import json
import os
from twisted.internet import reactor, endpoints
from twisted.web.resource import Resource
from twisted.web.server import Site
from twisted.web.static import File

from heating_controller import HeatingController


APP_NAME = "python ./raspitherm_listener.py"

logging.basicConfig(format='[%(asctime)s RASPITHERM] %(message)s',
                            datefmt='%H:%M:%S',level=logging.INFO)

RASPILED_DIR = os.path.dirname(os.path.realpath(__file__)) #The directory we're running in

DEFAULTS = {
        'config_path' : RASPILED_DIR,
        'pi_host'     : 'localhost',
        'pi_port'     : 9090,
        'pig_port'    : 8888,
        'hw_toggle_pin': 5,
        'cw_toggle_pin': 26,
        'hw_status_pin': 22,
        'cw_status_pin': 27,
        'pulse_duration_ms': 200,  # Duration of pulse
        'relay_delay_ms': 200,  # How long it takes for the relays to be thrown
    }

# Generate or read a config file.
config_path = os.path.expanduser(RASPILED_DIR+'/raspitherm.conf')
parser = configparser.ConfigParser(defaults=DEFAULTS)

if os.path.exists(config_path):
    logging.info('Using config file: {}'.format(config_path))
    parser.read(config_path)
else:
    logging.warn('No config file found. Creating default {} file.'.format(config_path))
    logging.warn('*** Please edit this file as needed. ***')
    parser = configparser.ConfigParser(defaults=DEFAULTS)
    with open(config_path, 'w') as f:
        parser.write(f)
CONFIG_SETTINGS = Odict2int(parser.defaults()) #Turn the Config file into settings


DEBUG = False


def D(item):
    if DEBUG:
        print(item)
        logging.debug(item)
        

class RaspithermControlResource(Resource):
    """
    Our web page for controlling the heating and seeing its status
    """
    isLeaf = False #Allows us to go into dirs
    heating_controller = None #Populated at init
    PARAM_TO_ACTION_MAPPING = (
        ("ch", "ch"),
        ("hw", "hw"),
        ("status", "status")
    )
    
    def __init__(self, *args, **kwargs):
        """
        @TODO: perform LAN discovery, interrogate the resources, generate controls for all of them
        """
        self.heating_controller = HeatingController(CONFIG_SETTINGS)
        Resource.__init__(self, *args, **kwargs) #Super
        # Add in the static folder
        static_folder = os.path.join(RASPILED_DIR, "static")
        self.putChild("static", File(static_folder))
    
    def getChild(self, path, request, *args, **kwargs):
        """
        Entry point for dynamic pages 
        """
        return self
    
    def getChildWithDefault(self, path, request):
        """
        Retrieve a static or dynamically generated child resource from me.

        First checks if a resource was added manually by putChild, and then
        call getChild to check for dynamic resources. Only override if you want
        to affect behaviour of all child lookups, rather than just dynamic
        ones.

        This will check to see if I have a pre-registered child resource of the
        given name, and call getChild if I do not.

        @see: L{IResource.getChildWithDefault}
        """
        if path in self.children:
            return self.children[path]
        return self.getChild(path, request)
    
    def render_GET(self, request):
        """
        Responds to GET requests
        """
        _action_result = None
        
        # Look through the actions if the request key exists, perform that action
        return_json = False
        for key_name, action_name in self.PARAM_TO_ACTION_MAPPING:
            if request.has_param(key_name):
                action_func_name = "action__%s" % action_name
                _action_result = getattr(self, action_func_name)(request) #Execute that function
                return_json = True
                break
        
        # Read our statuses:
        self.heating_controller.check_status()  # Actually reads from the pins and updates internal vars
        if self.heating_controller.hw:
            hw_status = "on"
            hw_status_js = 1
            hw_checked_attr = ' checked="checked"'
        else:
            hw_status = "off"
            hw_status_js = 0
            hw_checked_attr = ""
        if self.heating_controller.ch:
            ch_status = "on"
            ch_status_js = 1
            ch_checked_attr = ' checked="checked"'
        else:
            ch_status = "off"
            ch_status_js = 0
            ch_checked_attr = ""
        
        # Return a JSON object if a result:
        if _action_result is not None or return_json:
            json_data = {
                "hw_status_js": hw_status_js,
                "ch_status_js": ch_status_js,
                "hw_status": hw_status,
                "ch_status": ch_status,
                "hw": hw_status,
                "ch": ch_status,
            }
            try:
                return json.dumps(json_data)
            except:
                return b"Error: Json fkucked up"
        
        # Otherwise return normal page
        request.setHeader("Content-Type", "text/html; charset=utf-8")
        with open(RASPILED_DIR+'/templates/index.html', "r") as web_template:
            htmlstr = web_template.read()  # Reads en bloc. More preferable to line by line concatenation
        return htmlstr.format(
                hw_status=hw_status,
                hw_status_js=hw_status_js,
                ch_status=ch_status,
                ch_status_js=ch_status_js,
                hw_checked_attr=hw_checked_attr,
                ch_checked_attr=ch_checked_attr,
            ).encode('utf-8')
    
    def action__hw(self, request):
        """
        Run when user wants to set the heating on or off
        """
        intended_status = request.get_param("hw", force=unicode)
        outcome = self.heating_controller.set_hw(intended_status)
        logging.info("Turn hot water {}, status now: {}".format(intended_status, outcome))
        return outcome
    
    def action__ch(self, request):
        """
        Run when user wants to set the central heating on or off
        """
        intended_status = request.get_param("ch", force=unicode)
        outcome = self.heating_controller.set_ch(intended_status)
        logging.info("Turn central heating {}, status now: {}".format(intended_status, outcome))
        return outcome

    def action__status(self, request):
        """
        Report the status of the heating / hot water
        """
        outcome = self.heating_controller.check_status()
        return outcome
    
    def teardown(self):
        """
        Called automatically when exiting the parent reactor
        """
        self.heating_controller.teardown()


class RaspithermControlSite(Site, object):
    """
    Site thread which initialises the RaspithermControlResource properly
    """
    def __init__(self, *args, **kwargs):
        resource = kwargs.pop("resource", RaspithermControlResource())
        super(RaspithermControlSite, self).__init__(resource=resource, requestFactory=SmartRequest, *args, **kwargs)
    
    def stopFactory(self):
        """
        Called automatically when exiting the reactor. Here we tell the HeatingController class to tear down its resources
        """
        self.resource.teardown()
    
    
    
def start_if_not_running():
    """
    Checks if the process is running, if not, starts it!
    """
    pids = get_matching_pids(APP_NAME, exclude_self=True) #Will remove own PID
    pids = filter(bool,pids)
    if not pids: #No match! Implies we need to fire up the listener
        logging.info("[STARTING] Raspitherm Listener with PID {}".format(os.getpid()))
    
        factory = RaspithermControlSite(timeout=8) #8s timeout
        endpoint = endpoints.TCP4ServerEndpoint(reactor, CONFIG_SETTINGS['pi_port'])
        endpoint.listen(factory)
        reactor.run()
    else:
        logging.info("Rasptherm Listener already running with PID %s" % ", ".join(pids))


if __name__=="__main__":
    start_if_not_running()

