# SPDX-FileCopyrightText: © 2026 Ben Payne
# SPDX-License-Identifier: Apache-2.0
"""Closed-loop IEC 60958 (S/PDIF, consumer) biphase-mark decoder.

Covers task T045 (specs/001-psg-sound-chip/tasks.md, User Story 4). This is
a *reference decoder*, written from the normative format description in
specs/001-psg-sound-chip/contracts/audio-output-formats.md and research.md
R7/data-model.md sec 4 -- not from src/spdif_out.v, which does not exist
yet (constitution Principle I: tests, including their support code, are
written from the spec).

Format recap (24.576 MHz nominal clock):
  - Biphase-mark line code. Half-cell rate = clk/4 = 6.144 MHz. Bit cell =
    2 half-cells (3.072 Mbit/s). 32 bits/subframe, 2 subframes/frame
    (A = left, B = right, identical audio), 192 frames/block.
  - The line level toggles at the START of every bit cell, and toggles
    again at the MIDDLE of the cell iff the bit is 1. Given two half-cell
    samples (hc0, hc1) for one cell: bit = 1 if hc0 != hc1, else bit = 0.
    This holds regardless of the absolute/previous line level, so no
    running "current level" needs to be tracked to decode ordinary bits.
  - Preambles (bits 0-3 of each subframe, 8 half-cells) deliberately
    violate that rule and are instead fixed, literal half-cell level
    patterns: B = 11101000 (subframe A, frame 0 of a block), M = 11100010
    (subframe A, other frames), W = 11100100 (subframe B). Per R7, even
    parity over bits 4-31 plus the even count of 1s in every preamble
    guarantees the line is always back at level 0 at each subframe
    boundary, so preambles can be matched as literal patterns without
    needing to track a running level either.
  - Bits 4-11: aux (4) + unused audio LSBs (4), always 0 in this design.
  - Bits 12-27: the 16-bit sample, LSB first.
  - Bit 28 = V (0 = valid), bit 29 = U (0), bit 30 = C (channel-status
    bit for this frame), bit 31 = P (even parity over bits 4-30, i.e. the
    number of 1 bits across 4-31 inclusive is even).
  - Channel status: the 192 per-frame C bits (subframe A's C bit; both
    subframes of a frame carry the same value) assemble, 8 per byte,
    LSB-first within each byte, into a 24-byte channel-status block.
    Consumer linear PCM at 48 kHz has only bit 2 (copy permitted) and bit
    25 (byte 3 bit 1 -> byte 3 == 0x02, sample-frequency field) set.
"""

from typing import List, Optional

from cocotb.triggers import Timer, with_timeout

HALF_CELLS_PER_BIT = 2
BITS_PER_SUBFRAME = 32
HALF_CELLS_PER_SUBFRAME = BITS_PER_SUBFRAME * HALF_CELLS_PER_BIT  # 64
SUBFRAMES_PER_FRAME = 2
HALF_CELLS_PER_FRAME = HALF_CELLS_PER_SUBFRAME * SUBFRAMES_PER_FRAME  # 128
FRAMES_PER_BLOCK = 192
HALF_CELLS_PER_BLOCK = HALF_CELLS_PER_FRAME * FRAMES_PER_BLOCK  # 24576

# Preambles, as literal half-cell level sequences (8 half-cells = 4 bit
# cells: bits 0-3), per contracts/audio-output-formats.md.
_PREAMBLE_PATTERNS = {
    "B": (1, 1, 1, 0, 1, 0, 0, 0),
    "M": (1, 1, 1, 0, 0, 0, 1, 0),
    "W": (1, 1, 1, 0, 0, 1, 0, 0),
}
_PREAMBLE_HALF_CELLS = 8

# Frames (0-indexed within a block) whose C bit is 1 -- research.md R7 /
# data-model.md sec 4: copy-permitted (frame 2) and Fs=48kHz (frame 25).
CHANNEL_STATUS_SET_FRAMES = (2, 25)

# Nominal half-cell period: 4 system clocks at 24.576 MHz.
DEFAULT_HALF_CELL_NS = 4 * 40690 / 1000.0  # 162.76 ns


def _match_preamble(half_cells) -> Optional[str]:
    for name, pattern in _PREAMBLE_PATTERNS.items():
        if tuple(half_cells) == pattern:
            return name
    return None


