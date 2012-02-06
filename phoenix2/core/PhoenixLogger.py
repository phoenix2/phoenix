# Copyright (C) 2012 by jedi95 <jedi95@gmail.com> and
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

import sys
import time
import atexit

from twisted.internet import reactor

class PhoenixLog(object):
    """Base class for all manner of logs that may occur."""

    TYPE = 'general'

    def __init__(self, msg, kernelif=None):
        self._setup(kernelif)
        self.msg = msg

    def _setup(self, kernelif=None):
        self.time = time.time()
        self.kernelif = kernelif
        self.msg = ''

    # --- INTENDED TO BE OVERRIDDEN BY SUBCLASSES ---
    def toConsole(self, logger): return True
    def toFile(self, logger): return True
    def toRPC(self, logger): return True
    def getMsg(self, verbose): return self.msg
    def getType(self): return self.TYPE
    def getDetails(self): return {}
    # -----------------------------------------------

    def formatRPC(self, logger):
        if self.kernelif:
            devid = self.kernelif.getDeviceID()
        else:
            devid = None

        return {'id': devid, 'timestamp': int(self.time),
                'msg': self.getMsg(logger.isVerbose()),
                'type': self.getType(), 'details': self.getDetails()}

    def formatConsole(self, logger, fullDate=False):
        """Format this log for appearance in the console."""

        timeformat = '%m/%d/%Y %H:%M:%S' if fullDate else '%H:%M:%S'

        output = '[%s] ' % time.strftime(timeformat, time.localtime(self.time))
        if self.kernelif:
            output += '[%s] ' % self.kernelif.getName()

        output += self.getMsg(logger.isVerbose())

        return output

    def formatFile(self, logger):
        return self.formatConsole(logger, True)

# --- VARIOUS LOGS ---

class DebugLog(PhoenixLog):
    TYPE = 'debug'
    def toConsole(self, logger): return logger.isVerbose()

class RateUpdateLog(PhoenixLog):
    TYPE = 'rate'

    def __init__(self, rate):
        self._setup()
        self.rate = rate

    def toConsole(self, logger): return False
    def toFile(self, logger): return False
    def toRPC(self, logger): return False

class LongPollPushLog(PhoenixLog):
    TYPE = 'lppush'

    def __init__(self):
        self._setup()
        self.msg = 'LP: New work pushed'

class BlockChangeLog(PhoenixLog):
    TYPE = 'block'

    def __init__(self, block):
        self._setup()
        self.block = block

    def getMsg(self, verbose):
        return 'Currently on block: %s' % self.block

    def getDetails(self): return {'block': self.block}

class ResultLog(PhoenixLog):
    TYPE = 'result'

    def __init__(self, kernelif, hash, accepted):
        self._setup(kernelif)
        self.hash = hash
        self.accepted = accepted

    def getMsg(self, verbose):
        status = ('ACCEPTED' if self.accepted else 'REJECTED')
        if verbose:
            hash = self.hash[:23:-1].encode('hex') + '...'
        else:
            hash = self.hash[27:23:-1].encode('hex')
        return 'Result %s %s' % (hash, status)

    def getDetails(self):
        return {'hash': self.hash[::-1].encode('hex'),
                'accepted': self.accepted}

class ConnectionLog(PhoenixLog):
    TYPE = 'connection'

    def __init__(self, connected, url):
        self._setup()
        self.connected = connected
        self.url = url

    def getMsg(self, verbose):
        if self.connected:
            return 'Connected to server'
        else:
            return 'Disconnected from server'

    def getDetails(self): return {'connected': self.connected, 'url': self.url}

class KernelErrorLog(PhoenixLog):
    TYPE = 'error'

    def __init__(self, kernelif, error):
        self._setup(kernelif)
        self.error = error

    def toConsole(self, logger): return bool(self.error)
    def toFile(self, logger): return bool(self.error)

    def getMsg(self, verbose):
        if self.error:
            return 'Error: ' + self.error

    def getDetails(self): return {'error': self.error}

class KernelFatalLog(KernelErrorLog):
    TYPE = 'fatal'
    def getMsg(self, verbose):
        if self.error:
            return 'Fatal error: ' + self.error

