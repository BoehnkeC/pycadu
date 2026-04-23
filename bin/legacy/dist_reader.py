#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright (c) 2017 Martin Raspaud

# Author(s):

#   Martin Raspaud <martin.raspaud@smhi.se>

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Read in a distributed manner."""

from multiprocessing import JoinableQueue, Process
from Queue import Empty, Queue
from threading import Thread

from joblib import Parallel, delayed

import npp_reader as nr


class APIDReader(Process):
    """Reader for a give APID."""

    def __init__(self, apid):
        super(APIDReader, self).__init__()
        self.apid = apid
        self.queue = JoinableQueue()
        self.buffer = []
        self.zone = []
        self.running = True

    def ingest(self, packet_bundle):
        """Ingest a data packet."""
        try:
            hdr, packet = packet_bundle
        except TypeError:
            self.running = False
            return
        sec_hdr = bool((hdr["ccsds_version"] >> 11) & 1)
        seq_flag = (hdr["sequence"] >> 14) & 3
        if seq_flag == 0b11:
            # standalone packet
            self.queue.put([packet[6:]])
            self.buffer = []
        elif seq_flag == 0b01:
            # start packet
            self.buffer = [packet[6:]]
        elif seq_flag == 0b00:
            # continuation packet
            if len(self.buffer) == 0:
                self.buffer.append(None)
                print "missing start packet"
            self.buffer.append(packet[6:])
        elif seq_flag == 0b10:
            # end packet
            self.buffer.append(packet[6:])
            self.queue.put(self.buffer)
            self.buffer = []

    def queue_iterator(self):
        while True:
            try:
                pack = self.queue.get(timeout=2)
            except Empty:
                if not self.running:
                    break
                continue
            if len(pack) > 1:
                yield pack
                #self.zone.append(nr.build_scan(pack, self.apid))
            self.queue.task_done()

    def run(self):
        """Decode the data."""
        self.zone = Parallel(n_jobs=4)(delayed(nr.build_scan)(pack, self.apid)
                                       for pack in self.queue_iterator())


class DistributedReader(object):
    """Read file distributedly."""

    def __init__(self, apids_to_read):
        super(DistributedReader, self).__init__()
        self.areaders = {apid: APIDReader(apid) for apid in apids_to_read}
        for reader in self.areaders.values():
            reader.start()

    def ingest(self, packet_bundle):
        """Ingest a data packet."""
        hdr, packet = packet_bundle
        apid = hdr["ccsds_version"] & (2**11 - 1)
        try:
            self.areaders[apid].ingest((hdr, packet))
        except KeyError:
            pass

    def stop(self):
        for reader in self.areaders.values():
            reader.ingest(None)
            reader.queue.join()
            print 'reader done', reader.apid

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("cadu_file", help="cadu file to read")
    parser.add_argument("channel", nargs='+', help="channel to read")
    parser.add_argument("-p", '--pn-decode',
                        action='store_true', help="perform PN decoding")
    opts = parser.parse_args()

    reqs = opts.channel
    to_read = []
    for req in opts.channel:
        to_read.extend(nr.get_deps(req))
    print 'reading', to_read
    apids_to_read = [nr.channels[channel] for channel in to_read]
    apids_to_read.reverse()

    reader = DistributedReader(apids_to_read)

    for packet in nr.ccsds_iterator(opts.cadu_file, vcid=16, pn=opts.pn_decode):
        reader.ingest(packet)
    reader.stop()
