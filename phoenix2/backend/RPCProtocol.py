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

import urlparse
import json
import sys
import httplib
import socket
from twisted.internet import defer, reactor, error, threads
from twisted.python import failure

from ClientBase import ClientBase, AssignedWork

class ServerMessage(Exception): pass

class HTTPBase(object):
    connection = None
    timeout = None
    __lock = None
    __response = None

    def __makeResponse(self, *args, **kwargs):
        # This function exists as a workaround: If the connection is closed,
        # we also want to kill the response to allow the socket to die, but
        # httplib doesn't keep the response hanging around at all, so we need
        # to intercept its creation (hence this function) and store it.
        self.__response = httplib.HTTPResponse(*args, **kwargs)
        return self.__response

    def doRequest(self, *args):
        if self.__lock is None:
            self.__lock = defer.DeferredLock()
        return self.__lock.run(threads.deferToThread, self._doRequest, *args)

    def closeConnection(self):
        if self.connection is not None:
            if self.connection.sock is not None:
                self.connection.sock._sock.close()
            try:
                self.connection.close()
            except (AttributeError):
                #This is to fix "'NoneType' object has no attribute 'close'"
                #Theoretically this shouldn't be possible as we specifically
                #verify that self.connection isn't NoneType before trying to
                #call close(). I would add a debug message here, but HTTPBase
                #isn't passed a reference to the miner. The stack trace causing
                #this problem originates from the errback on line 138 (ask())
                #Most likely some sort of threading problem (race condition)
                pass

        if self.__response is not None:
            try:
                self.__response.close()
            except (AttributeError):
                #This was added for the same reason as the above
                pass
        self.connection = None
        self.__response = None

    def _doRequest(self, url, *args):
        if self.connection is None:
            connectionClass = (httplib.HTTPSConnection
                               if url.scheme.lower() == 'https' else
                               httplib.HTTPConnection)
            self.connection = connectionClass(url.hostname,
                                              url.port,
                                              timeout=self.timeout)
            # Intercept the creation of the response class (see above)
            self.connection.response_class = self.__makeResponse
            self.connection.connect()
            self.connection.sock.setsockopt(socket.SOL_TCP,
                                            socket.TCP_NODELAY, 1)
            self.connection.sock.setsockopt(socket.SOL_SOCKET,
                                            socket.SO_KEEPALIVE, 1)
        try:
            self.connection.request(*args)
            response = self.connection.getresponse()
            headers = response.getheaders()
            data = response.read()
            return (headers, data)
        except (httplib.HTTPException, socket.error):
            self.closeConnection()
            raise

class RPCPoller(HTTPBase):
    """Polls the root's chosen bitcoind or pool RPC server for work."""

    timeout = 5

    def __init__(self, root):
        self.root = root
        self.askInterval = None
        self.askCall = None
        self.currentAsk = None

    def setInterval(self, interval):
        """Change the interval at which to poll the getwork() function."""
        self.askInterval = interval
        self._startCall()

    def _startCall(self):
        self._stopCall()
        if self.root.disconnected:
            return
        if self.askInterval:
            self.askCall = reactor.callLater(self.askInterval, self.ask)
        else:
            self.askCall = None

    def _stopCall(self):
        if self.askCall:
            try:
                self.askCall.cancel()
            except (error.AlreadyCancelled, error.AlreadyCalled):
                pass
            self.askCall = None

    def ask(self):
        """Run a getwork request immediately."""

        if self.currentAsk and not self.currentAsk.called:
             return
        self._stopCall()

        self.currentAsk = self.call('getwork')

        def errback(failure):
            try:
                if failure.check(ServerMessage):
                    self.root.runCallback('msg', failure.getErrorMessage())
                self.root._failure()
            finally:
                self._startCall()

        self.currentAsk.addErrback(errback)

        def callback(x):
            try:
                try:
                    (headers, result) = x
                except TypeError:
                    return
                self.root.handleWork(result, headers)
                self.root.handleHeaders(headers)
            finally:
                self._startCall()
        self.currentAsk.addCallback(callback)

    @defer.inlineCallbacks
    def call(self, method, params=[]):
        """Call the specified remote function."""

        body = json.dumps({'method': method, 'params': params, 'id': 1})
        path = self.root.url.path or '/'
        if self.root.url.query:
            path += '?' + self.root.url.query
        response = yield self.doRequest(
            self.root.url,
            'POST',
            path,
            body,
            {
                'Authorization': self.root.auth,
                'User-Agent': self.root.version,
                'Content-Type': 'application/json',
                'X-Work-Identifier': '1',
                'X-Mining-Extensions': self.root.EXTENSIONS
            })

        (headers, data) = response
        result = self.parse(data)
        defer.returnValue((dict(headers), result))

    @classmethod
    def parse(cls, data):
        """Attempt to load JSON-RPC data."""

        response = json.loads(data)
        try:
            message = response['error']['message']
        except (KeyError, TypeError):
            pass
        else:
            raise ServerMessage(message)

        return response.get('result')

