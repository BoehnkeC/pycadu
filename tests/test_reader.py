"""Tests for pycadu.reader.

The central property under test is that VCDU frame reassembly must not lose
data at the edges of whatever byte range it happens to be given: neither the
bytes leading up to the first sync marker nor a trailing partial frame at the
end. That property matters because a satellite pass split into several `.tm`
chunk files can be cut at an arbitrary byte offset, and the one VCDU frame
straddling each cut point must survive the split intact — that's what the
`initial_*`/tail-state carry mechanism on `read_frames_batch` and
`vcdu_frames` exists for.
"""

import math
from pathlib import Path

import numpy as np
import pytest

from pycadu.constants import (
    CADU_STRIDE_1024,
    CCSDS_HEADER_LENGTH,
    CCSDS_SYNC_MARKER,
    RS_PARITY_LENGTH,
    VCDU_PAYLOAD_LENGTH,
)
from pycadu.reader import cadu_blocks, read_frames_batch, vcdu_frames, vcdu_payloads

PATTERN = bytes([0xAA, 0x55] * 6)  # stand-in for any instrument sync pattern
FRAME_SIZE = 0x38E4F4

# ---------------------------------------------------------------------------
# Synthetic data builders
# ---------------------------------------------------------------------------


def _n_frames(n: int) -> bytes:
    """*n* concatenated frames, each FRAME_SIZE bytes and starting with PATTERN."""
    frame = bytearray(FRAME_SIZE)
    frame[: len(PATTERN)] = PATTERN
    return bytes(frame) * n


def _right_shift_bits(data: bytes, shift: int) -> bytes:
    """Simulate *data* arriving `shift` bits (0-7) off byte alignment on the wire."""
    if shift == 0:
        return data
    arr = np.frombuffer(data, dtype=np.uint8).astype(np.uint16)
    prev = np.empty_like(arr)
    prev[0] = 0
    prev[1:] = arr[:-1]
    shifted = ((prev << (8 - shift)) | (arr >> shift)) & 0xFF
    return shifted.astype(np.uint8).tobytes()


def _n_frames_bit_shifted(n: int, shift: int) -> bytes:
    """*n* frames as `_n_frames` would build, but bit-shifted as if received off-alignment."""
    raw = _n_frames(n) + bytes(2)
    return _right_shift_bits(raw, shift)[:-1]


def _wrap_frames_in_cadu_blocks(frame_data: bytes, vcid: int = 3) -> bytes:
    """Slice arbitrary payload bytes into VCDU payloads and wrap each in a CADU
    physical block (sync marker + VCID header + RS parity), as real telemetry
    would carry it."""
    n_blocks = math.ceil(len(frame_data) / VCDU_PAYLOAD_LENGTH)
    out = bytearray()
    for i in range(n_blocks):
        payload = frame_data[i * VCDU_PAYLOAD_LENGTH : (i + 1) * VCDU_PAYLOAD_LENGTH]
        payload = payload.ljust(VCDU_PAYLOAD_LENGTH, b"\x00")
        header = CCSDS_SYNC_MARKER + bytes([0x4D, vcid & 0x3F]) + b"\x00" * (CCSDS_HEADER_LENGTH - 6)
        out += header + payload + b"\x00" * RS_PARITY_LENGTH
    return bytes(out)


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


# ---------------------------------------------------------------------------
# Physical block / payload extraction
# ---------------------------------------------------------------------------


def test_cadu_blocks_yields_one_block_per_stride(tmp_path):
    n = 5
    dat = _write(tmp_path, "test.dat", b"\x00" * (CADU_STRIDE_1024 * n))
    blocks = list(cadu_blocks(dat, CADU_STRIDE_1024))
    assert len(blocks) == n
    assert all(len(b) == CADU_STRIDE_1024 for b in blocks)


def test_vcdu_payloads_yields_only_matching_vcid():
    vcid_3_block = (
        CCSDS_SYNC_MARKER
        + bytes([0x4D, 3])
        + b"\x00" * (CCSDS_HEADER_LENGTH - 6)
        + b"\xab" * VCDU_PAYLOAD_LENGTH
        + b"\x00" * RS_PARITY_LENGTH
    )
    vcid_6_block = (
        CCSDS_SYNC_MARKER
        + bytes([0x4D, 6])
        + b"\x00" * (CCSDS_HEADER_LENGTH - 6)
        + b"\xcd" * VCDU_PAYLOAD_LENGTH
        + b"\x00" * RS_PARITY_LENGTH
    )
    blocks = iter([vcid_3_block, vcid_6_block, vcid_3_block])
    payloads = list(vcdu_payloads(blocks, CADU_STRIDE_1024, target_vcid=3))
    assert len(payloads) == 2
    assert all(p == b"\xab" * VCDU_PAYLOAD_LENGTH for p in payloads)


