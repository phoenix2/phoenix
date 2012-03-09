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

import time

from twisted.internet import reactor

def norpc(func):
    """This is a quick decorator to mark a function as FORBIDDEN to the RPC
    server. It's intended for useful plugin functions that could pose security
    risks if inadvertently exposed over the network.
    """

    func.rpc_forbidden = True
    return func

class PluginInterface(object):
    """All of the functions that do not start with a _ are acceptable to use in
    third-party plugins.
    """
    @norpc
    def __init__(self, core):
        self.core = core

    def getstatus(self):
        return {'uptime': int(time.time() - self.core.startTime),
                'connection': {'type': self.core.connectionType,
                               'connected': self.core.connected,
                               'url': self.core.connectionURL},
                'results': {'accepted': self.core.logger.accepted,
                            'rejected': self.core.logger.rejected}}

    def getrawconfig(self):
        return self.core.config.text

    def setrawconfig(self, text):
        text = str(text)
        self.core.config.setraw(text)
        self.core.config.save()
        self.core.configChanged()

    @norpc
    def _checkSection(self, section):
        if ':' in section and section not in self.core.config.listsections():
            # Make sure autoconfiguration is preserved.
            self.core.config.set(section, 'autoconfigure', True)
            self.core.config.set(section, 'start_undetected',
                                 False)

    def getconfig(self, section, var):
        section = str(section)
        var = str(var)
        return self.core.config.get(section, var, str, None)

    def setconfig(self, section, var, value):
        section = str(section)
        var = str(var)
        # value doesn't get converted to str - set does that (unless it's None)
        self._checkSection(section)
        self.core.config.set(section, var, value)
        self.core.config.save()
        self.core.configChanged()

    def redetect(self, terminate=False):
        self.core.redetect(terminate)

    def switchto(self, backend=None):
        if backend is None:
            backend = self.core.config.get('general', 'backend', str)
        else:
            backend = str(backend)
        self.core.switchURL(backend)

    @norpc
    def _getminers(self):
        miners = [section for section in self.core.config.listsections()
                  if ':' in section]
        miners.extend([miner for miner in self.core.kernels
                       if miner is not None and miner not in miners])
        return miners

    def listdevices(self):
        devices = []
        for miner in self._getminers():
            device = {'id': miner}

            config = self.core.getKernelConfig(miner)

            if self.core.kernels.get(miner) is not None:
                kernel = self.core.kernels[miner]
                interface = self.core.interfaces[kernel]

                device['status'] = 'running'
                device['name'] = interface.getName()
                device['rate'] = interface.getRate()
                device['config'] = config
                device['meta'] = interface.meta
                device['uptime'] = int(time.time() - interface.started)
                device['results'] = interface.results
                device['accepted'] = interface.accepted
                device['rejected'] = interface.rejected
            else:
                disabled = self.core.config.get(miner, 'disabled', bool, False)

                device['status'] = ('disabled' if disabled else 'suspended')
                device['name'] = config.get('name', miner)
                device['rate'] = 0
                device['config'] = config
                for key, value in self.core.config.getsection(miner).items():
                    device['config'][key.lower()] = value
                device['meta'] = {}
                device['uptime'] = 0
                device['results'] = 0
                device['accepted'] = 0
                device['rejected'] = 0

            devices.append(device)

        return devices

    def getlogs(self, skip, limit=0):
        skip = int(skip)
        limit = int(limit)

        total = len(self.core.logger.rpcLogs) + self.core.logger.rpcIndex
        if skip < 0:
            skip %= total

        buf = [{'id': None, 'timestamp': None, 'msg': None, 'type': 'purged',
                'details': {}}] * (self.core.logger.rpcIndex - skip)
        skip = max(0, skip - self.core.logger.rpcIndex)

        if limit == 0:
            limit = None

        return (buf + [log.formatRPC(self.core.logger) for log in
                       self.core.logger.rpcLogs[skip:]])[:limit]

    @norpc
    def _manage(self, minerID, action):
        # Just a quick helper function to be used for the next 4...
        if minerID is not None:
            minerID = str(minerID)

        saveConfig = False
        managed = False
        for miner in self._getminers():
            running = self.core.kernels.get(miner) is not None
            disabled = self.core.config.get(miner, 'disabled', bool, False)
            if minerID is None or miner == minerID.lower():
                if action == 'suspend':
                    if running:
                        self.core.stopKernel(miner)
                        managed = True
                elif action == 'restart':
                    if running:
                        self.core.stopKernel(miner)
                        self.core.startKernel(miner)
                        managed = True
                elif action == 'disable':
                    if running:
                        self.core.stopKernel(miner)
                    if not disabled:
                        self._checkSection(miner)
                        self.core.config.set(miner, 'disabled', True)
                        saveConfig = True
                        managed = True
                elif action == 'start':
                    if disabled:
                        continue # Can't use start(null) for disabled.
                    if self.core.startKernel(miner):
                        managed = True

        if saveConfig:
            self.core.config.save()
        return managed

    def restart(self, minerID=None):
        return self._manage(minerID, 'restart')

    def suspend(self, minerID=None):
        return self._manage(minerID, 'suspend')

    def disable(self, minerID):
        return self._manage(minerID, 'disable')

    def start(self, minerID=None):
        if minerID is None:
            return self._manage(None, 'start')
        else:
            self.core.config.set(minerID, 'disabled', None)
            self.core.config.save()
            return self.core.startKernel(minerID) is not None

    def shutdown(self):
        reactor.callLater(0.01, reactor.stop)
