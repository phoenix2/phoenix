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

from twisted.internet.defer import Deferred

from MMPProtocol import MMPClient
from RPCProtocol import RPCClient

def openURL(url, handler):
    """Parses a URL and opens a connection using the appropriate client."""

    parsed = urlparse.urlparse(url)
    if parsed.scheme.lower() == 'mmp':
        client = MMPClient(handler, parsed.hostname or 'localhost',
            parsed.port or 8880, parsed.username or 'default',
            parsed.password or 'default')

        for var, value in urlparse.parse_qsl(parsed.path.lstrip('/?')):
            client.setMeta(var, value)

        return client
    elif parsed.scheme.lower() in ['http', 'https']:
        return RPCClient(handler, parsed)
    else:
        raise ValueError('Unknown protocol: ' + parsed.scheme)

class TestHandler(object):
    def __init__(self, d):
        self.d = d
        self.conn = None

    def test(self, url):
        self.conn = openURL(url, self)
        self.conn.connect()

    def onConnect(self):
        if not self.conn: return
        self.d.callback(True)
        self.conn.disconnect()
        self.conn = None

    def onFailure(self):
        if not self.conn: return
        self.d.callback(False)
        self.conn.disconnect()
        self.conn = None

    def _dummy(self, *args): pass
    def __getattr__(self, attr):
        if attr.startswith('on'):
            return self._dummy


def testURL(url):
    """Return a Deferred specifying whether this URL is available or not."""

    d = Deferred()

    testHandler = TestHandler(d)
    testHandler.test(url)

    return d
