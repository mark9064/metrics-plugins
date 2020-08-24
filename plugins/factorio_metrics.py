"""Factorio metrics plugin"""
# pylint: disable=logging-format-interpolation
import functools
import logging
import re
import sys
import time

import factorio_rcon
import trio

from common_lib import BaseStat, format_error # pylint: disable=no-name-in-module


class RCONConnection(BaseStat):
    """Connection to an RCON server

    Params:
        name: str; human readable server name.
        ip_address: str; IP address to connect to.
        port: int; port to connect to.
        password: str; password to use to authenticate.
        level (optional, default logging.WARNING): int; maximum loglevel to use
    Raises:
        No specific exceptions.
    Extra information:
        If a connection rejection, connect error or timeout occurs, it will be logged
        with the loglevel provided in 'level'. An error during receiving or sending (e.g
        RCON socket locked or an incorrect response) will always log
        with the logging.ERROR level. Informational messages will be logged with
        the logging.INFO level or 'level', whichever is lower.
    """
    def __init__(self, name, ip_address, port, password, level=logging.WARNING):
        self.name = name
        self.ip_address = ip_address
        self.port = port
        self.password = password
        self.level = level
        if level > logging.INFO:
            self.info_level = logging.INFO
        else:
            self.info_level = level
        self.reconnect_failures = 0
        self.rcon_client = None
        self.last_connect_attempt_time = 0
        self.results = {}
        self.persistent = {}
        self.playercount_regex = re.compile(r"(?!\()\d+(?=\))")

    def time_down(self):
        """Calculates the time that the server has been down"""
        return self.reconnect_failures * self.collect_interval

    async def connect(self, initial=False):
        """Connects to the RCON server"""
        try:
            if self.rcon_client is None:
                self.rcon_client = factorio_rcon.AsyncRCONClient(
                    self.ip_address, self.port, self.password, connect_on_init=False
                )
            with trio.fail_after(0.5):
                await self.rcon_client.connect()
        except (ConnectionError, trio.TooSlowError):
            LOGGER.log(self.level, "Connection to {0} rejected".format(self.name))
            self.rcon_client = None
        else:
            if initial:
                message = "Connect"
            else:
                message = "Reconnect"
            LOGGER.log(self.info_level, "{0} to {1} successful".format(message, self.name))
            self.reconnect_failures = 0
            self.last_connect_attempt_time = 0

    async def fetch_queries(self, queries):
        """Peforms the queries requested"""
        self.results = {}
        if self.rcon_client is None:
            self.reconnect_failures += 1
            if self.time_down() >= self.last_connect_attempt_time + 60:
                self.last_connect_attempt_time = self.time_down()
                LOGGER.log(self.info_level,
                           "Attempting to reconnect to {0} "
                           "({1}s since last successful connection)"
                           .format(self.name, self.time_down()))
                await self.connect()
                if self.rcon_client is None:
                    return
            else:
                return
        results = None
        with trio.move_on_after(0.5):
            try:
                results = await self.rcon_client.send_commands(queries)
            except Exception:
                results = sys.exc_info()
        response_time = time.time()
        if results is None or isinstance(results, tuple):
            if results is None:
                LOGGER.log(self.level, "RCON connection to {0} timed out".format(self.name))
            else:
                LOGGER.error("Error executing RCON commands for {0}: {1}"
                             .format(self.name, format_error(results)))
            await self.connect()
            return
        results["time"] = response_time
        await self.process_results(results)

    async def process_results(self, queries):
        """Processes the results of queries"""
        response_time = queries.pop("time")
        results = {}
        for command, response in queries.items():
            if command == "player_count":
                response = int(self.playercount_regex.search(response).group(0))
                results["playercount"] = response
            elif command == "tick":
                response = int(response)
                if "ups" in self.persistent:
                    time_delta = response_time - self.persistent["ups"][0]
                    tick_delta = response - self.persistent["ups"][1]
                    self.persistent["ups"] = [response_time, response]
                    if tick_delta >= 0: # check the number of ticks passed has increased
                        results["ups"] = tick_delta / time_delta
                    elif response < 7200: # 7200 ticks which is 120s at 60UPS
                        LOGGER.log(self.info_level, "New map started on {0}".format(self.name))
                    else:
                        LOGGER.log(self.level, "Save rollback detected on {0}".format(self.name))
                else:
                    self.persistent["ups"] = [response_time, response]
        self.results = results


class FactorioStats(BaseStat):
    """Factorio metrics: playercount and UPS logged for each server provided"""
    name = "Factorio"
    def __init__(self):
        self.out_data = {"measurement": "factorio"}
        self.connections = [
            RCONConnection("my_server", "127.0.0.1", 12345, "my_password"),
            RCONConnection("server_2", "127.0.0.1", 12346, "my_password", level=logging.DEBUG)
        ]
        self.queries = dict(player_count="/p o c", tick="/silent-command rcon.print(game.tick)")

    async def async_init(self):
        """Connects to all the RCON servers at once"""
        async with trio.open_nursery() as nursery:
            for connection in self.connections:
                nursery.start_soon(functools.partial(connection.connect, initial=True))

    async def get_stats(self):
        """Fetches the point stats and pushes to out_data"""
        self.out_data = []
        async with trio.open_nursery() as nursery:
            for server in self.connections:
                nursery.start_soon(server.fetch_queries, self.queries)
        for server in self.connections:
            if server.results:
                self.out_data.append(dict(
                    measurement="factorio", tags=dict(server=server.name), **server.results
                ))


LOGGER = logging.getLogger("factorio_metrics")

ACTIVATED_METRICS = [FactorioStats]
