# Copyright (C) 2011 by jedi95 <jedi95@gmail.com> and
#                       CFSworks <CFSworks@gmail.com>
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

import struct

from collections import deque
from time import time
from twisted.internet import defer
from twisted.internet import reactor
from twisted.internet.defer import DeferredLock
from ..util.Midstate import calculateMidstate

"""A WorkUnit is a single unit containing up to 2^32 nonces. A single getWork
request returns a WorkUnit.
"""
class WorkUnit(object):

    def __init__(self, aw):
        self.data = aw.data
        self.target = aw.target
        self.identifier = aw.identifier
        self.maxtime = aw.maxtime
        try:
            self.nonces = 2 ** aw.mask
        except AttributeError:
            self.nonces = aw.nonces
        self.base = 0
        self.midstate = calculateMidstate(self.data[:64])
        self.isStale = False
        self.time = aw.time
        self.downloaded = time()
        self.callbacks = set()

    def set_timestamp(self, timestamp):
        self.data = (self.data[:68] + struct.pack('>I', timestamp) +
                     self.data[72:])
    def get_timestamp(self):
        return struct.unpack('>I', self.data[68:72])[0]
    timestamp = property(get_timestamp, set_timestamp)

    def addStaleCallback(self, callback):
        if self.isStale:
            callback(self)
        else:
            self.callbacks.add(callback)

    def removeStaleCallback(self, callback):
        self.callbacks.remove(callback)

    def stale(self):
        if self.isStale:
            return
        self.isStale = True
        for cb in list(self.callbacks):
            cb(self)

"""A NonceRange is a range of nonces from a WorkUnit, to be dispatched in a
single execution of a mining kernel. The size of the NonceRange can be
adjusted to tune the performance of the kernel.

This class doesn't actually do anything, it's just a well-defined container
that kernels can pull information out of.
"""
class NonceRange(object):

    def __init__(self, unit, base, size):
        self.unit = unit # The WorkUnit this NonceRange comes from.
        self.base = base # The base nonce.
        self.size = size # How many nonces this NonceRange says to test.