# ---------------------------------------------------------------------------
# Basic reassembly: a single call over one uninterrupted stream
# ---------------------------------------------------------------------------


def test_vcdu_frames_yields_complete_frames_from_payload_stream():
    data = _n_frames(2)
    chunk = VCDU_PAYLOAD_LENGTH
    payloads = (data[i : i + chunk] for i in range(0, len(data), chunk))
    result = list(vcdu_frames(payloads, PATTERN, FRAME_SIZE))
    assert len(result) == 2
    assert all(len(f) == FRAME_SIZE for f in result)


@pytest.mark.parametrize("shift", [1, 3, 7])
def test_vcdu_frames_handles_bit_shifted_payload_stream(shift):
    data = _n_frames_bit_shifted(1, shift)
    payloads = (data[i : i + VCDU_PAYLOAD_LENGTH] for i in range(0, len(data), VCDU_PAYLOAD_LENGTH))
    result = list(vcdu_frames(payloads, PATTERN, FRAME_SIZE))
    assert len(result) == 1
    assert result[0][: len(PATTERN)] == PATTERN


def test_read_frames_batch_matches_streaming(tmp_path):
    file_data = _wrap_frames_in_cadu_blocks(_n_frames(2), vcid=3) * 3
    dat = _write(tmp_path, "test.dat", file_data)
    expected = list(
        vcdu_frames(
            vcdu_payloads(cadu_blocks(dat, CADU_STRIDE_1024), CADU_STRIDE_1024, 3),
            PATTERN,
            FRAME_SIZE,
        )
    )
    actual = read_frames_batch(dat, CADU_STRIDE_1024, 3, PATTERN, FRAME_SIZE)
    assert actual.frames == expected
    assert actual.bit_shift == 0


# ---------------------------------------------------------------------------
# Chunk-boundary correctness — the whole point of this module's design.
#
# A `.tm` file can be split into chunks at an arbitrary byte offset that
# essentially never lands on a frame boundary. Reading each chunk in
# isolation would lose the one frame straddling the cut: its head is dropped
# from chunk A as an "incomplete trailing frame", and its tail is dropped
# from chunk B as "garbage before the first sync". Carrying leftover state
# from chunk A's read into chunk B's read must eliminate that loss, no
# matter where the cut falls or whether the sync pattern is byte-aligned.
# ---------------------------------------------------------------------------


