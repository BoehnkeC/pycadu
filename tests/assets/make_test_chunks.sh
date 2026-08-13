#!/usr/bin/env bash
#
# Build two small, real-data .tm test chunks plus the reference file they
# concatenate to, from a genuine CADU telemetry file, for pycadu's
# chunk-boundary reassembly tests. Keeps two genuine sync-anchored real byte
# runs and drops the (irrelevant) megabytes of real content around and
# between them -- see --help for the full rationale and the TARGET FRAME
# LENGTH this produces.
#
# Usage:
#   ./make_test_chunks.sh <src.tm> [name] [options]
#   ./make_test_chunks.sh --help
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

print_help() {
  cat <<'HELP'
make_test_chunks.sh - build two small, real-data .tm test chunks, plus the
reference file they concatenate to, from a genuine CADU telemetry file, for
pycadu's chunk-boundary reassembly tests.

A full FY-3F MERSI-3 VCDU frame is 3,731,208 bytes (one scan) - far too
large to commit to source control, let alone three times over. read_frames_batch
never inspects frame *content*, only that a `frame_size`-byte slice starts
with the sync pattern, so this script keeps two genuine sync-anchored real
byte runs (frame 1's start, frame 2's start) and drops the (irrelevant)
megabytes of real content in between and around them. Every byte in the
output is a real, unmodified byte copied from the source file - nothing is
fabricated.

TARGET FRAME LENGTH:
  frame_size = FRAME_BLOCKS * 882 bytes  (882 = VCDU payload length)
  Default FRAME_BLOCKS=3 -> frame_size = 2,646 bytes.
  This is a deliberately SHRUNK stand-in "frame size" for these output
  files only - NOT the true ~3.7 MB production VCDU frame size. Whatever
  value you pass here is exactly the value the corresponding test must use
  as its frame_size argument to read_frames_batch/vcdu_frames.

The cut between chunk 1 and chunk 2 is placed so it lands inside frame 2's
(shrunk) content, not at a frame boundary: reading the two chunks
independently, with no carried state, loses that frame; carrying chunk 1's
leftover reassembly state into chunk 2's read recovers it, landing in
exactly the same result as reading the reference file (chunk 1 + chunk 2,
byte for byte) in one call. That's the exact property pycadu.reader's
initial_*/tail-state carry mechanism exists to guarantee, and what the
reference file is for: it's the "read the whole thing in one go" ground
truth to test chunk_1 + chunk_2 against.

USAGE:
  make_test_chunks.sh <src.tm> [name] [options]
  make_test_chunks.sh -h | --help

ARGUMENTS:
  src.tm  Real CADU telemetry file to sample two genuine frames from.
  name    Basename for the three output files (default: src's filename
          without its extension). Written next to this script, i.e. into
          tests/assets/, as <name>.tm (the reference), <name>_1.tm and
          <name>_2.tm (the two chunks; <name>_1.tm + <name>_2.tm is
          byte-for-byte <name>.tm).

OPTIONS:
  --vcid N          Target VCID to sample (default: 3, FY-3F day-side MERSI-3).
                    This is not the final chunk count.
  --frame-blocks N  Shrunk frame length in whole VCID payload blocks (see
                    TARGET FRAME LENGTH above). Default: 3 (2,646 bytes).
  --lead-margin N   Extra real VCID blocks of context kept before frame 1's
                    sync marker (default: 3).
  --scan-bytes N    How many bytes of src to scan looking for two real sync
                    occurrences (default: 41943040, i.e. 40 MiB). Increase
                    this if src's target VCID is sparse and the script can't
                    find a second sync marker within the default window. This is 
                    not the size of the output chunks, which are much smaller.
  --pattern-hex HEX VCDU sync pattern, as hex (default: aa55aa55aa55aa55aa55aa55,
                    the MERSI 0xAA/0x55 x6 pattern).
  --stride N        Physical CADU block size in bytes (default: 1024).
  -h, --help        Show this help text and exit.

EXIT STATUS:
  0  on success
  1  on bad arguments, a source file that doesn't exist, two real sync
     markers not found in the scanned window, or the constructed fixture
     not actually exercising the carry mechanism

EXAMPLES:
  make_test_chunks.sh /data/fy3f_pass.tm my_scene
  make_test_chunks.sh /data/fy3f_pass.tm my_scene --frame-blocks 20
HELP
}

