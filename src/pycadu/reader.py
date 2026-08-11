"""Generic CADU/VCDU streaming and batch frame reassembly."""

import logging
from collections.abc import Generator, Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from pycadu.cadu import extract_cadu_data, extract_vcdu_payload, extract_vcid
from pycadu.constants import CADU_DATA_LENGTH, CCSDS_HEADER_LENGTH
from pycadu.sync import _build_shifted, find_sync_pattern

logger = logging.getLogger(__name__)


@dataclass
class FrameTailState:
    """Leftover `vcdu_frames` reassembly state to resume in the next chunk.

    Every field is a plain bytes/int/None value, so this is trivially
    serializable to pass across separate pipeline invocations if consecutive
    chunks aren't processed in one Python process.
    """

    buf: bytes
    bit_shift: int | None
    carry: int
    raw_buf: bytes | None
    desynced: bool = False


@dataclass
class FrameReadResult:
    """Frames extracted from one chunk, plus leftover state to resume the next.

    Feed the four `initial_*`-mapped fields (`tail_buf`, `bit_shift`,
    `raw_carry`, `sync_acc`) into the next chunk's `read_frames_batch`
    call to reassemble frames correctly across a chunk boundary. All of them
    are plain bytes/int/None, so this is trivially serializable to pass across
    separate pipeline invocations if consecutive chunks aren't processed in
    one Python process.
    """

    frames: list[bytes]
    tail_buf: bytes
    bit_shift: int | None
    raw_carry: int
    sync_acc: bytes | None
    desynced: bool = False


def cadu_blocks(path: Path, stride: int) -> Iterator[bytes]:
    """Yield one raw physical block at a time without loading the whole file."""
    with open(path, "rb") as fh:
        while block := fh.read(stride):
            if len(block) == stride:
                yield block


def vcdu_payloads(blocks: Iterator[bytes], stride: int, target_vcid: int) -> Iterator[bytes]:
    """Yield VCDU payload bytes for blocks matching *target_vcid*."""
    for block in blocks:
        if extract_vcid(block) == target_vcid:
            yield extract_vcdu_payload(extract_cadu_data(block, stride))


def vcdu_frames(
    payloads: Iterator[bytes],
    pattern: bytes,
    frame_size: int,
    *,
    initial_raw_buf: bytes = b"",
    initial_buf: bytes = b"",
    initial_bit_shift: int | None = None,
    initial_carry: int = 0,
) -> Generator[bytes, None, FrameTailState]:
    """Reassemble VCDU payload chunks into complete fixed-length frames.

    Detects the bit-shift of the sync pattern (0-7 bits) on first sync find,
    then unshifts each subsequent chunk using a one-byte carry to maintain
    bit alignment across chunk boundaries.

    To resume reassembly across a chunk boundary (e.g. a scene split into
    multiple `.tm` files), pass the `FrameTailState` this generator
    returns (available as `StopIteration.value` once it's exhausted) into
    the next chunk's call as `initial_raw_buf`/`initial_buf`/
    `initial_bit_shift`/`initial_carry`. Consecutive chunks must be fed in
    sequential order.
    """
    raw_buf = bytearray(initial_raw_buf)
    buf = bytearray(initial_buf)
    bit_shift: int | None = initial_bit_shift
    carry: int = initial_carry
    had_carried_raw_buf = bool(initial_raw_buf)

    for chunk in payloads:
        if bit_shift is None:
            raw_buf.extend(chunk)
            byte_pos, shift = find_sync_pattern(bytes(raw_buf), pattern)
            if byte_pos == -1:
                keep = len(pattern) + 1
                if len(raw_buf) > keep:
                    del raw_buf[: len(raw_buf) - keep]
                continue
            if shift:
                aligned = _build_shifted(bytes(raw_buf), shift)
                buf = bytearray(aligned[byte_pos:])
            else:
                buf = bytearray(raw_buf[byte_pos:])
            carry = raw_buf[-1]
            bit_shift = shift
        else:
            if bit_shift:
                arr = np.frombuffer(bytes([carry]) + bytes(chunk), dtype=np.uint8)
                lo = arr[:-1].astype(np.uint16) << bit_shift
                hi = arr[1:].astype(np.uint16) >> (8 - bit_shift)
                buf.extend(((lo | hi) & 0xFF).astype(np.uint8))
                carry = chunk[-1] if chunk else carry
            else:
                buf.extend(chunk)

        while len(buf) >= frame_size:
            if bytes(buf[: len(pattern)]) != pattern:
                nxt = bytes(buf).find(pattern)
                if nxt == -1:
                    buf.clear()
                    break
                del buf[:nxt]
                continue
            yield bytes(buf[:frame_size])
            del buf[:frame_size]

    desynced = had_carried_raw_buf and bit_shift is None
    if desynced:
        logger.warning(
            "vcdu_frames: sync pattern not found even after carrying over %d byte(s) "
            "of unsynced data from a previous chunk; this may indicate the chunks "
            "were fed out of order or the data is corrupted, rather than a normal "
            "chunk boundary.",
            len(initial_raw_buf),
        )

    return FrameTailState(
        buf=bytes(buf),
        bit_shift=bit_shift,
        carry=carry,
        raw_buf=bytes(raw_buf) if bit_shift is None else None,
        desynced=desynced,
    )