class LongPoller(HTTPBase):
    """Polls a long poll URL, reporting any parsed work results to the
    callback function.
    """

    # 10 minutes should be a sane value for this.
    timeout = 600

    def __init__(self, url, root):
        self.url = url
        self.root = root
        self.polling = False

    def start(self):
        """Begin requesting data from the LP server, if we aren't already..."""
        if self.polling:
            return
        self.polling = True
        self._request()

    def _request(self):
        if self.polling:
            path = self.url.path or '/'
            if self.url.query:
                path += '?' + self.url.query
            d = self.doRequest(
                self.url,
                'GET',
                path,
                None,
                {
                    'Authorization': self.root.auth,
                    'User-Agent': self.root.version,
                    'X-Work-Identifier': '1',
                    'X-Mining-Extensions': self.root.EXTENSIONS
                })
            d.addBoth(self._requestComplete)

    def stop(self):
        """Stop polling. This LongPoller probably shouldn't be reused."""
        self.polling = False
        self.closeConnection()

    def _requestComplete(self, response):
        try:
            if not self.polling:
                return

            if isinstance(response, failure.Failure):
                return

            try:
                (headers, data) = response
            except TypeError:
                #handle case where response doesn't contain valid data
                self.root.runCallback('debug', 'TypeError in LP response:')
                self.root.runCallback('debug', str(response))
                return

            try:
                result = RPCPoller.parse(data)
            except ValueError:
                return
            except ServerMessage:
                exctype, value = sys.exc_info()[:2]
                self.root.runCallback('msg', str(value))
                return

        finally:
            self._request()

        self.root.handleWork(result, headers, True)

