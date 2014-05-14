#!/usr/bin/python
# -*- coding: utf-8 -*-

# Hive Appier Framework
# Copyright (C) 2008-2014 Hive Solutions Lda.
#
# This file is part of Hive Appier Framework.
#
# Hive Appier Framework is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Hive Appier Framework is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Hive Appier Framework. If not, see <http://www.gnu.org/licenses/>.

__author__ = "João Magalhães joamag@hive.pt>"
""" The author(s) of the module """

__version__ = "1.0.0"
""" The version of the module """

__revision__ = "$LastChangedRevision$"
""" The revision number of the module """

__date__ = "$LastChangedDate$"
""" The last change date of the module """

__copyright__ = "Copyright (c) 2008-2014 Hive Solutions Lda."
""" The copyright for the module """

__license__ = "GNU General Public License (GPL), Version 3"
""" The license for the module """

import time
import logging
import threading
import traceback

LOOP_TIMEOUT = 60.0
""" The time value to be used to sleep the main sequence
loop between ticks, this value should not be too small
to spend many resources or to high to create a long set
of time between external interactions """

class Scheduler(threading.Thread):
    """
    Scheduler class that handles all the async tasks
    relates with the house keeping of the appier
    infra-structure. The architecture of the logic
    for the class should be modular in the sense that
    new task may be added to it through a queue system.
    """

    def __init__(self, owner, timeout = LOOP_TIMEOUT, daemon = True):
        threading.Thread.__init__(self)
        self.owner = owner
        self.timeout = timeout
        self.daemon = daemon

    def run(self):
        self.running  = True
        self.load()
        while self.running:
            try:
                self.tick()
            except BaseException as exception:
                self.logger.critical("Unhandled scheduler exception raised")
                self.logger.error(exception)
                lines = traceback.format_exc().splitlines()
                for line in lines: self.logger.warning(line)
            time.sleep(self.timeout)

    def stop(self):
        self.running = False

    def tick(self):
        pass

    @property
    def logger(self):
        if self.owner: return self.owner.logger
        else: return logging.getLogger()