# --------------------

class ConsoleOutput(object):
    def __init__(self):
        self._status = ''
        atexit.register(self._exit)

    def _exit(self):
        self._status += '  ' # In case the shell added a ^C
        self.status('')

    def status(self, status):
        update = '\r'
        update += status
        update += ' ' * (len(self._status) - len(status))
        update += '\b' * (len(self._status) - len(status))
        sys.stderr.write(update)
        self._status = status

    def printline(self, line):
        update = '\r'
        update += line + ' ' * (len(self._status) - len(line)) + '\n'
        update += self._status
        sys.stderr.write(update)

class PhoenixLogger(object):
    def __init__(self, core):
        self.console = ConsoleOutput()
        self.core = core
        self.rateText = '0 Khash/s'

        self.accepted = 0
        self.rejected = 0

        self.consoleDay = None

        self.logfile = None
        self.logfileName = None

        self.rpcLogs = []
        self.rpcIndex = 0

        self.nextRefresh = 0
        self.refreshScheduled = False
        self.refreshStatus()

    def isVerbose(self):
        return self.core.config.get('general', 'verbose', bool, False)

    def log(self, msg):
        self.dispatch(PhoenixLog(msg))

    def debug(self, msg):
        self.dispatch(DebugLog(msg))

    def writeToFile(self, text):
        logfileName = self.core.config.get('general', 'logfile', str, None)
        if logfileName != self.logfileName:
            self.logfileName = logfileName
            if self.logfile:
                self.logfile.close()
                self.logfile = None
            if logfileName:
                self.logfile = open(logfileName, 'a')

        if self.logfile:
            self.logfile.write(text + '\n')
            self.logfile.flush()

    def addToRPC(self, log):
        self.rpcLogs.append(log)
        rpcLimit = self.core.config.get('web', 'logbuffer', int, 1000)
        if len(self.rpcLogs) > rpcLimit:
            prune = len(self.rpcLogs) - rpcLimit
            self.rpcIndex += prune
            self.rpcLogs = self.rpcLogs[prune:]

    def dispatch(self, log):
        if log.toConsole(self):
            day = time.localtime(log.time)[:3]
            self.console.printline(log.formatConsole(self, day !=
                                                     self.consoleDay))
            self.consoleDay = day
        if log.toFile(self):
            self.writeToFile(log.formatFile(self))
        if log.toRPC(self):
            self.addToRPC(log)

        if isinstance(log, ResultLog):
            if log.accepted:
                self.accepted += 1
            else:
                self.rejected += 1
            self.refreshStatus()
        elif isinstance(log, RateUpdateLog):
            self.rateText = self.formatNumber(log.rate) + 'hash/s'
            self.refreshStatus()

    def refreshStatus(self):
        now = time.time()
        if now < self.nextRefresh:
            if not self.refreshScheduled:
                reactor.callLater(self.nextRefresh - now,
                                  self.refreshStatus)
            self.refreshScheduled = True
            return

        self.refreshScheduled = False
        self.nextRefresh = time.time() + self.core.config.get('log', 'refresh',
                                                              float, 1.0)

        if self.core.connected:
            connectionType = {'mmp': 'MMP', 'rpc': 'RPC',
                              'rpclp': 'RPC (+LP)'
                             }.get(self.core.connectionType, 'OTHER')
        else:
            connectionType = 'DISCONNECTED'
        self.console.status('[%s] [%s Accepted] [%s Rejected] [%s]' %
                            (self.rateText, self.accepted,
                             self.rejected, connectionType))

    @classmethod
    def formatNumber(cls, n):
        """Format a positive integer in a more readable fashion."""
        if n < 0:
            raise ValueError('can only format positive integers')
        prefixes = 'KMGTP'
        whole = str(int(n))
        decimal = ''
        i = 0
        while len(whole) > 3:
            if i + 1 < len(prefixes):
                decimal = '.%s' % whole[-3:-1]
                whole = whole[:-3]
                i += 1
            else:
                break
        return '%s%s %s' % (whole, decimal, prefixes[i])
