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

from twisted.internet import reactor
from phoenix2.core.PhoenixCore import PhoenixCore

import sys, os

def main():
    if len(sys.argv) > 1:
        cfg = sys.argv[1]
    elif sys.platform == 'win32':
        # The Windows users get their own special treatment. The script creates
        # an empty configuration file for them if they don't specify one.
        cfg = os.path.join(os.path.dirname(sys.argv[0]), 'phoenix.cfg')
        if not os.path.isfile(cfg) or os.stat(cfg).st_size == 0:
            print('-'*79)
            print('It looks like this is your first time running Phoenix.')
            #print('Please go to http://localhost:7780 in a web browser to'
            #      ' set it up.')
            print('Please edit your phoenix.cfg file')
            print('-'*79)
            open(cfg, 'a').close()
    else:
        print('Please specify a configuration file.')
        sys.exit()

    if not os.path.isfile(cfg):
        print('Error: %s does not exist.' % cfg)
        sys.exit()

    pc = PhoenixCore(cfg)
    pc.start()

    reactor.run()