def iter_vcdu_frames(path: Path, pattern: bytes, frame_size: int, vcid: int, stride: int) -> Iterator[bytes]:
    """Yield complete frames from a CADU file for the given VCID."""
    return vcdu_frames(vcdu_payloads(cadu_blocks(path, stride), stride, vcid), pattern, frame_size)


def read_frames_batch(
    path: Path,
    stride: int,
    vcid: int,
    pattern: bytes,
    frame_size: int,
    *,
    initial_buf: bytes = b"",
    initial_bit_shift: int | None = None,
    initial_raw_carry: int = 0,
    initial_sync_acc: bytes | None = None,
) -> FrameReadResult:
    """Read all frames from a CADU file using vectorised numpy operations.

    Processes blocks in chunks to keep peak memory independent of file size
    (~200 MB peak regardless of file size).  ~20x faster than the streaming
    generator on a typical pass file.

    To reassemble frames correctly across a scene split into multiple ``.tm``
    chunk files, pass the ``tail_buf``/``bit_shift``/``raw_carry``/``sync_acc``
    fields of the ``FrameReadResult`` returned for one chunk as the
    ``initial_*`` arguments for the next chunk's call. Consecutive chunks must
    be processed in sequential order — this only works if the caller knows
    that ordering.
    """
    import mmap as _mmap

    pat_len = len(pattern)
    payload_len = CADU_DATA_LENGTH - CCSDS_HEADER_LENGTH
    blocks_per_chunk = 10_000
    _max_chunk_payload = blocks_per_chunk * payload_len

    buf = bytearray(initial_buf)
    bit_shift: int | None = initial_bit_shift
    raw_carry: int = initial_raw_carry
    had_carried_sync_acc = bool(initial_sync_acc)
    sync_acc: bytearray | None = None if bit_shift is not None else bytearray(initial_sync_acc or b"")
    frames: list[bytes] = []
    _ext = np.empty(_max_chunk_payload + 1, dtype=np.uint8)
    _sft = np.empty(_max_chunk_payload, dtype=np.uint8)
    _rhs = np.empty(_max_chunk_payload, dtype=np.uint8)

    with open(path, "rb") as _fh:
        with _mmap.mmap(_fh.fileno(), 0, access=_mmap.ACCESS_READ) as _mm:
            raw = np.frombuffer(_mm, dtype=np.uint8)
            n_blocks = len(raw) // stride

            for chunk_start in range(0, n_blocks, blocks_per_chunk):
                chunk_end = min(chunk_start + blocks_per_chunk, n_blocks)
                chunk = raw[chunk_start * stride : chunk_end * stride].reshape(chunk_end - chunk_start, stride)
                vcids = chunk[:, 5] & np.uint8(0x3F)
                mersi_chunk = chunk[vcids == vcid]
                del chunk, vcids
                if len(mersi_chunk) == 0:
                    del mersi_chunk
                    continue
                payloads = np.ascontiguousarray(mersi_chunk[:, CCSDS_HEADER_LENGTH:CADU_DATA_LENGTH]).ravel()
                del mersi_chunk

                if bit_shift is None:
                    assert sync_acc is not None
                    prev_len = len(sync_acc)
                    sync_acc.extend(payloads)
                    search_start = max(0, prev_len - len(pattern))
                    byte_pos, found_shift = find_sync_pattern(bytes(sync_acc), pattern, start=search_start)
                    if byte_pos == -1:
                        raw_carry = int(sync_acc[-1])
                        del payloads
                        continue
                    bit_shift = found_shift
                    shifted_init = _build_shifted(bytes(sync_acc), bit_shift) if bit_shift else bytes(sync_acc)
                    buf.extend(shifted_init[byte_pos:])
                    raw_carry = int(sync_acc[-1])
                    sync_acc = None
                else:
                    if bit_shift:
                        n = len(payloads)
                        _ext[0] = raw_carry
                        _ext[1 : n + 1] = payloads
                        np.left_shift(_ext[:n], bit_shift, out=_sft[:n])
                        np.right_shift(_ext[1 : n + 1], 8 - bit_shift, out=_rhs[:n])
                        np.bitwise_or(_sft[:n], _rhs[:n], out=_sft[:n])
                        buf.extend(_sft[:n])
                    else:
                        buf.extend(payloads)
                    raw_carry = int(payloads[-1])
                del payloads

                while len(buf) >= frame_size:
                    if buf[:pat_len] == bytearray(pattern):
                        frames.append(bytes(buf[:frame_size]))
                        del buf[:frame_size]
                    else:
                        nxt = bytes(buf).find(pattern)
                        if nxt == -1:
                            del buf[: len(buf) - pat_len]
                            break
                        del buf[:nxt]

            del raw

    desynced = had_carried_sync_acc and bit_shift is None
    if desynced:
        logger.warning(
            "read_frames_batch(%s): sync pattern not found even after carrying over "
            "%d byte(s) of unsynced data from a previous chunk; this may indicate the "
            "chunks were fed out of order or the data is corrupted, rather than a "
            "normal chunk boundary.",
            path,
            len(initial_sync_acc or b""),
        )

    return FrameReadResult(
        frames=frames,
        tail_buf=bytes(buf),
        bit_shift=bit_shift,
        raw_carry=raw_carry,
        sync_acc=bytes(sync_acc) if sync_acc is not None else None,
        desynced=desynced,
    )