class RPCClient(ClientBase):
    """The actual root of the whole RPC client system."""

    EXTENSIONS = ' '.join([
        'midstate',
        'rollntime'
    ])

    def __init__(self, handler, url):
        self.handler = handler
        self.url = url
        self.params = {}
        for param in url.params.split('&'):
            s = param.split('=',1)
            if len(s) == 2:
                self.params[s[0]] = s[1]
        self.auth = 'Basic ' + ('%s:%s' % (
            url.username, url.password)).encode('base64').strip()
        self.version = 'RPCClient/2.0'

        self.poller = RPCPoller(self)
        self.longPoller = None # Gets created later...
        self.disconnected = False
        self.saidConnected = False
        self.block = None
        self.setupMaxtime()

    def connect(self):
        """Begin communicating with the server..."""

        self.poller.ask()

    def disconnect(self):
        """Cease server communications immediately. The client is probably not
        reusable, so it's probably best not to try.
        """

        self._deactivateCallbacks()
        self.disconnected = True
        self.poller.setInterval(None)
        self.poller.closeConnection()
        if self.longPoller:
            self.longPoller.stop()
            self.longPoller = None

    def setupMaxtime(self):
        try:
            self.maxtime = int(self.params['maxtime'])
            if self.maxtime < 0:
                self.maxtime = 0
            elif self.maxtime > 3600:
                self.maxtime = 3600
        except (KeyError, ValueError):
            self.maxtime = 60

    def setMeta(self, var, value):
        """RPC clients do not support meta. Ignore."""

    def setVersion(self, shortname, longname=None, version=None, author=None):
        if version is not None:
            self.version = '%s/%s' % (shortname, version)
        else:
            self.version = shortname

    def requestWork(self):
        """Application needs work right now. Ask immediately."""
        self.poller.ask()

    def sendResult(self, result):
        """Sends a result to the server, returning a Deferred that fires with
        a bool to indicate whether or not the work was accepted.
        """

        # Must be a 128-byte response, but the last 48 are typically ignored.
        result += '\x00'*48

        d = self.poller.call('getwork', [result.encode('hex')])

        def errback(*ignored):
            return False # ANY error while turning in work is a Bad Thing(TM).

        #we need to return the result, not the headers
        def callback(x):
            try:
                (headers, accepted) = x
            except TypeError:
                self.runCallback('debug',
                        'TypeError in RPC sendResult callback:')
                self.runCallback('debug', str(x))
                return False

            if (not accepted):
                self.handleRejectReason(headers)

            return accepted

        d.addCallback(callback)
        d.addErrback(errback)
        return d

    #if the server sends a reason for reject then print that
    def handleRejectReason(self, headers):
        reason = headers.get('x-reject-reason')
        if reason is not None:
            self.runCallback('debug', 'Reject reason: ' + str(reason))

    def useAskrate(self, variable):
        defaults = {'askrate': 10, 'retryrate': 15, 'lpaskrate': 0}
        try:
            askrate = int(self.params[variable])
        except (KeyError, ValueError):
            askrate = defaults.get(variable, 10)
        self.poller.setInterval(askrate)

    def handleWork(self, work, headers, pushed=False):
        if work is None:
            return;

        try:
            rollntime = headers.get('x-roll-ntime')
        except Exception:
            rollntime = None

        if rollntime:
            if rollntime.lower().startswith('expire='):
                try:
                    maxtime = int(rollntime[7:])
                except:
                    #if the server supports rollntime but doesn't format the
                    #request properly, then use a sensible default
                    maxtime = self.maxtime
            else:
                if rollntime.lower() in ('t', 'true', 'on', '1', 'y', 'yes'):
                    maxtime = self.maxtime
                elif rollntime.lower() in ('f', 'false', 'off', '0', 'n', 'no'):
                    maxtime = 0
                else:
                    try:
                        maxtime = int(rollntime)
                    except:
                        maxtime = self.maxtime
        else:
            maxtime = 0

        if self.maxtime < maxtime:
            maxtime = self.maxtime

        if not self.saidConnected:
            self.saidConnected = True
            self.runCallback('connect')
            self.useAskrate('askrate')

        aw = AssignedWork()
        aw.data = work['data'].decode('hex')[:80]
        aw.target = work['target'].decode('hex')
        aw.mask = work.get('mask', 32)
        aw.setMaxTimeIncrement(maxtime)
        aw.identifier = work.get('identifier', aw.data[4:36])
        if pushed:
            self.runCallback('push', aw)
        self.runCallback('work', aw)

    def handleHeaders(self, headers):
        try:
            block = int(headers['x-blocknum'])
        except (KeyError, ValueError):
            pass
        else:
            if self.block != block:
                self.block = block
                self.runCallback('block', block)
        try:
            longpoll = headers.get('x-long-polling')
        except:
            longpoll = None

        if longpoll:
            lpParsed = urlparse.urlparse(longpoll)
            lpURL = urlparse.ParseResult(
                lpParsed.scheme or self.url.scheme,
                lpParsed.netloc or self.url.netloc,
                lpParsed.path, lpParsed.query, '', '')
            if self.longPoller and self.longPoller.url != lpURL:
                self.longPoller.stop()
                self.longPoller = None
            if not self.longPoller:
                self.longPoller = LongPoller(lpURL, self)
                self.longPoller.start()
                self.useAskrate('lpaskrate')
                self.runCallback('longpoll', True)
        elif self.longPoller:
            self.longPoller.stop()
            self.longPoller = None
            self.useAskrate('askrate')
            self.runCallback('longpoll', False)

    def _failure(self):
        if self.saidConnected:
            self.saidConnected = False
            self.runCallback('disconnect')
        else:
            self.runCallback('failure')
        self.useAskrate('retryrate')
        if self.longPoller:
            self.longPoller.stop()
            self.longPoller = None
            self.runCallback('longpoll', False)
