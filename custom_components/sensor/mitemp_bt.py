"""
Support for Xiaomi Mi Flora BLE plant sensor.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/sensor.miflora/
"""
import logging
import urllib.request
import base64
import time
from datetime import datetime, timedelta
from threading import Lock, current_thread
import re
from subprocess import PIPE, Popen, TimeoutExpired
import logging
import time
import signal
import os
import voluptuous as vol

from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.helpers.entity import Entity
import homeassistant.helpers.config_validation as cv
from homeassistant.const import (
    CONF_FORCE_UPDATE, CONF_MONITORED_CONDITIONS, CONF_NAME, CONF_MAC
)


REQUIREMENTS = ['miflora==0.3.0']

_LOGGER = logging.getLogger(__name__)
LOCK = Lock()

CONF_ADAPTER = 'adapter'
CONF_CACHE = 'cache_value'
CONF_MEDIAN = 'median'
CONF_RETRIES = 'retries'
CONF_TIMEOUT = 'timeout'

DEFAULT_ADAPTER = 'hci0'
DEFAULT_UPDATE_INTERVAL = 1200
DEFAULT_FORCE_UPDATE = False
DEFAULT_MEDIAN = 3
DEFAULT_NAME = 'Mi Temp Bluetooth'
DEFAULT_RETRIES = 2
DEFAULT_TIMEOUT = 10

MI_TEMPERATURE = "temperature"
MI_HUMIDITY = "humidity"
MI_BATTERY = "battery"


# Sensor types are defined like: Name, units
SENSOR_TYPES = {
    'temperature': ['Temperatura', '°C'],
    'humidity': ['Umidità', '%'],
    'battery': ['Batteria', '%'],
}

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_MAC): cv.string,
    vol.Optional(CONF_MONITORED_CONDITIONS, default=list(SENSOR_TYPES)):
        vol.All(cv.ensure_list, [vol.In(SENSOR_TYPES)]),
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Optional(CONF_MEDIAN, default=DEFAULT_MEDIAN): cv.positive_int,
    vol.Optional(CONF_FORCE_UPDATE, default=DEFAULT_FORCE_UPDATE): cv.boolean,
    vol.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): cv.positive_int,
    vol.Optional(CONF_RETRIES, default=DEFAULT_RETRIES): cv.positive_int,
    vol.Optional(CONF_CACHE, default=DEFAULT_UPDATE_INTERVAL): cv.positive_int,
    vol.Optional(CONF_ADAPTER, default=DEFAULT_ADAPTER): cv.string,
})

def write_readnotif_ble(mac, handle, value, retries=3, timeout=20, adapter='hci0'):
    """
    Write to a BLE address

    @param: mac - MAC address in format XX:XX:XX:XX:XX:XX
    @param: handle - BLE characteristics handle in format 0xXX
    @param: value - value to write to the given handle
    @param: timeout - timeout in seconds
    """

    global LOCK
    attempt = 0
    delay = 10
    _LOGGER.debug("Enter write_readnotif_ble (%s)", current_thread())

    while attempt <= retries:
        cmd = "gatttool --device={} --char-write-req -a {} -n {} --adapter={} --listen".format(mac,
                                                                                    handle,
                                                                                    value,
                                                                                    adapter)
        with LOCK:
            _LOGGER.debug("Created lock in thread %s",
                         current_thread())
            _LOGGER.debug("Running gatttool with a timeout of %s",
                         timeout)

            with Popen(cmd,
                       shell=True,
                       stdout=PIPE,
                       preexec_fn=os.setsid) as process:
                try:
                    result = process.communicate(timeout=timeout)[0]
                    _LOGGER.debug("Finished gatttool")
                except TimeoutExpired:
                    # send signal to the process group
                    os.killpg(process.pid, signal.SIGINT)
                    result = process.communicate()[0]
                    _LOGGER.debug("Killed hanging gatttool")

        _LOGGER.debug("Released lock in thread %s", current_thread())

        result = result.decode("utf-8").strip(' \n\t')
        _LOGGER.debug("Got %s from gatttool", result)
        # Parse the output
        res = re.search("( [0-9a-fA-F][0-9a-fA-F])+", result)
        if res:
            _LOGGER.debug(
                "Exit write_readnotif_ble with result (%s)", current_thread())
                
            return [int(x, 16) for x in res.group(0).split()]

        attempt += 1
        _LOGGER.debug("Waiting for %s seconds before retrying", delay)
        if attempt < retries:
            time.sleep(delay)
            delay *= 2

    _LOGGER.debug("Exit write_readnotif_ble, no data (%s)", current_thread())
    return None


