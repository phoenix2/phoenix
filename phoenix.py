#!/usr/bin/env python

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

if __name__ == '__main__':
    main()
