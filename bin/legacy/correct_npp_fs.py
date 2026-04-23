#!/usr/bin/python
# Copyright (c) 2016.
#

# Author(s):
#   Martin Raspaud <martin.raspaud@smhi.se>

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

"""
"""
import sys

plen = 1024

with open(sys.argv[1], 'r+') as fd:
    while True:
        offset = fd.tell()
        packet = fd.read(plen)
        if packet:
            if bytearray(packet[:4]) != b'\x1a\xcf\xfc\x1d':
            #if bytearray(packet[:4]) == b'\x1a\xcf\xf4\x1d':
                fd.seek(offset, 0)
                fd.write(b'\x1a\xcf\xfc\x1d')
                fd.seek(offset + plen, 0)
        else:
            break