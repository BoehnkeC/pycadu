from pycadu.cadu import (
    CADU_STRIDE_1072,
    detect_cadu_stride,
    extract_cadu_data,
    extract_vcdu_payload,
    extract_vcid,
)
from pycadu.constants import (
    CADU_DATA_LENGTH,
    CADU_STRIDE_896,
    CADU_STRIDE_1024,
    CCSDS_SYNC_MARKER,
    RS_PARITY_LENGTH,
    VCDU_PAYLOAD_LENGTH,
)


def _frame_with_markers_at_stride(stride: int, count: int, total_bytes: int) -> bytes:
    data = bytearray(total_bytes)
    for i in range(count):
        offset = i * stride
        if offset + 4 <= total_bytes:
            data[offset : offset + 4] = CCSDS_SYNC_MARKER
    return bytes(data)


def test_detects_stride_896_when_markers_at_896():
    data = _frame_with_markers_at_stride(CADU_STRIDE_896, 10, CADU_STRIDE_896 * 12)
    assert detect_cadu_stride(data) == CADU_STRIDE_896


def test_detects_stride_1024_when_markers_at_1024():
    data = _frame_with_markers_at_stride(CADU_STRIDE_1024, 10, CADU_STRIDE_1024 * 12)
    assert detect_cadu_stride(data) == CADU_STRIDE_1024


def test_defaults_to_stride_1024_on_tie():
    data = bytes(CADU_STRIDE_1024 * 12)
    assert detect_cadu_stride(data) == CADU_STRIDE_1024


def test_detects_stride_1072_when_markers_at_1072():
    data = _frame_with_markers_at_stride(CADU_STRIDE_1072, 10, CADU_STRIDE_1072 * 12)
    assert detect_cadu_stride(data) == CADU_STRIDE_1072


def test_extract_cadu_data_strips_rs_parity_from_1024_block():
    block = b"\xab" * CADU_DATA_LENGTH + b"\xff" * RS_PARITY_LENGTH
    assert extract_cadu_data(block, CADU_STRIDE_1024) == b"\xab" * CADU_DATA_LENGTH


def test_extract_cadu_data_returns_full_block_for_896_stride():
    block = b"\xcd" * CADU_STRIDE_896
    assert extract_cadu_data(block, CADU_STRIDE_896) == block


def test_extract_cadu_data_strips_annotation_from_1072_block():
    cadu_part = b"\xab" * CADU_DATA_LENGTH + b"\xff" * RS_PARITY_LENGTH
    annotation = b"\x25\x67" + b"\x00" * 46
    block = cadu_part + annotation
    assert extract_cadu_data(block, CADU_STRIDE_1072) == b"\xab" * CADU_DATA_LENGTH


def test_extract_vcdu_payload_strips_ccsds_header():
    header = b"\x1a\xcf\xfc\x1d" + b"\x00" * 10
    payload = b"\xab" * VCDU_PAYLOAD_LENGTH
    cadu = header + payload
    assert extract_vcdu_payload(cadu) == payload


def test_extract_vcid_returns_vcid_from_cadu():
    raw_block = bytes([0x1A, 0xCF, 0xFC, 0x1D, 0x4D, 0x03]) + bytes(890)
    assert extract_vcid(raw_block) == 3