def test_read_frames_batch_drops_a_frame_without_carried_state(tmp_path):
    """Regression guard for the originally reported bug: splitting a stream and
    reading each half independently, with no carried state, loses the frame
    straddling the cut."""
    file_data = _wrap_frames_in_cadu_blocks(_n_frames(4), vcid=3)
    stride = CADU_STRIDE_1024
    n_blocks = len(file_data) // stride
    split = (n_blocks // 2) * stride

    whole = _write(tmp_path, "whole.dat", file_data)
    expected = read_frames_batch(whole, stride, 3, PATTERN, FRAME_SIZE).frames

    chunk_a = _write(tmp_path, "a.dat", file_data[:split])
    chunk_b = _write(tmp_path, "b.dat", file_data[split:])
    naive_a = read_frames_batch(chunk_a, stride, 3, PATTERN, FRAME_SIZE)
    naive_b = read_frames_batch(chunk_b, stride, 3, PATTERN, FRAME_SIZE)

    assert naive_a.frames + naive_b.frames != expected
    assert len(naive_a.frames) + len(naive_b.frames) < len(expected)


@pytest.mark.parametrize("split_fraction", [0.02, 0.25, 0.5, 0.75, 0.98])
def test_read_frames_batch_seamlessly_rejoins_split_chunks(split_fraction, tmp_path):
    """However the stream is cut into two chunks, reading chunk A then chunk B
    while carrying chunk A's leftover state must reproduce exactly the frames
    obtained by reading the whole stream in one call."""
    file_data = _wrap_frames_in_cadu_blocks(_n_frames(4), vcid=3)
    stride = CADU_STRIDE_1024
    n_blocks = len(file_data) // stride
    split_block = max(1, min(n_blocks - 1, round(n_blocks * split_fraction)))
    split = split_block * stride

    whole = _write(tmp_path, "whole.dat", file_data)
    expected = read_frames_batch(whole, stride, 3, PATTERN, FRAME_SIZE).frames

    chunk_a = _write(tmp_path, "a.dat", file_data[:split])
    chunk_b = _write(tmp_path, "b.dat", file_data[split:])
    result_a = read_frames_batch(chunk_a, stride, 3, PATTERN, FRAME_SIZE)
    result_b = read_frames_batch(
        chunk_b,
        stride,
        3,
        PATTERN,
        FRAME_SIZE,
        initial_buf=result_a.tail_buf,
        initial_bit_shift=result_a.bit_shift,
        initial_raw_carry=result_a.raw_carry,
        initial_sync_acc=result_a.sync_acc,
    )

    assert result_a.frames + result_b.frames == expected
    assert not result_b.desynced


def test_read_frames_batch_seamlessly_rejoins_bit_shifted_split_chunks(tmp_path):
    """Same property as above, but with the sync pattern arriving off byte
    alignment — matching the bit_shift=4 case actually observed against real
    FY-3F CADU chunk files."""
    file_data = _wrap_frames_in_cadu_blocks(_n_frames_bit_shifted(4, shift=4), vcid=3)
    stride = CADU_STRIDE_1024
    n_blocks = len(file_data) // stride
    split = (n_blocks // 2) * stride

    whole = _write(tmp_path, "whole.dat", file_data)
    expected = read_frames_batch(whole, stride, 3, PATTERN, FRAME_SIZE).frames
    assert len(expected) == 4  # sanity: the setup actually produced 4 frames

    chunk_a = _write(tmp_path, "a.dat", file_data[:split])
    chunk_b = _write(tmp_path, "b.dat", file_data[split:])
    result_a = read_frames_batch(chunk_a, stride, 3, PATTERN, FRAME_SIZE)
    assert result_a.bit_shift == 4
    result_b = read_frames_batch(
        chunk_b,
        stride,
        3,
        PATTERN,
        FRAME_SIZE,
        initial_buf=result_a.tail_buf,
        initial_bit_shift=result_a.bit_shift,
        initial_raw_carry=result_a.raw_carry,
        initial_sync_acc=result_a.sync_acc,
    )

    assert result_a.frames + result_b.frames == expected


def test_read_frames_batch_flags_desync_when_carried_sync_never_found(tmp_path):
    """If a chunk is told to resume an unsynced search (`initial_sync_acc`) but
    never finds the sync pattern, that's a real signal of corrupt or
    out-of-order chunks — not a normal chunk boundary — and must be flagged."""
    # A payload that never contains PATTERN at any bit shift: all-0xFF can't
    # line up with the alternating 0xAA/0x55 pattern no matter how it's shifted.
    garbage = _wrap_frames_in_cadu_blocks(b"\xff" * VCDU_PAYLOAD_LENGTH, vcid=3)
    dat = _write(tmp_path, "garbage.dat", garbage)

    result = read_frames_batch(
        dat,
        CADU_STRIDE_1024,
        3,
        PATTERN,
        FRAME_SIZE,
        initial_sync_acc=b"\xff" * 64,
    )

    assert result.frames == []
    assert result.bit_shift is None
    assert result.desynced


def test_vcdu_frames_seamlessly_rejoins_split_chunks():
    """The streaming generator carries the same guarantee as the batch reader:
    feeding chunk A's returned `FrameTailState` into chunk B's call as
    `initial_*` reproduces the same frames as one uninterrupted stream."""
    data = _n_frames(3)
    chunk = VCDU_PAYLOAD_LENGTH
    all_payloads = [data[i : i + chunk] for i in range(0, len(data), chunk)]
    split = len(all_payloads) // 2

    def _drain(gen):
        frames = []
        try:
            while True:
                frames.append(next(gen))
        except StopIteration as stop:
            return frames, stop.value

    whole_gen = vcdu_frames(iter(all_payloads), PATTERN, FRAME_SIZE)
    expected, _ = _drain(whole_gen)

    gen_a = vcdu_frames(iter(all_payloads[:split]), PATTERN, FRAME_SIZE)
    frames_a, tail_a = _drain(gen_a)

    gen_b = vcdu_frames(
        iter(all_payloads[split:]),
        PATTERN,
        FRAME_SIZE,
        initial_raw_buf=tail_a.raw_buf or b"",
        initial_buf=tail_a.buf,
        initial_bit_shift=tail_a.bit_shift,
        initial_carry=tail_a.carry,
    )
    frames_b, tail_b = _drain(gen_b)

    assert frames_a + frames_b == expected
    assert not tail_b.desynced


# ---------------------------------------------------------------------------
# Real production data: two small real FY-3F .tm chunk files (tests/assets),
# checked against the undivided reference file they were cut from. Built by
# make_test_chunks.sh (invoked as `./make_test_chunks.sh <src> --frame-blocks
# 3 --lead-margin 3`), which keeps two genuine sync-anchored real byte runs —
# real bytes, but a deliberately shrunk stand-in frame size, not the true
# ~3.7 MB production VCDU frame (see FY3F_FRAME_SIZE above and the script's
# --help) — so the whole fixture set is only ~80 KB. The reference contains
# 2 real VCDU frames: 1 complete, plus a second one whose start is cut
# between chunk 1 and chunk 2. Read independently with no carried state, that
# second frame is lost entirely (only the first survives); carrying chunk
# 1's leftover state into chunk 2's read recovers both — the exact defect
# this module exists to fix, reproduced against bytes that actually came off
# the satellite instead of synthetic data.
# ---------------------------------------------------------------------------

ASSETS_DIR = Path(__file__).parent / "assets"
# NOT the real ~3.7 MB FY-3F MERSI-3 VCDU frame size. make_test_chunks.sh built
# these fixtures with its default --frame-blocks 3, i.e. a deliberately shrunk
# stand-in frame size of 3 * VCDU_PAYLOAD_LENGTH bytes -- see that script's
# --help for the rationale. Using the real frame size here finds 0 frames in
# these ~80 KB fixtures.
FY3F_FRAME_SIZE = 3 * VCDU_PAYLOAD_LENGTH
FY3F_VCID = 3  # day-side MERSI-3 virtual channel

REFERENCE_TM = ASSETS_DIR / "3e2e262f0d96b61f16a60ca9f93d9333.tm"
CHUNK_1_TM = ASSETS_DIR / "3e2e262f0d96b61f16a60ca9f93d9333_1.tm"
CHUNK_2_TM = ASSETS_DIR / "3e2e262f0d96b61f16a60ca9f93d9333_2.tm"

_real_assets_present = REFERENCE_TM.exists() and CHUNK_1_TM.exists() and CHUNK_2_TM.exists()
requires_real_assets = pytest.mark.skipif(
    not _real_assets_present, reason="real FY-3F sample fixtures not present in tests/assets"
)


@requires_real_assets
def test_real_chunk_assets_concatenate_to_the_reference():
    """Sanity check on the fixtures themselves: chunk_1 + chunk_2 must be
    byte-for-byte the reference file, or the tests below aren't testing what
    they claim to."""
    assert CHUNK_1_TM.read_bytes() + CHUNK_2_TM.read_bytes() == REFERENCE_TM.read_bytes()


@requires_real_assets
def test_real_chunks_drop_a_frame_without_carried_state():
    """Regression guard on real data: reading the two real chunks
    independently, with no carried state, loses the VCDU frame straddling
    the cut — the originally reported bug, reproduced against real bytes
    instead of synthetic data."""
    expected = read_frames_batch(REFERENCE_TM, CADU_STRIDE_1024, FY3F_VCID, PATTERN, FY3F_FRAME_SIZE)
    naive_a = read_frames_batch(CHUNK_1_TM, CADU_STRIDE_1024, FY3F_VCID, PATTERN, FY3F_FRAME_SIZE)
    naive_b = read_frames_batch(CHUNK_2_TM, CADU_STRIDE_1024, FY3F_VCID, PATTERN, FY3F_FRAME_SIZE)

    assert len(expected.frames) == 2
    assert naive_a.frames + naive_b.frames != expected.frames
    assert len(naive_a.frames) + len(naive_b.frames) == len(expected.frames) - 1


@requires_real_assets
def test_real_chunks_seamlessly_rejoin_into_the_reference():
    """Carrying chunk 1's leftover reassembly state into chunk 2's read
    recovers the frame the naive read above loses, landing in exactly the
    same result as reading the undivided reference file in one call.

    `desynced` is deliberately not compared here: it flags "carried-over
    unsynced data still wasn't resolved by this call", which only makes
    sense to ask of the carried (chunked) read — the whole-reference read
    never carries anything in, so it can never be desynced, and that's not
    a discrepancy.
    """
    expected = read_frames_batch(REFERENCE_TM, CADU_STRIDE_1024, FY3F_VCID, PATTERN, FY3F_FRAME_SIZE)

    result_a = read_frames_batch(CHUNK_1_TM, CADU_STRIDE_1024, FY3F_VCID, PATTERN, FY3F_FRAME_SIZE)
    result_b = read_frames_batch(
        CHUNK_2_TM,
        CADU_STRIDE_1024,
        FY3F_VCID,
        PATTERN,
        FY3F_FRAME_SIZE,
        initial_buf=result_a.tail_buf,
        initial_bit_shift=result_a.bit_shift,
        initial_raw_carry=result_a.raw_carry,
        initial_sync_acc=result_a.sync_acc,
    )

    assert result_a.frames + result_b.frames == expected.frames
    assert len(result_a.frames + result_b.frames) == 2
    assert result_b.tail_buf == expected.tail_buf
    assert result_b.bit_shift == expected.bit_shift
    assert result_b.raw_carry == expected.raw_carry
    assert result_b.sync_acc == expected.sync_acc
