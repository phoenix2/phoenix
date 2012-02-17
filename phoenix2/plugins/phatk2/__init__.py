# Copyright (C) 2011 by jedi95 <jedi95@gmail.com> and
#                       CFSworks <CFSworks@gmail.com> and
#                       Phateus <jesse.moll@gmail.com>
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

import pyopencl as cl
import numpy as np
import os
import sys

from math import log
from hashlib import md5
from struct import pack, unpack
from twisted.internet import reactor

from phoenix2.util.Midstate import calculateMidstate
from phoenix2.util.QueueReader import QueueReader
from phoenix2.core.KernelInterface import *
from phoenix2.util.BFIPatcher import *

# Yes, this behaves exactly like an import statement...
importPlugin('opencl')

class KernelData(object):
    """This class is a container for all the data required for a single kernel
    execution.
    """

    def __init__(self, nr, rateDivisor, aggression):
        # Prepare some raw data, converting it into the form that the OpenCL
        # function expects.
        data = np.array(
               unpack('IIII', nr.unit.data[64:]), dtype=np.uint32)

        # get the number of iterations from the aggression and size
        self.iterations = int(nr.size / (1 << aggression))
        self.iterations = max(1, self.iterations)

        #set the size to pass to the kernel based on iterations and vectors
        self.size = (nr.size / rateDivisor) / self.iterations

        #compute bases for each iteration
        self.base = [None] * self.iterations
        for i in range(self.iterations):
            if rateDivisor == 1:
                self.base[i] = pack('I',
                    (nr.base + (i * self.size * rateDivisor)))
            if rateDivisor == 2:
                self.base[i] = pack('II',
                    (nr.base + (i * self.size * rateDivisor))
                    , (1 + nr.base + (i * self.size * rateDivisor)))
            if rateDivisor == 4:
                self.base[i] = pack('IIII',
                    ((nr.base) + (i * self.size * rateDivisor))
                    , (1 + nr.base + (i * self.size * rateDivisor))
                    , (2 + nr.base + (i * self.size * rateDivisor))
                    , (3 + nr.base + (i * self.size * rateDivisor))
                    )
        #set up state and precalculated static data
        self.state = np.array(
            unpack('IIIIIIII', nr.unit.midstate), dtype=np.uint32)
        self.state2 = np.array(unpack('IIIIIIII',
            calculateMidstate(nr.unit.data[64:80] +
                '\x00\x00\x00\x80' + '\x00'*40 + '\x80\x02\x00\x00',
                nr.unit.midstate, 3)), dtype=np.uint32)
        self.state2 = np.array(
            list(self.state2)[3:] + list(self.state2)[:3], dtype=np.uint32)
        self.nr = nr

        self.f = np.zeros(9, np.uint32)
        self.calculateF(data)

    def calculateF(self, data):
        rotr = lambda x,y: x>>y | x<<(32-y)
        #W2
        self.f[0] = np.uint32(data[2])

        #W16
        W16 = np.uint32(data[0] + (rotr(data[1], 7) ^ rotr(data[1], 18) ^
            (data[1] >> 3)))
        self.f[1] = W16
        #W17
        W17 = np.uint32(data[1] + (rotr(data[2], 7) ^ rotr(data[2], 18) ^
            (data[2] >> 3)) + 0x01100000)
        self.f[2] = W17

        #2 parts of the first SHA round
        PreVal4 = (self.state[4] + (rotr(self.state2[1], 6) ^
            rotr(self.state2[1], 11) ^ rotr(self.state2[1], 25)) +
            (self.state2[3] ^ (self.state2[1] & (self.state2[2] ^
            self.state2[3]))) + 0xe9b5dba5)
        T1 = ((rotr(self.state2[5], 2) ^
            rotr(self.state2[5], 13) ^ rotr(self.state2[5], 22)) +
            ((self.state2[5] & self.state2[6]) | (self.state2[7] &
            (self.state2[5] | self.state2[6]))))
        self.f[3] = np.uint32(( PreVal4 + T1))
        self.f[4] = np.uint32( PreVal4 + self.state[0])
        self.f[5] = np.uint32(0x00000280 + ((rotr(W16, 7) ^
            rotr(W16, 18) ^ (W16 >> 3))))
        self.f[6] = np.uint32(self.f[1] + ((rotr(W17, 7) ^
            rotr(W17, 18) ^ (W17 >> 3))))

        self.f[7] = np.uint32(0x11002000 + (rotr(W17, 17) ^ rotr(W17, 19) ^
            (W17 >> 10)))
        self.f[8] = np.uint32(data[2] + (rotr(W16, 17) ^ rotr(W16, 19) ^
            (W16 >> 10)))

