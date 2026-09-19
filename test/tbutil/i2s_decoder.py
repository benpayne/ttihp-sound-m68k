# SPDX-FileCopyrightText: © 2026 Ben Payne
# SPDX-License-Identifier: Apache-2.0
"""I2S output decoder for the 68k PSG sound chip testbench (T040).

Decodes the chip's Philips I2S output (BCLK/LRCLK/SDATA) into per-frame
samples by awaiting edges of BCLK/LRCLK themselves -- never by sampling on
every system `clk` edge. `clk` runs 8x faster than BCLK (research.md R6:
BCLK = count[2]), so polling every `clk` edge would multiply the number of
awaits roughly 8x for zero benefit; research.md R12 sizes the runtime
budget for 1,000 I2S frames as ~128k edge-awaits (a few minutes), which
already assumes edge-driven decoding.

Format decoded (contracts/audio-output-formats.md, research.md R6):
  - The chip is the I2S clock master. BCLK = 3.072 MHz (64 per LRCLK
    frame). LRCLK = 48 kHz, low = left, high = right.
  - SDATA and LRCLK both change on the BCLK *falling* edge; a receiver
    samples SDATA on the BCLK *rising* edge.
  - Each 32-BCLK slot carries: one bit of one-BCLK delay (always 0),
    then a 16-bit two's-complement sample MSB-first, then 15 bits of
    zero padding.

Every public coroutine here takes a hard `timeout_ns` on each edge it
waits for. The all-zero placeholder in src/project.v never toggles BCLK/
LRCLK/SDATA at all, so without a timeout a test against it would hang the
simulator forever instead of failing cleanly.
"""

import cocotb
from cocotb.triggers import FallingEdge, RisingEdge, with_timeout
from cocotb.utils import get_sim_time

# Per contracts/audio-output-formats.md and research.md R6:
BITS_PER_SLOT = 32  # BCLKs per channel slot (left or right)
BCLK_PER_FRAME = 2 * BITS_PER_SLOT  # 64: one full LRCLK period
SAMPLE_BITS = 16
MSB_DELAY_BCLKS = 1  # the sample's MSB starts one BCLK after the LRCLK edge

DEFAULT_TIMEOUT_NS = 50_000  # >>150x the nominal ~326 ns BCLK period


def _twos_complement(unsigned_value, width=SAMPLE_BITS):
    """Reinterpret an unsigned `width`-bit integer as signed two's complement."""
    if unsigned_value & (1 << (width - 1)):
        unsigned_value -= 1 << width
    return unsigned_value


def _extract_sample(slot_bits):
    """Reconstruct a signed 16-bit sample from one slot's raw sampled bits.

    `slot_bits` is the ordered list of bits sampled on each BCLK rising
    edge since the slot's LRCLK boundary: index 0 is the one-BCLK delay
    bit (must be 0, FR-032), indices 1..16 are the sample MSB..LSB, and
    anything beyond that is zero padding out to the 32-bit slot. Returns
    None if fewer than 17 bits were captured (malformed framing -- the
    caller's bclk_count / msb_delay_ok fields will already flag that).
    """
    if len(slot_bits) < 1 + SAMPLE_BITS:
        return None
    raw = 0
    for bit in slot_bits[1 : 1 + SAMPLE_BITS]:
        raw = (raw << 1) | (bit & 1)
    return _twos_complement(raw)


async def _timed(trigger, timeout_ns):
    return await with_timeout(trigger, timeout_ns, "ns")


