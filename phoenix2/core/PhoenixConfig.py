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

class PhoenixConfig(object):
    PRESENT = object() # Special identifier to indicate a value is present
                       # but has no overridden value.

    def __init__(self, filename):
        self.filename = filename
        self.text = ''
        self.sections = {}
        self.sectionlist = [] # An in-order list
        self.load()

    def load(self):
        self.setraw(open(self.filename, 'r').read())

    def setraw(self, text):
        self.text = text
        self.sections = self._parse(self.text)

    def save(self):
        open(self.filename, 'w').write(self.text)

    def set(self, section, var, value):
        section = section.lower().replace('#','').strip()
        var = var.lower().replace('#','').strip()
        if value is not None:
            value = str(value).replace('#','').strip()
            self.sections.setdefault(section, {})[var] = value
        else:
            if section not in self.sections:
                return # Don't create an empty section just to delete a var.
            section_dict = self.sections[section]
            if var not in section_dict:
                return # Don't bother deleting an already-missing var.
            del section_dict[var]
        self._alter(section, var, value)
        if section not in self.sectionlist:
            self.sectionlist.append(section)
        assert self._parse(self.text) == self.sections
        assert set(self.sectionlist) == set(self.sections.keys())

    def get(self, section, var, type, default=None):
        section = section.lower(); var = var.lower();
        value = self.sections.get(section, {}).get(var, None)
        if value is None:
            return default
        else:
            if type == bool:
                return (value == self.PRESENT or
                        value.lower() in ('t', 'true', 'on', '1', 'y', 'yes'))
            elif value == self.PRESENT:
                return default
            else:
                return type(value)

    def listsections(self):
        return self.sectionlist

    def getsection(self, section):
        return self.sections.get(section, {})

    @classmethod
    def _3strip(cls, text):
        # Performs a 3-way strip on the text, returning a tuple:
        # (left, stripped, right)
        # Where left/right contain the whitespace removed from the text.
        # N.B. this considers comments to be whitespace and will thus be
        # included in "right"
        s = text.split('#',1)
        ls = s[0].lstrip()
        left = s[0][:-len(ls)]
        stripped = ls.rstrip()
        right = ls[len(stripped):]
        if len(s) == 2:
            right += '#' + s[1]
        return (left, stripped, right)

    @classmethod
    def _parseLine(cls, line):
        _, line, _ = cls._3strip(line)
        if not line:
            return None, None

        if line == '[' + line[1:-1] + ']':
            return None, line[1:-1].lower()

        linesplit = line.split('=', 1)

        if len(linesplit) == 2:
            value = linesplit[1].strip()
        else:
            value = None

        return linesplit[0].strip().lower(), value

    def _parse(self, text):
        sections = {}
        section = None
        self.sectionlist = []

        for line in text.splitlines():
            var, value = self._parseLine(line)

            if var is None and value is None:
                pass
            elif var is None:
                section = sections.setdefault(value, {})
                self.sectionlist.append(value)
            else:
                if value is None:
                    value = self.PRESENT
                if section is not None:
                    section.setdefault(var, value) # First is greatest priority

        return sections

    def _alter(self, section, var, value):
        thisSection = None
        i = 0
        lastLineEnd = 0
        for line in self.text.splitlines(True):
            linevar, linevalue = self._parseLine(line)

            if linevar is None and linevalue is None:
                pass # Ignore blank lines entirely.
            elif linevar is None:
                if thisSection == section:
                    # Instead of leaving the section, insert the line:
                    self.text = (self.text[:lastLineEnd]
                                 + ('%s = %s\n' % (var, value))
                                 + self.text[lastLineEnd:])
                    return
                else:
                    thisSection = linevalue
                    lastLineEnd = i+len(line)
            elif linevar == var and thisSection == section:
                if value is None:
                    self.text = self.text[:i] + self.text[i+len(line):]
                    return
                # Carefully split to preserve whitespace and comment...
                left, stripped, right = self._3strip(line)
                split = stripped.split('=',1)
                if len(split) == 2:
                    ws, _, _ = self._3strip(split[1])
                    split[1] = ws + value
                else:
                    split[0] += ' = ' + value
                stripped = '='.join(split)
                self.text = (self.text[:i]
                             + left + stripped + right
                             + self.text[i+len(line):])
                return
            else:
                lastLineEnd = i+len(line)

            i += len(line)

        # Fell out of the loop without making a modification to a variable!
        if thisSection == section:
            # Already in the correct section, just add variable.
            if not self.text.endswith('\n'):
                self.text += '\n'
            self.text += '%s = %s\n' % (var, value)
        else:
            # Section isn't in the file... Just add it to the bottom.
            self.text += '\n[%s]\n%s = %s\n' % (section, var, value)