VCID=3
FRAME_BLOCKS=3
LEAD_MARGIN=3
SCAN_BYTES=41943040
PATTERN_HEX="aa55aa55aa55aa55aa55aa55"
STRIDE=1024
POSITIONAL=()

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) print_help; exit 0 ;;
    --vcid) VCID="$2"; shift 2 ;;
    --frame-blocks) FRAME_BLOCKS="$2"; shift 2 ;;
    --lead-margin) LEAD_MARGIN="$2"; shift 2 ;;
    --scan-bytes) SCAN_BYTES="$2"; shift 2 ;;
    --pattern-hex) PATTERN_HEX="$2"; shift 2 ;;
    --stride) STRIDE="$2"; shift 2 ;;
    -*) echo "Unknown flag: $1" >&2; exit 1 ;;
    *) POSITIONAL+=("$1"); shift ;;
  esac
done

if [ "${#POSITIONAL[@]}" -lt 1 ]; then
  echo "Usage: $0 <src.tm> [name] [options]" >&2
  echo "Run '$0 --help' for details." >&2
  exit 1
fi

SRC="${POSITIONAL[0]}"
if [ ! -f "$SRC" ]; then
  echo "Not a file: $SRC" >&2
  exit 1
fi

if [ "${#POSITIONAL[@]}" -ge 2 ]; then
  NAME="${POSITIONAL[1]}"
else
  base="$(basename "$SRC")"
  NAME="${base%.*}"
fi

OUT_REF="$SCRIPT_DIR/${NAME}.tm"
OUT1="$SCRIPT_DIR/${NAME}_1.tm"
OUT2="$SCRIPT_DIR/${NAME}_2.tm"

PYTHON="$REPO_ROOT/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  PYTHON="python3"
fi

"$PYTHON" - "$SRC" "$OUT_REF" "$OUT1" "$OUT2" "$VCID" "$FRAME_BLOCKS" "$LEAD_MARGIN" \
    "$SCAN_BYTES" "$PATTERN_HEX" "$STRIDE" "$REPO_ROOT" <<'PYEOF'
import sys

SRC, OUT_REF, OUT1, OUT2, VCID, FRAME_BLOCKS, LEAD_MARGIN, SCAN_BYTES, PATTERN_HEX, STRIDE, REPO_ROOT = sys.argv[1:]
VCID = int(VCID)
FRAME_BLOCKS = int(FRAME_BLOCKS)
LEAD_MARGIN = int(LEAD_MARGIN)
SCAN_BYTES = int(SCAN_BYTES)
STRIDE = int(STRIDE)
PATTERN = bytes.fromhex(PATTERN_HEX)

try:
    import numpy as np
except ImportError:
    sys.path.insert(0, f"{REPO_ROOT}/src")
    import numpy as np

try:
    from pycadu.sync import find_sync_pattern
except ImportError:
    sys.path.insert(0, f"{REPO_ROOT}/src")
    from pycadu.sync import find_sync_pattern

from pycadu.reader import read_frames_batch

CCSDS_HEADER_LENGTH = 14
CADU_DATA_LENGTH = 896
PAYLOAD_LEN = CADU_DATA_LENGTH - CCSDS_HEADER_LENGTH  # 882

# True production VCDU frame size for FY-3F MERSI-3 (one full scan). Used
# ONLY to know how far to skip forward when searching for the *next* real
# sync marker -- NOT the length of the shrunk frames this script produces.
REAL_FRAME_SIZE = 3_731_208

FRAME_SIZE = FRAME_BLOCKS * PAYLOAD_LEN

with open(SRC, "rb") as fh:
    data = fh.read(SCAN_BYTES)

n_blocks = len(data) // STRIDE
if n_blocks == 0:
    sys.exit(f"ERROR: {SRC} is smaller than one CADU block ({STRIDE} bytes)")

arr = np.frombuffer(data[: n_blocks * STRIDE], dtype=np.uint8).reshape(n_blocks, STRIDE)
vcids = arr[:, 5] & 0x3F
vcid_block_idx = np.flatnonzero(vcids == VCID)
if len(vcid_block_idx) < 2 * FRAME_BLOCKS + LEAD_MARGIN:
    sys.exit(
        f"ERROR: only {len(vcid_block_idx)} blocks match VCID {VCID} in the first "
        f"{SCAN_BYTES} bytes of {SRC} -- not enough for two {FRAME_BLOCKS}-block "
        f"frames. Try a larger --scan-bytes or a different --vcid."
    )
payload = arr[vcid_block_idx, CCSDS_HEADER_LENGTH:CADU_DATA_LENGTH].reshape(-1)

byte_pos1, _shift1 = find_sync_pattern(bytes(payload), PATTERN)
if byte_pos1 == -1:
    sys.exit(
        f"ERROR: no VCDU sync marker found for VCID {VCID} in the first {SCAN_BYTES} "
        f"bytes of {SRC}. Try a larger --scan-bytes, a different --vcid, or check "
        f"--pattern-hex."
    )