def read_ble(mac, handle, retries=3, timeout=20, adapter='hci0'):
    """
    Read from a BLE address

    @param: mac - MAC address in format XX:XX:XX:XX:XX:XX
    @param: handle - BLE characteristics handle in format 0xXX
    @param: timeout - timeout in seconds
    """

    global LOCK
    attempt = 0
    delay = 10
    _LOGGER.debug("Enter read_ble (%s)", current_thread())

    while attempt <= retries:
        cmd = "gatttool --device={} --char-read -a {} --adapter={}".format(mac,
                                                                         handle,
                                                                         adapter)
        with LOCK:
            _LOGGER.debug("Created lock in thread %s",
                         current_thread())
            _LOGGER.debug("Running gatttool with a timeout of %s",
                         timeout)

            with Popen(cmd,
                       shell=True,
                       stdout=PIPE,
                       preexec_fn=os.setsid) as process:
                try:
                    result = process.communicate(timeout=timeout)[0]
                    _LOGGER.debug("Finished gatttool")
                except TimeoutExpired:
                    # send signal to the process group
                    os.killpg(process.pid, signal.SIGINT)
                    result = process.communicate()[0]
                    _LOGGER.debug("Killed hanging gatttool")

        _LOGGER.debug("Released lock in thread %s", current_thread())

        result = result.decode("utf-8").strip(' \n\t')
        _LOGGER.debug("Got %s from gatttool", result)
        # Parse the output
        res = re.search("( [0-9a-fA-F][0-9a-fA-F])+", result)
        if res:
            _LOGGER.debug(
                "Exit read_ble with result (%s)", current_thread())
            return [int(x, 16) for x in res.group(0).split()]

        attempt += 1
        _LOGGER.debug("Waiting for %s seconds before retrying", delay)
        if attempt < retries:
            time.sleep(delay)
            delay *= 2

    _LOGGER.debug("Exit read_ble, no data (%s)", current_thread())
    return None


class MijiaPoller(object):
    """"
    A class to read data from Xiaomi Mijia Bluetooth sensors.
    """

    def __init__(self, mac, cache_timeout=600, retries=3, adapter='hci0'):
        """
        Initialize a Xiaomi Mijia Poller for the given MAC address.
        """

        self._mac = mac
        self._adapter = adapter
        self._cache = None
        self._cache_timeout = timedelta(seconds=cache_timeout)
        self._last_read = None
        self._fw_last_read = datetime.now()
        self.retries = retries
        self.ble_timeout = 10
        self.lock = Lock()
        self._firmware_version = None
        self._battery_level = None
        self._bat_last_read = datetime.now()

    def name(self):
        """
        Return the name of the sensor.
        """
        name = read_ble(self._mac, "0x03",
                        retries=self.retries,
                        timeout=self.ble_timeout,
                        adapter=self._adapter)
        return ''.join(chr(n) for n in name)

    def fill_cache(self):
        firmware_version = self.firmware_version()
        if not firmware_version:
            # If a sensor doesn't work, wait 5 minutes before retrying
            self._last_read = datetime.now() - self._cache_timeout + \
                timedelta(seconds=300)
            return

        self._cache = write_readnotif_ble(self._mac, "0x10", "0100", retries=self.retries, timeout=self.ble_timeout, adapter=self._adapter)
		
        self._check_data()
        if self._cache is not None:
            self._last_read = datetime.now()
        else:
            # If a sensor doesn't work, wait 5 minutes before retrying
            self._last_read = datetime.now() - self._cache_timeout + \
                timedelta(seconds=300)

    def battery_level(self):
        """
        Return the battery level.
        """
        if (self._battery_level is None) or \
                (datetime.now() - timedelta(hours=1) > self._bat_last_read):
            self._bat_last_read = datetime.now()
            res = read_ble(self._mac, '0x18', retries=self.retries, adapter=self._adapter)
            if res is None:
                self._battery_level = 0
            else:
                self._battery_level = res[0]
        return self._battery_level

    def firmware_version(self):
        """ Return the firmware version. """
        if (self._firmware_version is None) or \
                (datetime.now() - timedelta(hours=24) > self._fw_last_read):
            self._fw_last_read = datetime.now()
            res = read_ble(self._mac, '0x24', retries=self.retries, adapter=self._adapter)
            if res is None:
                self._firmware_version = None
            else:
                self._firmware_version = "".join(map(chr, res))
        return self._firmware_version

    def parameter_value(self, parameter, read_cached=True):
        """
        Return a value of one of the monitored paramaters.

        This method will try to retrieve the data from cache and only
        request it by bluetooth if no cached value is stored or the cache is
        expired.
        This behaviour can be overwritten by the "read_cached" parameter.
        """

        _LOGGER.debug("Call to parameter_value (%s)",parameter)
		
        # Special handling for battery attribute
        if parameter == MI_BATTERY:
            return self.battery_level()

        # Use the lock to make sure the cache isn't updated multiple times
        with self.lock:
            if (read_cached is False) or \
                    (self._last_read is None) or \
                    (datetime.now() - self._cache_timeout > self._last_read):
                self.fill_cache() 
            else:
                _LOGGER.debug("Using cache (%s < %s)",
                             datetime.now() - self._last_read,
                             self._cache_timeout)

        if self._cache and (len(self._cache) == 14):
            return self._parse_data()[parameter]
        else:
            raise IOError("Could not read data from Mi sensor %s",
                          self._mac)

    def _check_data(self):
        if self._cache is None:
            return
        datasum = 0
        for i in self._cache:
            datasum += i
        if datasum == 0:
            self._cache = None

    def _parse_data(self):
        data = self._cache
        temp,humid = "".join(map(chr, data)).replace("T=", "").replace("H=", "").rstrip(' \t\r\n\0').split(" ")
        res = {}
        res[MI_TEMPERATURE] = temp
        res[MI_HUMIDITY] = humid
        return res