class WorkQueue(object):
    """A WorkQueue contains WorkUnits and dispatches NonceRanges when requested
    by the miner. WorkQueues dispatch deffereds when they runs out of nonces.
    """

    def __init__(self, core):

        self.core = core
        self.logger = core.logger
        self.queueSize = core.config.get('general', 'queuesize', int, 1)
        self.queueDelay = core.config.get('general', 'queuedelay', int, 5)

        self.lock = DeferredLock()
        self.queue = deque('', self.queueSize)
        self.deferredQueue = deque()
        self.currentUnit = None
        self.lastBlock = None
        self.block = ''

        self.staleCallbacks = []

    def storeWork(self, aw):

        #check if this work matches the previous block
        if (self.lastBlock is not None) and (aw.identifier == self.lastBlock):
            self.logger.debug('Server gave work from the previous '
                              'block, ignoring.')
            #if the queue is too short request more work
            if self.checkQueue():
                if self.core.connection:
                    self.core.connection.requestWork()
            return

        #create a WorkUnit
        work = WorkUnit(aw)
        reactor.callLater(max(60, aw.time - 1) - self.queueDelay,
                            self.checkWork)
        reactor.callLater(max(60, aw.time - 1), self.workExpire, work)

        #check if there is a new block, if so reset queue
        newBlock = (aw.identifier != self.block)
        if newBlock:
            self.queue.clear()
            self.currentUnit = None
            self.lastBlock = self.block
            self.block = aw.identifier
            self.logger.debug("New block (WorkQueue)")

        #add new WorkUnit to queue
        if work.data and work.target and work.midstate and work.nonces:
            self.queue.append(work)

        #if the queue is too short request more work
        workRequested = False
        if self.checkQueue():
            if self.core.connection:
                self.core.connection.requestWork()
                workRequested = True

        #if there is a new block notify kernels that their work is now stale
        if newBlock:
            for callback in self.staleCallbacks:
                callback()
            self.staleCallbacks = []
        self.staleCallbacks.append(work.stale)

        #check if there are deferred WorkUnit requests pending
        #since requests to fetch a WorkUnit can add additional deferreds to
        #the queue, cache the size beforehand to avoid infinite loops.
        for i in range(len(self.deferredQueue)):
            df = self.deferredQueue.popleft()
            d = self.fetchUnit(workRequested)
            d.chainDeferred(df)

        #clear the idle flag since we just added work to queue
        self.core.reportIdle(False)

    def checkWork(self):
        # Called 5 seconds before any work expires in order to fetch more
        if self.checkQueue():
            if self.core.connection:
                self.core.requestWork()

    def checkQueue(self, added = False):

        # This function checks the queue length including the current unit
        size = 1

        # Check if the current unit will last long enough
        if self.currentUnit is None:
            if len(self.queue) == 0:
                return True
            else:
                size = 0
                if added:
                    rolls = self.queue[0].maxtime - self.queue[0].timestamp
                    # If new work can't be rolled, and queue would be too small
                    if rolls == 0 and (len(self.queue) - 1) < self.queueSize:
                        return True

        else:
            remaining = self.currentUnit.maxtime - self.currentUnit.timestamp
            # Check if we are about to run out of rolltime on current unit
            if remaining < (self.queueDelay):
                size = 0

            # Check if the current unit is about to expire
            age = self.currentUnit.downloaded + self.currentUnit.time
            lifetime = age - time()
            if lifetime < (2 * self.queueDelay):
                size = 0

        # Check if the queue will last long enough
        queueLength = 0
        for i in range(len(self.queue)):
            age = self.queue[0].downloaded + max(60, self.queue[0].time - 1)
            lifetime = age - time()
            if lifetime > (2 * self.queueDelay):
                queueLength += 1

        # Return True/False indicating if more work should be fetched
        return size + queueLength < self.queueSize

    def workExpire(self, wu):
        # Don't expire WorkUnits if idle and queue empty
        if (self.core.idle) and (len(self.queue) <= 1):
            return

        # Remove the WorkUnit from queue
        if len(self.queue) > 0:
            iSize = len(self.queue)
            if not (len(self.queue) == 1 and (self.currentUnit is None)):
                try:
                    self.queue.remove(wu)
                except ValueError: pass
            if self.currentUnit == wu:
                self.currentUnit = None

            # Check queue size
            if self.checkQueue() and (iSize != len(self.queue)):
                if self.core.connection:
                    self.core.connection.requestWork()

            # Flag the WorkUnit as stale
            wu.stale()
        else:
            # Check back again later if we didn't expire the work
            reactor.callLater(5, self.workExpire, wu)

    def getRangeFromUnit(self, size):

        #get remaining nonces
        noncesLeft = self.currentUnit.nonces - self.currentUnit.base

        # Flag indicating if the WorkUnit was depeleted by this request
        depleted = False

        #if there are enough nonces to fill the full reqest
        if noncesLeft >= size:
            nr = NonceRange(self.currentUnit, self.currentUnit.base, size)

            #check if this uses up the rest of the WorkUnit
            if size >= noncesLeft:
                depleted = True
            else:
                self.currentUnit.base += size

        #otherwise send whatever is left
        else:
            nr = NonceRange(
                self.currentUnit, self.currentUnit.base, noncesLeft)
            depleted = True

        #return the range
        return nr, depleted

    def checkRollTime(self, wu):
    # This function checks if a WorkUnit could be time rolled
        if wu.maxtime > wu.timestamp and not wu.isStale:
            remaining = (wu.downloaded + wu.time) - time()
            if remaining > (self.queueDelay) or len(self.queue) < 1:
                # If it has been more than 5 minutes probably better to idle
                if time() - wu.downloaded < 300:
                    return True

        return False

    def rollTime(self, wu):

        # Check if this WorkUnit supports rolling time, return None if not
        if not self.checkRollTime(wu):
            return None

        # Create the new WU
        newWU = WorkUnit(wu)

        # Increment the timestamp
        newWU.timestamp += 1

        # Reset the download time to the original WU's
        newWU.downloaded = wu.downloaded

        # Set a stale callback for this WU
        self.staleCallbacks.append(newWU.stale)

        # Setup a workExpire callback
        remaining = max(self.queueDelay, (wu.downloaded + wu.time) - time())
        reactor.callLater(remaining - 1, self.workExpire, newWU)

        # Return the new WU
        return newWU

    def fetchUnit(self, delayed = False):
        #if there is a unit in queue
        if len(self.queue) >= 1:

            #check if the queue has fallen below the desired size
            if self.checkQueue(True) and (not delayed):
                #Request more work to maintain minimum queue size
                if self.core.connection:
                    self.core.connection.requestWork()

            #get the next unit from queue
            wu = self.queue.popleft()

            #return the unit
            return defer.succeed(wu)

        #if the queue is empty
        else:

            #request more work
            if self.core.connection:
                self.core.connection.requestWork()

            #report that the miner is idle
            self.core.reportIdle(True)

            #set up and return deferred
            df = defer.Deferred()
            self.deferredQueue.append(df)
            return df

    #make sure that only one fetchRange request runs at a time
    def fetchRange(self, size=0x10000):
        return self.lock.run(self._fetchRange, size)

    def _fetchRange(self, size):

        #make sure size is not too large
        size = min(size, 0x100000000)

        #check if the current unit exists
        if self.currentUnit is not None:

            # Get a nonce range
            nr, depleated = self.getRangeFromUnit(size)

            # If we depleted the Workunit then try to roll time
            if depleated:
                self.currentUnit = self.rollTime(self.currentUnit)

            # Return the range
            return defer.succeed(nr)

        #if there is no current unit
        else:

            # Check if we can get a new unit with rolltime
            def callback(wu):
                #get a new current unit
                self.currentUnit = wu

                #get a nonce range
                nr, depleated = self.getRangeFromUnit(size)

                # If we depleted the Workunit then try to roll time
                if depleated:
                    self.currentUnit = self.rollTime(self.currentUnit)

                #return the range
                return nr

            d = self.fetchUnit()
            d.addCallback(callback)
            return d