byte_pos2, _shift2 = find_sync_pattern(bytes(payload), PATTERN, start=byte_pos1 + REAL_FRAME_SIZE - 50)
if byte_pos2 == -1:
    sys.exit(
        f"ERROR: found one VCDU sync marker for VCID {VCID}, but not a second one, "
        f"within the first {SCAN_BYTES} bytes of {SRC}. This script needs two real "
        f"frames to sample from -- try a larger --scan-bytes."
    )


def region(sync_payload_pos, lead_margin_blocks):
    sync_v_block = sync_payload_pos // PAYLOAD_LEN
    start_v_block = max(0, sync_v_block - lead_margin_blocks)
    start_phys_block = int(vcid_block_idx[start_v_block])
    end_v_block = sync_v_block
    accumulated = 0
    while accumulated < FRAME_SIZE + PAYLOAD_LEN and end_v_block < len(vcid_block_idx) - 1:
        accumulated += PAYLOAD_LEN
        end_v_block += 1
    end_phys_block = int(vcid_block_idx[end_v_block])
    return start_phys_block, end_phys_block


head_start, head_end = region(byte_pos1, LEAD_MARGIN)
tail_start, tail_end = region(byte_pos2, 0)

head_bytes = data[head_start * STRIDE : (head_end + 1) * STRIDE]
tail_bytes = data[tail_start * STRIDE : (tail_end + 1) * STRIDE]
tail_n_blocks = (tail_end - tail_start + 1)

# Cut in the middle of frame 2's (shrunk) real content, not at a frame
# boundary -- this is what makes the fixture actually exercise the carry
# mechanism instead of trivially reassembling on its own.
split_within_tail = tail_n_blocks // 2
if split_within_tail < 1 or split_within_tail >= tail_n_blocks:
    sys.exit("ERROR: frame 2's region is too short to split in half; increase --frame-blocks")

chunk1 = head_bytes + tail_bytes[: split_within_tail * STRIDE]
chunk2 = tail_bytes[split_within_tail * STRIDE :]

with open(OUT_REF, "wb") as f:
    f.write(chunk1 + chunk2)
with open(OUT1, "wb") as f:
    f.write(chunk1)
with open(OUT2, "wb") as f:
    f.write(chunk2)

# Self-verify: carrying chunk 1's leftover state into chunk 2's read must
# recover exactly what reading the reference file (chunk_1 + chunk_2, byte
# for byte) recovers, and a naive independent read of each chunk must lose
# at least one frame.
whole = read_frames_batch(OUT_REF, STRIDE, VCID, PATTERN, FRAME_SIZE)

result_a = read_frames_batch(OUT1, STRIDE, VCID, PATTERN, FRAME_SIZE)
naive_b = read_frames_batch(OUT2, STRIDE, VCID, PATTERN, FRAME_SIZE)
carried_b = read_frames_batch(
    OUT2,
    STRIDE,
    VCID,
    PATTERN,
    FRAME_SIZE,
    initial_buf=result_a.tail_buf,
    initial_bit_shift=result_a.bit_shift,
    initial_raw_carry=result_a.raw_carry,
    initial_sync_acc=result_a.sync_acc,
)

naive_total = len(result_a.frames) + len(naive_b.frames)
carried_total = len(result_a.frames) + len(carried_b.frames)
carried_matches_whole = result_a.frames + carried_b.frames == whole.frames

if not carried_matches_whole:
    sys.exit(
        f"ERROR: carrying chunk 1's state into chunk 2 did NOT reproduce {OUT_REF}'s "
        "result -- something is wrong with this fixture's construction, not just a "
        f"benign chunk boundary. Wrote {OUT_REF}, {OUT1}, {OUT2} anyway, but they are "
        "NOT a usable fixture -- delete them or rerun with different options."
    )
if naive_total >= len(whole.frames):
    sys.exit(
        "ERROR: the chosen cut point didn't actually land inside a frame -- the "
        "naive (no carry) read recovered everything already, so this fixture "
        f"wouldn't demonstrate anything. Wrote {OUT_REF}, {OUT1}, {OUT2} anyway, but "
        "they are NOT a usable fixture -- delete them or try different "
        "--frame-blocks/--lead-margin."
    )

print(f"Wrote {OUT_REF} ({len(chunk1) + len(chunk2)} bytes, the reference)")
print(f"Wrote {OUT1} ({len(chunk1)} bytes) and {OUT2} ({len(chunk2)} bytes)")
print(f"frame_size to use in tests: {FRAME_SIZE} bytes ({FRAME_BLOCKS} * {PAYLOAD_LEN}), vcid={VCID}")
print(f"reference frames:  {len(whole.frames)}")
print(f"naive (no carry) frames:  {naive_total}  <- loses data at the cut")
print(f"carried frames:           {carried_total}  <- matches the reference, as expected")
PYEOF

echo "Done."