async def decode_i2s_frames(bclk, lrclk, sdata, n_frames, timeout_ns=DEFAULT_TIMEOUT_NS):
    """Decode `n_frames` consecutive I2S frames from live signals.

    Returns a list of `n_frames` dicts:
        {'left': int, 'right': int, 'bclk_count': int, 'msb_delay_ok': bool}

    - 'left'/'right': signed 16-bit sample values for that frame.
    - 'bclk_count': total BCLK rising edges observed for the whole frame
      (left slot + right slot). Should be 64 (FR-032); reported rather
      than assumed so a framing bug shows up directly instead of silently
      misaligning bits.
    - 'msb_delay_ok': True iff the very first bit sampled after each
      LRCLK boundary (both slots) was 0, as required for the one-BCLK
      delay before the MSB (FR-032). This is checked, not assumed: a
      decoder that just skipped one bit unconditionally could not tell a
      correct delay from an accidentally-zero MSB.

    Bit sampling: SDATA and LRCLK change on the BCLK falling edge, so a
    reading taken exactly on the BCLK rising edge (`int(sdata.value)`
    alongside `int(lrclk.value)`) is always stable ("edges" above,
    research.md R6 "Timing"), matching what a real I2S receiver does.

    Raises `cocotb.triggers.SimTimeoutError` if BCLK/LRCLK do not toggle
    within `timeout_ns` of when they're expected to -- this is what turns
    the placeholder's permanently-idle pins into a fast, clear failure
    instead of a hang.
    """

    async def _next_bit():
        await _timed(RisingEdge(bclk), timeout_ns)
        return int(lrclk.value), int(sdata.value)

    # Synchronize to the start of a left slot (LRCLK low = left).
    await _timed(FallingEdge(lrclk), timeout_ns)
    cur_lr, cur_bit = await _next_bit()

    frames = []
    for _ in range(n_frames):
        slot_bits = {0: [], 1: []}
        bclk_count = 0
        slot = cur_lr  # expected 0 (left) at a synced frame boundary
        while True:
            slot_bits[slot].append(cur_bit)
            bclk_count += 1
            cur_lr, cur_bit = await _next_bit()
            if cur_lr != slot:
                if slot == 1:
                    # Back to left: this frame is complete. cur_lr/cur_bit
                    # are the first sample of the NEXT frame's left slot --
                    # leave them primed for the next loop iteration rather
                    # than consuming them here.
                    break
                slot = cur_lr  # left -> right, same frame continues

        left = _extract_sample(slot_bits[0])
        right = _extract_sample(slot_bits[1])
        msb_delay_ok = (
            len(slot_bits[0]) >= 1
            and slot_bits[0][0] == 0
            and len(slot_bits[1]) >= 1
            and slot_bits[1][0] == 0
        )
        frames.append(
            {
                "left": left,
                "right": right,
                "bclk_count": bclk_count,
                "msb_delay_ok": msb_delay_ok,
            }
        )

    return frames


async def check_i2s_framing(bclk, lrclk, n_frames, timeout_ns=DEFAULT_TIMEOUT_NS):
    """Structural framing check, independent of sample decoding.

    Measures, over `n_frames` consecutive LRCLK periods:
        {'bclk_per_frame': int, 'lrclk_hz': float, 'duty': float}

    - 'bclk_per_frame': average BCLK rising edges per full LRCLK period
      (left + right slots), rounded to the nearest integer. Should be 64.
    - 'lrclk_hz': LRCLK frequency measured from simulation time between
      corresponding left-slot-start edges. Should be ~48 kHz.
    - 'duty': fraction of each LRCLK period spent low (left slot).
      Should be ~0.5.

    Uses the same "prime one edge, defer its use" pattern as
    `decode_i2s_frames` so an edge that closes one frame and opens the
    next is never double-counted or dropped.
    """

    async def _next_edge():
        await _timed(RisingEdge(bclk), timeout_ns)
        return int(lrclk.value), get_sim_time(unit="ns")

    await _timed(FallingEdge(lrclk), timeout_ns)
    cur_lr, cur_ts = await _next_edge()

    bclk_counts = []
    low_ns_list = []
    period_ns_list = []
    frame_start_ns = cur_ts
    for _ in range(n_frames):
        slot = cur_lr  # expected 0 (left)
        count = 1  # the primed edge belongs to this frame's left slot
        high_start_ns = None
        while True:
            cur_lr, cur_ts = await _next_edge()
            if cur_lr != slot:
                if slot == 0:
                    high_start_ns = cur_ts
                    slot = 1
                    count += 1
                    continue
                break
            count += 1

        bclk_counts.append(count)
        period_ns_list.append(cur_ts - frame_start_ns)
        low_ns_list.append((high_start_ns or cur_ts) - frame_start_ns)
        frame_start_ns = cur_ts

    avg_period_ns = sum(period_ns_list) / len(period_ns_list)
    avg_low_ns = sum(low_ns_list) / len(low_ns_list)
    return {
        "bclk_per_frame": round(sum(bclk_counts) / len(bclk_counts)),
        "lrclk_hz": 1e9 / avg_period_ns,
        "duty": avg_low_ns / avg_period_ns,
    }