class SpdifDecoder:
    """Feed it raw half-cell line-level samples; read back subframes/blocks.

    This is a pull-based, incremental decoder: `feed_levels()` can be
    called multiple times (e.g. as a capture progresses) and internally
    self-synchronizes to the first recognizable preamble in the fed data,
    so a caller does not need to know in advance exactly which half-cell
    of the line corresponds to a subframe boundary.
    """

    def __init__(self, half_cell_ns: float = DEFAULT_HALF_CELL_NS):
        self.half_cell_ns = half_cell_ns
        self._half_cells: List[int] = []
        self._synced = False
        self._decoded_upto = 0  # index into _half_cells consumed so far
        self._subframes: List[dict] = []

    # -- ingest -----------------------------------------------------------

    def feed_levels(self, levels) -> None:
        """Append raw half-cell level samples (each 0 or 1) and decode."""
        self._half_cells.extend(int(lvl) & 1 for lvl in levels)
        self._decode_available()

    def _find_sync(self) -> Optional[int]:
        """Return the index of the first half-cell that starts a genuine
        subframe-*A* (frame start) boundary, or None if that can't be
        confirmed yet.

        A raw 8-half-cell preamble pattern can appear by chance inside
        ordinary (non-preamble) payload bits, so a bare pattern match
        isn't enough evidence on its own. A candidate must (a) look like
        a subframe-A preamble (B or M -- never W, so that decoding always
        proceeds in aligned (A, B) frame pairs from the first decoded
        subframe onward) and (b) have the half-cells exactly one, two, and
        three subframes (64 half-cells) later also look like a preamble,
        in the expected alternating role (B-subframe, A-subframe,
        B-subframe) -- a false positive essentially never survives that
        many independent, role-consistent 64-half-cell hops. If there
        isn't yet enough buffered data to check even one such hop, sync is
        deferred (returns None) rather than locking onto an unverified
        candidate.
        """
        n = len(self._half_cells)
        max_start = n - _PREAMBLE_HALF_CELLS
        # Role expected at 1/2/3 subframes after a subframe-A boundary:
        # next subframe is B (W), then A (B or M), then B (W) again.
        expected_roles = {1: ("W",), 2: ("B", "M"), 3: ("W",)}
        for start in range(0, max_start + 1):
            window = self._half_cells[start : start + _PREAMBLE_HALF_CELLS]
            if _match_preamble(window) not in ("B", "M"):
                continue
            checked = 0
            verified = 0
            for k in (1, 2, 3):
                offset = start + k * HALF_CELLS_PER_SUBFRAME
                if offset + _PREAMBLE_HALF_CELLS > n:
                    break
                checked += 1
                nxt = self._half_cells[offset : offset + _PREAMBLE_HALF_CELLS]
                if _match_preamble(nxt) in expected_roles[k]:
                    verified += 1
            if checked == 0:
                # Not enough data yet to confirm this (or any later)
                # candidate -- wait for more before accepting anything.
                return None
            if verified == checked:
                return start
            # Otherwise this was a coincidental match; keep scanning.
        return None

    def _decode_available(self) -> None:
        if not self._synced:
            sync_idx = self._find_sync()
            if sync_idx is None:
                return
            self._decoded_upto = sync_idx
            self._synced = True

        while len(self._half_cells) - self._decoded_upto >= HALF_CELLS_PER_SUBFRAME:
            chunk = self._half_cells[
                self._decoded_upto : self._decoded_upto + HALF_CELLS_PER_SUBFRAME
            ]
            self._decoded_upto += HALF_CELLS_PER_SUBFRAME
            self._subframes.append(self._decode_subframe(chunk))

    @staticmethod
    def _decode_subframe(half_cells) -> dict:
        preamble = _match_preamble(half_cells[0:_PREAMBLE_HALF_CELLS])

        # Bits 4..31: standard biphase-mark decode, one bit per 2 half-cells.
        bits = []
        for bit_idx in range(4, 32):
            hc_pos = bit_idx * HALF_CELLS_PER_BIT
            hc0 = half_cells[hc_pos]
            hc1 = half_cells[hc_pos + 1]
            bits.append(1 if hc0 != hc1 else 0)
        # bits[0] == b4 ... bits[27] == b31

        aux = 0
        for k in range(4):  # b4-b7
            aux |= bits[k] << k

        sample_bits = bits[8:24]  # b12..b27, LSB first
        sample = 0
        for k, bit in enumerate(sample_bits):
            sample |= bit << k
        if sample & 0x8000:  # sign-extend to a signed 16-bit value
            sample -= 0x10000

        v = bits[24]  # b28
        u = bits[25]  # b29
        c = bits[26]  # b30
        # p (bits[27] == b31) is itself part of the parity population --
        # "even parity over bits 4-31" means the 28 bits 4..31 inclusive
        # contain an even number of 1s.
        parity_ok = (sum(bits) % 2) == 0

        return {
            "preamble": preamble,
            "sample": sample,
            "v": v,
            "u": u,
            "c": c,
            "parity_ok": parity_ok,
            "aux": aux,
        }

    # -- query --------------------------------------------------------------

    def subframes(self) -> List[dict]:
        """All subframes decoded so far, in order."""
        return list(self._subframes)

    def blocks(self) -> List[dict]:
        """Complete 192-frame blocks decoded so far.

        A block starts at a frame whose subframe A preamble is 'B'. Only
        fully-decoded 192-frame blocks are returned; a trailing partial
        block (not enough subframes fed yet) is omitted.
        """
        subframes = self._subframes
        frames = []
        i = 0
        while i + 1 < len(subframes):
            a, b = subframes[i], subframes[i + 1]
            frames.append((a, b))
            i += 2

        block_starts = [
            idx for idx, (a, _b) in enumerate(frames) if a["preamble"] == "B"
        ]

        blocks = []
        for n, start in enumerate(block_starts):
            end = (
                block_starts[n + 1]
                if n + 1 < len(block_starts)
                else len(frames)
            )
            block_frames = frames[start:end]
            if len(block_frames) < FRAMES_PER_BLOCK:
                continue  # incomplete trailing block -- not fed yet
            block_frames = block_frames[:FRAMES_PER_BLOCK]

            cs_bits = [a["c"] for a, _b in block_frames]
            cs_bytes = self._pack_bits_to_bytes(cs_bits)
            blocks.append(
                {
                    "channel_status": cs_bytes,
                    "frames": len(block_frames),
                    "sample_hz": self._sample_rate_from_channel_status(cs_bytes),
                }
            )
        return blocks

    @staticmethod
    def _pack_bits_to_bytes(bits) -> bytes:
        out = bytearray((len(bits) + 7) // 8)
        for i, bit in enumerate(bits):
            if bit:
                out[i // 8] |= 1 << (i % 8)
        return bytes(out)

    @staticmethod
    def _sample_rate_from_channel_status(cs_bytes: bytes) -> Optional[int]:
        if len(cs_bytes) < 4:
            return None
        byte3 = cs_bytes[3]
        freq_field = byte3 & 0x0F  # channel-status bits 24-27
        # Only the 48 kHz consumer-PCM code this chip emits is recognized
        # (data-model.md sec 4 / research.md R7: byte 3 == 0x02).
        return {0x02: 48000}.get(freq_field)

    def decode_sample_rate(self) -> Optional[int]:
        """Sample rate reported by the most recently completed block's
        channel status, or None if no full block has been decoded yet or
        the code isn't recognized."""
        blocks = self.blocks()
        if not blocks:
            return None
        return blocks[-1]["sample_hz"]


async def capture_half_cells(
    signal, n_half_cells: int, half_cell_ns: float, timeout_ns: Optional[float] = None
) -> List[int]:
    """Sample `signal` at the middle of each of `n_half_cells` half-cells.

    Samples the very first half-cell at `half_cell_ns / 2` from the call,
    then every `half_cell_ns` thereafter, so each sample lands mid-cell
    regardless of where in a cell `signal` happens to be when this is
    called.

    Raises `cocotb.result.SimTimeoutError` (via `with_timeout`) if the
    capture doesn't complete within `timeout_ns` -- a hard bound so a
    pin that never toggles (e.g. the current all-zero placeholder, or a
    chip built with ENABLE_SPDIF=0) fails the test instead of hanging the
    simulation forever.
    """
    if timeout_ns is None:
        # Generous margin: 4x the nominal capture time plus a fixed pad.
        timeout_ns = half_cell_ns * n_half_cells * 4 + 10_000.0

    async def _do_capture() -> List[int]:
        levels = []
        await Timer(half_cell_ns / 2.0, unit="ns")
        levels.append(int(signal.value) & 1)
        for _ in range(n_half_cells - 1):
            await Timer(half_cell_ns, unit="ns")
            levels.append(int(signal.value) & 1)
        return levels

    return await with_timeout(_do_capture(), timeout_ns, "ns")
