"""Factorio metrics plugin"""
# pylint: disable=logging-format-interpolation
import logging
import re

import factorio_rcon

from common_lib import BaseStat  # pylint: disable=no-name-in-module


class FactorioStats(BaseStat):
    """Factorio metrics: playercount logged every second for each server provided"""
    name = "Factorio"
    def __init__(self):
        self.out_data = {"measurement": "factorio"}
        self.connections_details = {
            "my_server": {"args": ["127.0.0.1", 12345, "my_password"], "failures": 0},
            "server_2": {"args": ["127.0.0.1", 12346, "my_password"], "failures": 0}
        }
        self.connections = {}
        self.playercount_regex = re.compile(r"(?!\()\d+(?=\))")
        self.connect()


    def connect(self, server=None):
        """Connect to factorio RCON"""
        if server is not None:
            try:
                self.connections[server] = factorio_rcon.RCONClient(
                    *self.connections_details[server]["args"]
                )
                LOGGER.info("Reconnect successful")
            except ConnectionError:
                LOGGER.warning("Connection to {0} rejected".format(server))
            return
        for key, value in self.connections_details.items():
            try:
                self.connections[key] = factorio_rcon.RCONClient(*value["args"])
            except ConnectionError:
                self.connections[key] = None
                LOGGER.warning("Connection to {0} rejected".format(key))

    async def get_stats(self):
        """Fetches playercount"""
        self.out_data = {"measurement": "factorio"}
        for key, value in self.connections.items():
            try:
                response = value.send_command("/p o c")
                response = int(self.playercount_regex.search(response).group(0))
                self.out_data["{0}_playercount".format(key)] = response
            except Exception:
                LOGGER.debug("Playercount command failed for {0}".format(key))
                self.connections_details[key]["failures"] += 1
                if self.connections_details[key]["failures"] > 60 / self.save_rate:
                    LOGGER.info("{0} failed to fetch playercount for 60s, attempting reconnect"
                                .format(key))
                    self.connections_details[key]["failures"] = 0
                    self.connect(server=key)

LOGGER = logging.getLogger("factorio_metrics")

ACTIVATED_METRICS = [FactorioStats]
