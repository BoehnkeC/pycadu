"""CADU stride detection and payload extraction."""

from pycadu.constants import (
    CADU_DATA_LENGTH,
    CADU_STRIDE_896,
    CADU_STRIDE_1024,
    CCSDS_HEADER_LENGTH,
    CCSDS_SYNC_MARKER,
)

CADU_STRIDE_1072 = 1072


def _count_markers_at_stride(data: bytes, stride: int) -> int:
    count = 0
    for i in range(len(data) // stride):
        offset = i * stride
        if data[offset : offset + 4] == CCSDS_SYNC_MARKER:
            count += 1
    return count


def detect_cadu_stride(data: bytes) -> int:
    candidates = (CADU_STRIDE_1072, CADU_STRIDE_1024, CADU_STRIDE_896)
    counts = {s: _count_markers_at_stride(data, s) for s in candidates}
    best_count = max(counts.values())
    for stride in (CADU_STRIDE_1024, CADU_STRIDE_1072, CADU_STRIDE_896):
        if counts[stride] == best_count:
            return stride
    return CADU_STRIDE_1024


def extract_cadu_data(block: bytes, physical_stride: int) -> bytes:
    return block[:CADU_DATA_LENGTH]


def extract_vcdu_payload(cadu: bytes) -> bytes:
    return cadu[CCSDS_HEADER_LENGTH:]


def extract_vcid(block: bytes) -> int:
    return block[5] & 0x3F
