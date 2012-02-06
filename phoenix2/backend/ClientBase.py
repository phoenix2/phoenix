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

class AssignedWork(object):
    data = None
    mask = None
    target = None
    maxtime = None
    time = None
    identifier = None
    def setMaxTimeIncrement(self, n):
        self.time = n
        self.maxtime = struct.unpack('>I', self.data[68:72])[0] + n

class ClientBase(object):
    callbacksActive = True

    def _deactivateCallbacks(self):
        """Shut down the runCallback function. Typically used post-disconnect.
        """
        self.callbacksActive = False

    def runCallback(self, callback, *args):
        """Call the callback on the handler, if it's there, specifying args."""

        if not self.callbacksActive:
            return

        func = getattr(self.handler, 'on' + callback.capitalize(), None)
        if callable(func):
            func(*args)