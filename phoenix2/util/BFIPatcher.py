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

# List of devices known to support BFI_INT patching
WHITELIST = [   'Antilles',
                'Barts',
                'BeaverCreek',
                'Caicos',
                'Cayman',
                'Cedar',
                'Cypress',
                'Devastator',
                'Hemlock',
                'Juniper',
                'Loveland',
                'Palm',
                'Redwood',
                'Scrapper',
                'Sumo',
                'Turks',
                'WinterPark',
                'Wrestler']

class PatchError(Exception): pass

class BFIPatcher(object):
    """Patches .ELF files compiled for VLIW4/VLIW5 GPUs; changes the microcode
    so that any BYTE_ALIGN_INT instructions become BFI_INT.
    """

    def __init__(self, interface):
        self.interface = interface

    def patch(self, data):
        """Run the process of patching an ELF."""

        self.interface.debug('Finding inner ELF...')
        innerPos = self.locateInner(data)
        self.interface.debug('Patching inner ELF...')
        inner = data[innerPos:]
        patched = self.patchInner(inner)
        self.interface.debug('Patch complete, returning to kernel...')
        return data[:innerPos] + patched

    def patchInner(self, data):
        sections = self.readELFSections(data)
        # We're looking for .text -- there should be two of them.
        textSections = filter(lambda x: x[0] == '.text', sections)
        if len(textSections) != 2:
            self.interface.debug('Inner ELF does not have 2 .text sections!')
            self.interface.debug('Sections are: %r' % sections)
            raise PatchError()
        name, offset, size = textSections[1]
        before, text2, after = (data[:offset], data[offset:offset+size],
            data[offset+size:])

        self.interface.debug('Patching instructions...')
        text2 = self.patchInstructions(text2)
        return before + text2 + after

    def patchInstructions(self, data):
        output = ''
        nPatched = 0
        for i in xrange(len(data)/8):
            inst, = struct.unpack('Q', data[i*8:i*8+8])
            # Is it BYTE_ALIGN_INT?
            if (inst&0x9003f00002001000) == 0x0001a00000000000:
                nPatched += 1
                inst ^=  (0x0001a00000000000 ^ 0x0000c00000000000) # BFI_INT
            output += struct.pack('Q', inst)
        self.interface.debug('BFI-patched %d instructions...' % nPatched)
        if nPatched < 60:
            self.interface.debug('Patch safety threshold not met!')
            raise PatchError()
        return output

    def locateInner(self, data):
        """ATI uses an ELF-in-an-ELF. I don't know why. This function's job is
        to find it.
        """

        pos = data.find('\x7fELF', 1)
        if pos == -1 or data.find('\x7fELF', pos+1) != -1: # More than 1 is bad
            self.interface.debug('Inner ELF not located!')
            raise PatchError()
        return pos

    def readELFSections(self, data):
        try:
            (ident1, ident2, type, machine, version, entry, phoff,
                shoff, flags, ehsize, phentsize, phnum, shentsize, shnum,
                shstrndx) = struct.unpack('QQHHIIIIIHHHHHH', data[:52])

            if ident1 != 0x64010101464c457f:
                self.interface.debug('Invalid ELF header!')
                raise PatchError()

            # No section header?
            if shoff == 0:
                return []

            # Find out which section contains the section header names
            shstr = data[shoff+shstrndx*shentsize:shoff+(shstrndx+1)*shentsize]
            (nameIdx, type, flags, addr, nameTableOffset, size, link, info,
                addralign, entsize) = struct.unpack('IIIIIIIIII', shstr)

            # Grab the section header.
            sh = data[shoff:shoff+shnum*shentsize]

            sections = []
            for i in xrange(shnum):
                rawEntry = sh[i*shentsize:(i+1)*shentsize]
                (nameIdx, type, flags, addr, offset, size, link, info,
                    addralign, entsize) = struct.unpack('IIIIIIIIII', rawEntry)
                nameOffset = nameTableOffset + nameIdx
                name = data[nameOffset:data.find('\x00', nameOffset)]
                sections.append((name, offset, size))

            return sections
        except struct.error:
            self.interface.debug('A struct.error occurred while reading ELF!')
            raise PatchError()