# phatk2 is implemented by inheriting from opencl and then overriding the
# nessesary functions. See kernels/opencl to see the rest of the code.
class PhoenixKernel(opencl.PhoenixKernel):
    """A Phoenix Miner-compatible OpenCL kernel created by Phateus."""

    # This must be manually set for Git
    REVISION = 1

    @classmethod
    def analyzeDevice(cls, devid):
        # This class method is for analyzing how well a kernel will support a
        # specific device to help Phoenix automatically choose kernels.
        # See doc/cpu.py for further details.

        # Make sure we only deal with OpenCL devices.
        if devid.startswith('cl:'):
            (platform, device) = cls.getDevice(devid)

            if (platform is not None) and (device is not None):
                # Get the device name
                name = device.name.replace('\x00','').strip()

                # Check if the device is a CPU
                if device.get_info(cl.device_info.TYPE) == cl.device_type.CPU:
                    return (1, {'name': name, 'aggression': 0},
                                [devid, 'cpu:0'])

                # Check if the device has CUDA support
                ids = devid.split(':',3)
                if 'nvidia cuda' in platform.name.lower():
                    return (1, {'name': (name + ' ' + ids[2]),
                            'aggression': 3, }, [devid, 'cuda:' + ids[2]])

                # Check if the device supports BFI_INT
                if (device.extensions.find('cl_amd_media_ops') != -1):
                    supported = False
                    for whitelisted in WHITELIST:
                        if name in whitelisted:
                            supported = True

                    if supported:
                        return (3, {'name': (name + ' ' + ids[2]),
                                'bfi_int': True, 'vectors': True}, [devid])

                # Otherwise just use a safe default config
                return (1, {'name': (name + ' ' + ids[2]),
                        'aggression': 3}, [devid])
            else:
                return (0, {}, [devid])
        else:
            return (0, {}, [devid])

    # This override is required to load the correct kernel.cl
    def getKernelPath(self):
        return os.path.split(__file__)

    def applyMeta(self):
        """Apply any kernel-specific metadata."""
        self.interface.setMeta('kernel', 'phatk2 r%s' % self.REVISION)
        self.interface.setMeta('device',
                                self.device.name.replace('\x00','').strip())
        self.interface.setMeta('cores', self.device.max_compute_units)

    def preprocess(self, nr):
        if self.FASTLOOP:
            self.updateIterations()

        kd = KernelData(nr, self.rateDivisor, self.AGGRESSION)
        return kd

    def mineThread(self):
        for data in self.qr:
            for i in range(data.iterations):
                self.kernel.search(
                    self.commandQueue, (data.size, ), (self.WORKSIZE, ),
                    data.state[0], data.state[1], data.state[2], data.state[3],
                    data.state[4], data.state[5], data.state[6], data.state[7],
                    data.state2[1], data.state2[2], data.state2[3],
                    data.state2[5], data.state2[6], data.state2[7],
                    data.base[i],
                    data.f[1],data.f[2],
                    data.f[3],data.f[4],
                    data.f[5],data.f[6],
                    data.f[7],data.f[8],
                    self.output_buf)
                cl.enqueue_read_buffer(self.commandQueue, self.output_buf,
                                       self.output, is_blocking=False)
                self.commandQueue.finish()

                # The OpenCL code will flag the last item in the output buffer
                # when it finds a valid nonce. If that's the case, send it to
                # the main thread for postprocessing and clean the buffer
                # for the next pass.
                if self.output[self.WORKSIZE]:
                    reactor.callFromThread(self.postprocess,
                    self.output.copy(), data.nr)

                    self.output.fill(0)
                    cl.enqueue_write_buffer(
                        self.commandQueue, self.output_buf, self.output)
