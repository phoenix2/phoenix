# Copyright (C) 2011-2012 by jedi95 <jedi95@gmail.com> and
#                            CFSworks <CFSworks@gmail.com>
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

import sys
import os
import traceback
import time
from struct import pack, unpack
from hashlib import sha256
from twisted.internet import defer, reactor
from weakref import WeakKeyDictionary

from phoenix2.core.PhoenixLogger import *

# I'm using this as a sentinel value to indicate that an option has no default;
# it must be specified.
REQUIRED = object()

class KernelOption(object):
    """This works like a property, and is used in defining easy option tables
    for kernels.
    """

    def __init__(self, name, type, help=None, default=REQUIRED,
        advanced=False, **kwargs):
        self.localValues = WeakKeyDictionary()
        self.name = name
        self.type = type
        self.help = help
        self.default = default
        self.advanced = advanced

    def __get__(self, instance, owner):
        if instance in self.localValues:
            return self.localValues[instance]
        else:
            return instance.interface._getOption(
                self.name, self.type, self.default)

    def __set__(self, instance, value):
        self.localValues[instance] = value

class KernelInterface(object):
    """This is an object passed to kernels as an API back to the Phoenix
    framework.
    """

    def __init__(self, deviceID, core, options):
        self.deviceID = deviceID
        self.core = core
        self.options = options
        self.meta = {}
        self._fatal = False
        self.rateCounters = {}
        self.results = 0
        self.started = time.time()

    def getDeviceID(self):
        """Kernels should query this first, to get the device identifier."""
        return self.deviceID

    def getName(self):
        """Gets the configured name for this kernel."""
        return self.options.get('name', self.deviceID)

    def _getOption(self, name, optType, default):
        """KernelOption uses this to read the actual value of the option."""
        name = name.lower()
        if not name in self.options:
            if default == REQUIRED:
                self.fatal('Required option %s not provided!' % name)
            else:
                return default

        givenOption = self.options[name]
        if optType == bool:
            if type(givenOption) == bool:
                return givenOption
            # The following are considered true
            return givenOption is None or \
                givenOption.lower() in ('t', 'true', 'on', '1', 'y', 'yes')

        try:
            return optType(givenOption)
        except (TypeError, ValueError):
            self.fatal('Option %s expects a value of type %s!' %
                       (name, type.__name__))

    def getRevision(self):
        """Return the Phoenix core revision, so that kernels can require a
        minimum revision before operating (such as if they rely on a certain
        feature added in a certain revision)
        """

        return self.core.REVISION

    def setMeta(self, var, value):
        """Set metadata for this kernel."""

        self.meta[var] = value
        # TODO: Change this to distinguish between multiple kernels.
        self.core.setMeta(var, value)

    def getRate(self):
        """Get the total rate of this kernel, in khps"""

        total = 0
        for rc in self.rateCounters.values():
            if rc:
                total += sum(rc)/len(rc)
        return total

    def updateRate(self, rate, index=None):
        rc = self.rateCounters.setdefault(index, [])
        rc.append(rate)

        # Now limit to the sliding window:
        samples = self.core.config.get('general', 'ratesamples', int, 10)
        self.rateCounters[index] = rc[-samples:]

        self.core._recalculateTotalRate()

    def fetchRange(self, size=None):
        """Fetch a range from the WorkQueue, optionally specifying a size
        (in nonces) to include in the range.
        """

        if size is None:
            return self.core.queue.fetchRange()
        else:
            return self.core.queue.fetchRange(size)

    def fetchUnit(self):
        """Fetch a raw WorkUnit directly from the WorkQueue."""
        return self.core.queue.fetchUnit()

    def checkTarget(self, hash, target):
        """Utility function that the kernel can use to see if a nonce meets a
        target before sending it back to the core.
        Since the target is checked before submission anyway, this is mostly
        intended to be used in hardware sanity-checks.
        """

        # This for loop compares the bytes of the target and hash in reverse
        # order, because both are 256-bit little endian.
        for t,h in zip(target[::-1], hash[::-1]):
            if ord(t) > ord(h):
                return True
            elif ord(t) < ord(h):
                return False
        return True

    def calculateHash(self, wu, nonce, timestamp = None):
        """Given a NonceRange/WorkUnit and a nonce, calculate the SHA-256
        hash of the solution. The resulting hash is returned as a string, which
        may be compared with the target as a 256-bit little endian unsigned
        integer.
        """

        #If timestamp is not specified then use the one in the WorkUnit
        if timestamp is None:
            timestamp = wu.timestamp

        staticDataUnpacked = list(unpack('>' + 'I'*19, wu.data[:76]))
        staticDataUnpacked[-2] = timestamp
        staticData = pack('<' + 'I'*19, *staticDataUnpacked)
        hashInput = pack('>76sI', staticData, nonce)
        return sha256(sha256(hashInput).digest()).digest()

    def foundNonce(self, wu, nonce, timestamp = None):
        """Called by kernels when they may have found a nonce."""

        self.results += 1

        #If timestamp is not specified then use the one in the WorkUnit
        if timestamp is None:
            timestamp = wu.timestamp

        # Check if the hash meets the full difficulty before sending.
        hash = self.calculateHash(wu, nonce, timestamp)

        # Check if the block has changed while this NonceRange was being
        # processed by the kernel. If so, don't send it to the server.
        if wu.isStale and not getattr(self.core.connection,
                                      'submitold', False):
            return False

        if self.checkTarget(hash, wu.target):
            formattedResult = pack('>68sI4s', wu.data[:68], timestamp,
                                    wu.data[72:76]) + pack('<I', nonce)
            d = self.core.connection.sendResult(formattedResult)
            def callback(accepted):
                self.core.logger.dispatch(ResultLog(self, hash, accepted))
            d.addCallback(callback)
            return True
        else:
            self.core.logger.debug("Result didn't meet full "
                                   "difficulty, not sending")
            return False

    def debugException(self):
        """Call this from an except: block to drop the exception out to the
        logger as verbose messages.
        """

        exc = sys.exc_info()[1]

        msg = 'Exception: '
        for filename, ln, func, txt in traceback.extract_tb(sys.exc_info()[2]):
            filename = os.path.split(filename)[1]
            msg += '%s:%d, ' % (filename, ln)
        msg += '%s: %s' % (exc.__class__.__name__, exc)
        self.debug(msg)

    def debug(self, msg):
        """Log information as debug so that it can be viewed only when -v is
        enabled.
        """
        self.core.logger.dispatch(DebugLog(msg, self))

    def log(self, msg):
        """Log some general kernel information to the console."""
        self.core.logger.dispatch(PhoenixLog(msg, self))

    def error(self, msg=None):
        """The kernel has an issue that requires user attention."""
        self.core.logger.dispatch(KernelErrorLog(self, msg))

    def fatal(self, msg=None):
        """The kernel has an issue that is preventing it from continuing to
        operate.
        """
        self.core.logger.dispatch(KernelFatalLog(self, msg))
        self._fatal = True

        self.core.stopKernel(self.deviceID)
