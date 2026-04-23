import pycadu.constants as c


def test_ccsds_sync_marker():
    assert c.CCSDS_SYNC_MARKER == bytes([0x1A, 0xCF, 0xFC, 0x1D])


def test_cadu_stride_896():
    assert c.CADU_STRIDE_896 == 0x380


def test_cadu_stride_1024():
    assert c.CADU_STRIDE_1024 == 0x400


def test_rs_parity_length():
    assert c.RS_PARITY_LENGTH == 128


def test_cadu_data_length():
    assert c.CADU_DATA_LENGTH == 896


def test_ccsds_header_length():
    assert c.CCSDS_HEADER_LENGTH == 14


def test_vcdu_payload_length():
    assert c.VCDU_PAYLOAD_LENGTH == 882
