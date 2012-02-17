# THIS IS AN EXAMPLE KERNEL FOR PHOENIX 2.
# THOUGH IT IS FULLY FUNCTIONAL, IT IS QUITE SLOW. IT IS INTENDED FOR
# DEMONSTRATION PURPOSES ONLY.

# Additionally, this doesn't demonstrate QueueReader, which is the preferred
# way of making kernels that dispatch a separate thread to handle NonceRanges
# (as this one does)

import time
from twisted.internet import reactor, defer

# You should really look at the functions defined here. They are useful.
from phoenix2.core.KernelInterface import KernelOption

# This class needs to be defined in all Phoenix 2 kernels. The required
# functions are __init__, start, and stop. Everything else is optional.
class PhoenixKernel(object):
    # Here you can define options that the kernel will accept from the
    # configuration file.
    GREETING = KernelOption('greeting', str, default='',
                            help='Defines what debug message should be '
                                 'printed at kernel start-up time.')

    def __init__(self, interface):
        self.interface = interface
        self._stop = False

        # Here we can ask the interface for the Phoenix 2 device ID, which is a
        # lower-case (it is lower-cased by Phoenix even if caps are used in the
        # configuration file) string identifying a specific device on the
        # system.
        # We don't need this for much, given that this is a CPU miner, but
        # let's just show how to access it.
        if not self.interface.getDeviceID().startswith('cpu:'):
            self.interface.fatal('This kernel is only for CPUs!')
            return

        # Let's also print that configuration message that we set up before...
        self.interface.debug('Greeting: ' + self.GREETING)

        # We can also provide metadata on what the kernel is doing.
        self.interface.setMeta('cores', '1')

    @classmethod
    def autodetect(cls, callback):
        # This class method is used when Phoenix loads the kernel to autodetect
        # the devices that it supports. When this function runs, the kernel is
        # to look for all supported devices present on the system, and call
        # callback(devid) for each one.
        # It is also legal to store the callback and trigger the callback in
        # the event of hotplug. If this is the case, the kernel must also
        # define a class method called stopAutodetect() that disables hotplug
        # detection.
        # Also note that it is legal to call this function multiple times
        # without calling stopAutodetect in between. If this function is called
        # again, the kernel must redetect all devices present and send them all
        # through the callback again, even the ones it has already detected.

        # In this case, there is only one device this kernel supports: the CPU
        # (which we know is present) - the CPU is identified by devid cpu:0 by
        # default. The user can use cpu:1, etc, if he wishes to run several CPU
        # kernels in tandem (for some reason), but the canonical ID for
        # "the CPU" is always cpu:0.
        callback('cpu:0')

    @classmethod
    def analyzeDevice(cls, devid):
        # This class method is for analyzing how well a kernel will support a
        # specific device to help Phoenix automatically choose kernels.
        # It is to return a tuple: (suitability, config, ids)
        # Where 'suitability' is a number in the following table:
        # 0 - DO NOT USE THIS KERNEL
        # 1 - WILL WORK AS A FALLBACK
        # 2 - INTENDED USE FOR THIS CLASS OF HARDWARE
        # 3 - OPTIMIZED FOR THIS BRAND OF HARDWARE
        # 4 - OPTIMIZED FOR THIS SPECIFIC MODEL OF HARDWARE
        # 5 - OPTIMIZED FOR THIS HARDWARE'S CURRENT CONFIGURATION
        #     (e.g. kernels that work well when clocks are low, etc)
        # And config is a dictionary of recommended configuration values, which
        # will get used unless the user explicitly disables autoconfiguration.
        # Finally, ids is the list of IDs that the device is known by, with the
        # "preferred" ID being the first one.

        if devid.startswith('cpu:'):
            return (1, {}, [devid])
        else:
            return (0, {}, [devid])

    def start(self):
        self._stop = False
        reactor.callInThread(self.mine)

    def stop(self):
        self._stop = True

    def _fetchRangeHelper(self, d):
        # This function is a workaround for Twisted's threading model. The
        # callFromThread function, which is necessary to invoke a function in
        # the main thread, does not come back with return values. So, this
        # function accepts a deferred, fetches some work, and fires the work
        # through the deferred. QueueReader deals with all of this internally.
        self.interface.fetchRange().chainDeferred(d)

    # inlineCallbacks is a Twisted thing, it means you can do "x = yield y"
    # where y is a Deferred, and it will pause your function until the Deferred
    # fires back with a value
    @defer.inlineCallbacks
    def mine(self):
        # This is rate-counting logic...
        nonceCounter = 0
        nonceTime = time.time()

        while True:
            d = defer.Deferred()
            reactor.callFromThread(self._fetchRangeHelper, d)
            nr = yield d
            # Now we work on nr...
            # This is defined in WorkQueue.py
            for nonce in xrange(nr.base, nr.base+nr.size):
                # Here we simply have to test nonce. We can do this ourselves,
                # but the interface has a convenience function to do this for
                # us. (It doesn't communicate elsewhere with Phoenix and is
                # therefore safe to use without reactor.callFromThread)
                hash = self.interface.calculateHash(nr.unit, nonce)

                # There's also a convenience function for comparing the hash
                # against the target.
                if self.interface.checkTarget(hash, nr.unit.target):
                    # It's good! Let's send it in...
                    reactor.callFromThread(self.interface.foundNonce, nr.unit,
                                           nonce)

                # Count the nonce we just did, and report the rate, in
                # kilohashes, to the interface.
                nonceCounter += 1
                if nonceCounter >= 0x100:
                    now = time.time()
                    dt = now - nonceTime
                    reactor.callFromThread(self.interface.updateRate,
                                           int(nonceCounter/dt/1000))
                    nonceCounter = 0
                    nonceTime = now

                # Finally, this thread needs to die if the kernel has been
                # asked to stop...
                if self._stop:
                    return