def setup_platform(hass, config, add_devices, discovery_info=None):
    cache = config.get(CONF_CACHE)
    poller = MijiaPoller(config.get(CONF_MAC), cache, config.get(CONF_RETRIES),
        adapter=config.get(CONF_ADAPTER))
    force_update = config.get(CONF_FORCE_UPDATE)
    median = config.get(CONF_MEDIAN)
    poller.ble_timeout = config.get(CONF_TIMEOUT)

    devs = []

    for parameter in config[CONF_MONITORED_CONDITIONS]:
        name = SENSOR_TYPES[parameter][0]
        unit = SENSOR_TYPES[parameter][1]

        prefix = config.get(CONF_NAME)
        if prefix:
            name = "{} {}".format(prefix, name)

        devs.append(MiTempBtSensor(
            poller, parameter, name, unit, force_update, median))

    add_devices(devs)


class MiTempBtSensor(Entity):
    """Implementing the MiFlora sensor."""

    def __init__(self, poller, parameter, name, unit, force_update, median):
        """Initialize the sensor."""
        self.poller = poller
        self.parameter = parameter
        self._unit = unit
        self._name = name
        self._state = None
        self.data = []
        self._force_update = force_update
        # Median is used to filter out outliers. median of 3 will filter
        # single outliers, while  median of 5 will filter double outliers
        # Use median_count = 1 if no filtering is required.
        self.median_count = median

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def unit_of_measurement(self):
        """Return the units of measurement."""
        return self._unit

    @property
    def force_update(self):
        """Force update."""
        return self._force_update

    def update(self):
        """
        Update current conditions.

        This uses a rolling median over 3 values to filter out outliers.
        """
        try:
            _LOGGER.debug("Polling data for %s", self.name)
            data = self.poller.parameter_value(self.parameter)
        except IOError as ioerr:
            _LOGGER.info("Polling error %s", ioerr)
            return

        if data is not None:
            _LOGGER.debug("%s = %s", self.name, data)
            self.data.append(data)
        else:
            _LOGGER.info("Did not receive any data from Mi Flora sensor %s",
                         self.name)
            # Remove old data from median list or set sensor value to None
            # if no data is available anymore
            if self.data:
                self.data = self.data[1:]
            else:
                self._state = None
            return

        _LOGGER.debug("Data collected: %s", self.data)
        if len(self.data) > self.median_count:
            self.data = self.data[1:]

        if len(self.data) == self.median_count:
            median = sorted(self.data)[int((self.median_count - 1) / 2)]
            _LOGGER.debug("Median is: %s", median)
            self._state = median
        else:
            _LOGGER.debug("Not yet enough data for median calculation")
