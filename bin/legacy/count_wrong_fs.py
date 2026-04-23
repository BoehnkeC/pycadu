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
import glob

plen = 1024

total = 0
wrong = 0

filenames = sys.argv[1]

if '*' in filenames:
    filenames = glob.glob(filenames)

print 'Ready'
sys.stdout.flush()

for filename in filenames:
    with open(filename, 'r') as fd:
        while True:
            packet = fd.read(4)
            if packet:
                total += 1
                if bytearray(packet[:4]) != b'\x1a\xcf\xfc\x1d':
                    wrong += 1
            else:
                break
            fd.seek(plen - 4, 1)
    if total != 0:
        print filename, wrong, total, wrong * 100.0 / total, '%'
    sys.stdout.flush()
    wrong = 0
    total = 0