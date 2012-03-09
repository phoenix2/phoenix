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
import os
import json

from twisted.internet import reactor, defer, error
from twisted.web import server, script
from twisted.web.resource import Resource
from twisted.web.static import File

def rpcError(code, msg):
    return '{"result": null, "error": {"code": %d, "message": "%s"}, ' \
            '"id": null, "jsonrpc": "2.0"}' % (code, msg)

class PhoenixRPC(Resource):
    def __init__(self, core):
        Resource.__init__(self)
        self.core = core

        self.listen = None

        self.port = None
        self.ip = None

    def start(self):
        """Read configuration and start hosting the webserver."""
        disabled = self.core.config.get('web', 'disabled', bool, False)
        port = self.core.config.get('web', 'port', int, 7780)
        ip = self.core.config.get('web', 'bind', str, '')

        if not disabled and port != self.port or ip != self.ip:
            self.port = port
            self.ip = ip
            if self.listen:
                self.listen.stopListening()
            try:
                self.listen = reactor.listenTCP(port, server.Site(self),
                                                interface=ip)
            except error.CannotListenError:
                self.listen = None

    def getChild(self, name, request):
        versionString = 'phoenix/%s' % self.core.VERSION
        request.setHeader('Server', versionString)
        if request.method == 'POST' and request.path == '/':
            return self
        else:
            docroot = os.path.join(self.core.basedir, 'www')
            root = File(self.core.config.get('web', 'root', str, docroot))
            root.processors = {'.rpy': script.ResourceScript}
            return root.getChild(name, request)

    def render_POST(self, request):
        request.setHeader('Content-Type', 'application/json')

        passwordGood = (request.getPassword() ==
                        self.core.config.get('web', 'password', str,
                                             'phoenix'))

        # This is a workaround for WebKit bug #32916, Mozilla bug #282547,
        # et al... Don't send the WWW-Authenticate header for present, but
        # invalid, credentials.
        if not request.getHeader('Authorization') or passwordGood:
            request.setHeader('WWW-Authenticate', 'Basic realm="Phoenix RPC"')

        if not passwordGood:
            request.setResponseCode(401)
            return rpcError(-1, 'Password invalid.')

        try:
            data = json.loads(request.content.read())
            id = data['id']
            method = str(data['method'])
            if 'params' in data:
                params = tuple(data['params'])
            else:
                params = ()
        except ValueError:
            return rpcError(-32700, 'Parse error.')
        except (KeyError, TypeError):
            return rpcError(-32600, 'Invalid request.')

        func = getattr(self.core.pluginIntf, method, None)
        if func is None or getattr(func, 'rpc_forbidden', False):
            return rpcError(-32601, 'Method not found.')

        d = defer.maybeDeferred(func, *params)

        def callback(result):
            jsonResult = json.dumps({'result': result, 'error': None,
                                     'id': id, "jsonrpc": "2.0"})
            request.write(jsonResult)
            request.finish()
        d.addCallback(callback)

        def errback(failure):
            if failure.trap(TypeError, ValueError):
                request.write(rpcError(-1, 'Invalid arguments.'))
                request.finish()
        d.addErrback(errback)

        return server.NOT_DONE_YET
