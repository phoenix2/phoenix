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

from twisted.internet import reactor, defer
from twisted.internet.protocol import ReconnectingClientFactory
from twisted.protocols.basic import LineReceiver

from ClientBase import *

class MMPProtocolBase(LineReceiver):
    delimiter = '\r\n'
    commands = {} # To be overridden by superclasses...

    def lineReceived(self, line):
        # The protocol uses IRC-style argument passing. i.e. space-separated
        # arguments, with the final one optionally beginning with ':' (in which
        # case, the final argument is the only one that may contain spaces).
        halves = line.split(' :', 1)
        args = halves[0].split(' ') # The space-separated part.
        if len(halves) == 2:
            args.append(halves[1]) # The final argument; could contain spaces.

        cmd = args[0]
        args = args[1:]

        self.handleCommand(cmd, args)

    def handleCommand(self, cmd, args):
        """Handle a parsed command.

        This function takes care of converting arguments to their appropriate
        types and then calls the function handler. If a command is unknown,
        it is dispatched to illegalCommand.
        """
        function = getattr(self, 'cmd_' + cmd, None)

        if function is None or cmd not in self.commands:
            return

        types = self.commands[cmd]

        if len(types) != len(args):
            converted = False
        else:
            converted = True # Unless the below loop has a conversion problem.
            for i,t in enumerate(types):
                try:
                    args[i] = t(args[i])
                except (ValueError, TypeError):
                    converted = False
                    break

        if converted:
            function(*args)
        else:
            self.illegalCommand(cmd)

    def illegalCommand(self, cmd):
        pass # To be overridden by superclasses...

class MMPClientProtocol(MMPProtocolBase, ClientBase):
    """The actual connection to an MMP server. Probably not a good idea to use
    this directly, use MMPClient instead.
    """

    # A suitable default, but the server really should set this itself.
    target = ('\xff'*28) + ('\x00'*4)
    time = 0

    metaSent = False

    commands = {
        'MSG':      (str,),
        'TARGET':   (str,),
        'WORK':     (str, int),
        'BLOCK':    (int,),
        'ACCEPTED': (str,),
        'REJECTED': (str,),
        'TIME':     (int,),
    }

    def connectionMade(self):
        self.factory.connection = self
        self.runCallback('connect')
        self.sendLine('LOGIN %s :%s' % (self.factory.username,
                                        self.factory.password))
        # Got meta?
        for var,value in self.factory.meta.items():
            self.sendMeta(var, value)
        self.metaSent = True

    def connectionLost(self, reason):
        self.runCallback('disconnect')
        self.factory.connection = None
        self.factory._purgeDeferreds()

    def sendMeta(self, var, value):
        # Don't include ':' when sending a meta int, as per the protocol spec.
        colon = '' if isinstance(value, int) else ':'
        self.sendLine('META %s %s%s' % (var, colon, value))

    def cmd_MSG(self, message):
        self.runCallback('msg', message)

    def cmd_TARGET(self, target):
        try:
            t = target.decode('hex')
        except (ValueError, TypeError):
            return
        if len(t) == 32:
            self.target = t

    def cmd_TIME(self, time):
        self.time = time

    def cmd_WORK(self, work, mask):
        try:
            data = work.decode('hex')
        except (ValueError, TypeError):
            return
        if len(data) != 80:
            return
        aw = AssignedWork()
        aw.data = data
        aw.mask = mask
        aw.target = self.target
        aw.setMaxTimeIncrement(self.time)
        aw.identifier = data[4:36]
        self.runCallback('work', aw)
        # Since the server is giving work, we know it has accepted our
        # login details, so we can reset the factory's reconnect delay.
        self.factory.resetDelay()

    def cmd_BLOCK(self, block):
        self.runCallback('block', block)

    def cmd_ACCEPTED(self, data):
        self.factory._resultReturned(data, True)
    def cmd_REJECTED(self, data):
        self.factory._resultReturned(data, False)

class MMPClient(ReconnectingClientFactory, ClientBase):
    """This class implements an outbound connection to an MMP server.

    It's a factory so that it can automatically reconnect when the connection
    is lost.
    """

    protocol = MMPClientProtocol
    maxDelay = 60
    initialDelay = 0.2

    username = None
    password = None
    meta = {'version': 'MMPClient v1.0 by CFSworks'}

    deferreds = {}
    connection = None

    def __init__(self, handler, host, port, username, password):
        self.handler = handler
        self.host = host
        self.port = port
        self.username = username
        self.password = password

    def buildProtocol(self, addr):
        p = self.protocol()
        p.factory = self
        p.handler = self.handler
        return p

    def clientConnectionFailed(self, connector, reason):
        self.runCallback('failure')

        return ReconnectingClientFactory.clientConnectionFailed(
            self, connector, reason)

    def connect(self):
        """Tells the MMPClient to connect if it hasn't already."""

        reactor.connectTCP(self.host, self.port, self)

    def disconnect(self):
        """Tells the MMPClient to disconnect or stop connecting.
        The MMPClient shouldn't be used again.
        """

        self._deactivateCallbacks()

        if self.connection is not None:
            self.connection.transport.loseConnection()

        self.stopTrying()

    def requestWork(self):
        """If connected, ask the server for more work. The request is not sent
        if the client isn't connected, since the server will provide work upon
        next login anyway.
        """
        if self.connection is not None:
            self.connection.sendLine('MORE')

    def setMeta(self, var, value):
        """Set a metavariable, which gets sent to the server on-connect (or
        immediately, if already connected.)
        """
        self.meta[var] = value
        if self.connection and self.connection.metaSent:
            self.connection.sendMeta(var, value)

    def setVersion(self, shortname, longname=None, version=None, author=None):
        """Tells the protocol the application's version."""

        vstr = longname if longname is not None else shortname

        if version is not None:
            if not version.startswith('v') and not version.startswith('r'):
                version = 'v' + version
            vstr += ' ' + version

        if author is not None:
            vstr += ' by ' + author

        self.setMeta('version', vstr)

    def sendResult(self, result):
        """Submit a work result to the server. Returns a deferred which
        provides a True/False depending on whether or not the server
        accepetd the work.
        """
        if self.connection is None:
            return defer.succeed(False)

        d = defer.Deferred()

        if result in self.deferreds:
            self.deferreds[result].chainDeferred(d)
        else:
            self.deferreds[result] = d

        self.connection.sendLine('RESULT ' + result.encode('hex'))
        return d

    def _purgeDeferreds(self):
        for d in self.deferreds.values():
            d.callback(False)
        self.deferreds = {}

    def _resultReturned(self, data, accepted):
        try:
            data = data.decode('hex')
        except (TypeError, ValueError):
            return

        if data in self.deferreds:
            self.deferreds[data].callback(accepted)
            del self.deferreds[data]