# Feature Specification: 68k PSG Sound Chip

**Feature Branch**: `001-psg-sound-chip` (spec directory; no git branch created — work is on `main`)

**Created**: 2026-09-10

**Status**: Draft

**Input**: User description: "let's create a spec for this implementation." — i.e. the full chip described in `docs/design/sound-chip.md` and `docs/design/68k-bus-interface.md`, incorporating the design review of those documents.

## Overview

A programmable sound generator (PSG) chip for a 68k-based retrocomputer, in the tradition of the AY-3-8910 / SN76489. It appears to the CPU as a small block of byte-wide registers on the 68k asynchronous bus. Software programs three square-wave tone channels and one noise channel; the chip mixes them into a single mono signal and presents it simultaneously on three independent outputs — PWM (analog via an external filter), I2S (to an external DAC), and S/PDIF (to consumer audio gear) — so that at least one output path works on first silicon.

The bus-interface portion is also the first implementation of a reusable 68k peripheral interface that a sibling chip (an SPI/I2C bridge) will reuse.

**Stakeholders**:
- **Software author** — writes the 68k driver and music playback routine; cares about the register model, pitch accuracy, and glitch-free updates.
- **Board bring-up engineer** — first to power the fabricated chip; cares about safe reset behavior, bus correctness, and being able to observe the chip with a scope before any software works.
- **Listener** — hears the result; cares that notes are in tune and there are no clicks, thumps, or dropouts.
- **Sibling-project developer** — reuses the bus interface in another chip; cares that it is separable and independently verified.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Safe, correct bus citizen (Priority: P1)

The bring-up engineer installs the chip on the retrocomputer's bus alongside other peripherals. From power-on, the chip must never disturb the bus: it doesn't acknowledge cycles that aren't addressed to it, doesn't drive the data bus except when being read, and releases its acknowledge and data lines fast enough that the next bus cycle — possibly to a different device — is unaffected. Software can write every control register and read back what it wrote.

**Why this priority**: A chip that jams or corrupts the shared bus takes the whole computer down with it, and every other story depends on register access working. This is also exactly the failure class that killed the predecessor PS/2 chip on tt08.

**Independent Test**: With no audio functionality present at all, drive randomized 68k read/write cycles (randomized timing relative to the chip's clock, including aborted cycles and cycles addressed to other devices) and confirm every write reads back correctly, the acknowledge and data lines are released within bound, and unaddressed cycles produce no response.

**Acceptance Scenarios**:

1. **Given** the chip has just come out of reset, **When** no bus cycle is addressed to it, **Then** its acknowledge output is deasserted and its data bus is not driven.
2. **Given** an idle chip, **When** the CPU writes a byte to a register, **Then** the chip acknowledges the cycle and a subsequent read of that register returns the same byte.
3. **Given** a read cycle in progress, **When** the CPU releases address strobe, **Then** the chip releases the data bus and deasserts its acknowledge within 50 ns.
4. **Given** a cycle with the chip-select deasserted, **When** address strobe and data strobe toggle, **Then** the chip neither acknowledges nor drives the data bus.
5. **Given** a write cycle, **When** address strobe is released before the chip acknowledges (aborted cycle), **Then** the target register holds either its old value or the complete new value — never a partial or corrupted one — and the chip returns to idle ready for the next cycle.

---

### User Story 2 - Play in-tune music through the PWM output (Priority: P1)

The software author programs a tone channel with a pitch and volume, enables it, and hears a clean square-wave note from the PWM pin through a simple external RC filter. Changing pitch mid-note produces no audible glitch. Three tone channels and the noise channel can play at once without distortion from overflow.

**Why this priority**: PWM is the designated "must work" output — the simplest path from chip to speaker, requiring no external IC. Together with Story 1 it's a complete, useful sound chip.

**Independent Test**: With only the bus interface, sound engine, and PWM output present, program each channel via register writes and measure the PWM output's filtered frequency and relative level against the programmed values.

**Acceptance Scenarios**:

1. **Given** tone channel 0 is programmed for concert A (440 Hz) at full volume and enabled, **When** the PWM output is low-pass filtered, **Then** the result is a square wave within ±10 cents of 440 Hz.
2. **Given** all channels are disabled, **When** the PWM output is observed, **Then** it is a steady 50% duty-cycle waveform (filtered: a constant mid-level voltage, no audible tone).
3. **Given** all four channels are enabled at maximum volume, **When** the output is observed, **Then** the mix never wraps around or clips beyond the output's range.
4. **Given** a channel is playing, **When** software rewrites its pitch (which spans more than one register byte), **Then** the output switches directly from the old pitch to the new pitch without ever producing an intermediate pitch formed from mixed old and new bytes.
5. **Given** a channel is playing, **When** its volume is stepped from 15 down to 1, **Then** each step is an audibly similar reduction in loudness and 0 is silent.

---

### User Story 3 - Hear the chip through an I2S DAC (Priority: P2)

The bring-up engineer connects an off-the-shelf I2S DAC module (PCM5102-class) to the three I2S pins and hears the same music as the PWM output, at a clean 48 kHz, with no configuration of the DAC.

**Why this priority**: Gives a much higher-quality analog path than PWM using a cheap, common module, and is a straightforward serial format with low implementation risk. Not required for a minimum viable chip.

**Independent Test**: Capture the I2S bit clock, word select, and data lines, decode the sample stream, and compare it against the expected mixed signal for a programmed channel setup.

**Acceptance Scenarios**:

1. **Given** the chip is running, **When** the I2S lines are observed, **Then** the word-select rate is 48 kHz (at nominal clock), there are exactly 64 bit clocks per word-select period, and data is MSB-first, delayed one bit clock after each word-select transition (standard Philips I2S).
2. **Given** a channel is playing, **When** left and right slots are decoded, **Then** both slots carry the identical mono sample.
3. **Given** all channels are disabled, **When** the I2S stream is decoded, **Then** every sample is exactly zero.
4. **Given** a single enabled tone channel, **When** decoded samples are averaged over a whole number of tone periods, **Then** the average is zero (the tone contributes no DC offset).

---

### User Story 4 - Connect directly to consumer audio gear via S/PDIF (Priority: P3)

The engineer connects the S/PDIF pin (through a simple resistor divider or transformer) to a consumer receiver or USB S/PDIF interface. The receiver locks, identifies the stream as 48 kHz PCM, and plays the music with no external DAC.

**Why this priority**: Highest effort and highest payoff — no external IC needed and works with a wide range of existing gear — but also the most complex format. Per FR-051, it's the one feature cut if the design doesn't fit a 1x1 tile.

**Independent Test**: Capture the S/PDIF line, decode it with a reference IEC 60958 decoder in the testbench, and confirm preambles, parity, channel status, and audio samples.

**Acceptance Scenarios**:

1. **Given** the chip is running, **When** the S/PDIF line is decoded, **Then** every subframe has a valid preamble (block-start, left, right in the correct sequence), valid parity, and a valid-audio flag.
2. **Given** a full 192-frame block is decoded, **When** the channel-status bits are assembled, **Then** they identify the stream as consumer-format linear PCM at 48 kHz.
3. **Given** a channel is playing, **When** audio samples are decoded, **Then** they match the samples on the I2S output.

---

### User Story 5 - Observe the chip before any software works (Priority: P2)

The bring-up engineer powers the board and clocks the chip before any driver exists. With just a scope or logic analyzer, they can confirm the chip is clocked and its internal timing is alive, and — once the CPU starts poking it — that register writes are landing.

**Why this priority**: Cheap insurance directly addressing the tt08 lesson that status signals must reach a visible pin. It turns "the chip is silent, why?" from guesswork into a measurement.

**Independent Test**: With the chip in reset-released idle state and no bus activity, observe a spare output pin and measure a 48 kHz heartbeat; perform one register write and observe a pulse on the second spare pin.

**Acceptance Scenarios**:

1. **Given** the chip is clocked and out of reset, **When** the heartbeat pin is observed, **Then** it toggles at the audio sample rate (48 kHz at nominal clock) regardless of register contents.
2. **Given** an idle chip, **When** the CPU completes one register write, **Then** the write-indicator pin pulses exactly once.

---

### Edge Cases

- **Back-to-back cycles**: CPU issues a cycle to this chip immediately followed by one to a different peripheral — this chip's acknowledge must not still be asserted when the other device's cycle begins.
- **Aborted cycle**: Address strobe released before the chip acknowledges — see Story 1, Scenario 5.
- **Bus signals changing near a clock edge**: Every bus input changes asynchronously to the chip's clock; state must never be corrupted by input timing.
- **Frequency value of zero**: Programming a tone period of zero must produce a defined result (treated as the smallest non-zero period, or silence) — never a stuck or undefined output.
- **Pitch update straddling a sample**: Covered by Story 2, Scenario 4 — multi-byte pitch changes take effect atomically.
- **Tones above the audio band**: A tone programmed above ~20 kHz may alias on the digital outputs; this is acceptable (it's a chiptune chip) but must not destabilize any output format's framing.
- **Output format disabled by software**: A disabled output holds an idle state (PWM steady, S/PDIF/I2S silent or idle) without affecting the other outputs.
- **System clock not exactly 24.576 MHz**: All output rates scale proportionally; framing and ratios remain correct.
- **Reset asserted during a bus cycle**: The chip releases the data bus and deasserts acknowledge immediately.

## Requirements *(mandatory)*

### Functional Requirements

**Bus interface**

- **FR-001**: The chip MUST respond as a byte-wide memory-mapped peripheral on the 68k asynchronous bus, and only to cycles in which both its chip-select and the address strobe are asserted.
- **FR-002**: The chip MUST treat every bus input (chip-select, address strobe, data strobe, read/write, register-select address lines, and data lines) as asynchronous to its own clock, and MUST never enter an invalid state or corrupt register contents regardless of when those inputs change.
- **FR-003**: The chip MUST generate its own cycle acknowledge (DTACK) on a dedicated always-driven output, asserting it only after write data has been captured or read data is valid on the bus.
- **FR-004**: The chip MUST deassert its acknowledge and release the data bus within 50 ns of address strobe being released, independent of the phase of its internal clock.
- **FR-005**: The chip MUST drive the data bus only during a read cycle addressed to it, and MUST never drive it during a write cycle or when not selected.
- **FR-006**: A write cycle aborted before acknowledge MUST leave the target register with either its complete old value or its complete new value.
- **FR-007**: The chip MUST target a 68000 bus with the chip wired to a single byte-lane data strobe (UDS or LDS): register select uses address lines A1–A3, so the chip's registers appear at every other byte address within its decoded block.
- **FR-008**: The bus-interface function MUST be separable from the audio function, expose a generic register read/write boundary with no audio-specific assumptions, allow the number of register-select address bits to be configured, and be verifiable on its own.

**Register model**

- **FR-010**: Software MUST be able to control every channel's pitch, volume, and enable state, the noise channel's rate and volume, and each output format's enable state through exactly eight directly-addressed byte registers, each reachable in a single bus cycle with no address-latch or indirect access: two per tone channel (pitch low byte; volume combined with the pitch high bits), one for the noise channel (volume combined with rate), and one for the channel and output-format enables. Exact bit layout is a plan-level decision.
- **FR-011**: Every writable register MUST read back the value last written to it.
- **FR-012**: A pitch value that spans more than one register MUST take effect atomically: the audio output MUST never reflect a combination of old and new register bytes.
- **FR-013**: Reads MUST have no side effects on chip state.
- **FR-014**: On reset, all channels MUST be disabled and silent, all volumes zero, all output formats enabled and emitting silence, acknowledge deasserted, and the data bus released.

**Sound generation**

- **FR-020**: The chip MUST provide three independent square-wave tone channels, each with a 12-bit (4096-step) programmable pitch.
- **FR-021**: Tone pitch MUST span at least 27.5 Hz (A0) at the low end, and every equal-tempered note MUST be reproducible within ±10 cents from A0 to C6 (1047 Hz) and within ±25 cents from C6 to C8 (4186 Hz), at nominal clock.
- **FR-022**: The chip MUST provide one pseudo-random noise channel with 16 programmable rate settings.
- **FR-023**: Each of the four channels MUST have a 16-level volume, where level 0 is silent and successive levels are spaced as approximately equal loudness steps (logarithmic, as on the AY-3-8910).
- **FR-024**: Each of the four channels MUST have an independent enable.
- **FR-025**: The chip MUST mix all enabled channels into a single mono signal that cannot overflow, wrap around, or clip, even with all channels at maximum volume.
- **FR-026**: Each tone channel MUST contribute no sustained DC offset to the mixed signal; silence (all channels disabled or at volume 0) MUST correspond to the output's exact midpoint/zero.
- **FR-027**: A tone period value of zero MUST produce a defined, documented behavior.

**Audio outputs**

- **FR-030**: The chip MUST present the same mixed mono signal on all three outputs simultaneously.
- **FR-031**: The PWM output MUST offer at least 64 distinguishable levels with a carrier frequency of at least 150 kHz (at nominal clock), so a simple external RC filter can recover the audio band; silence MUST be a 50% duty cycle.
- **FR-032**: The I2S output MUST follow the standard Philips I2S format: 48 kHz frame rate, 64 bit clocks per frame (32 per channel), MSB-first, data delayed one bit clock after each word-select transition, signed two's-complement samples left-justified in each slot, identical sample in both left and right slots — directly consumable by common I2S DAC modules without configuration or a master clock.
- **FR-033**: The S/PDIF output MUST follow the IEC 60958 consumer format at 48 kHz: biphase-mark coding, correct block-start/left/right preamble sequence, even parity per subframe, validity flag indicating valid audio, and a 192-frame channel-status block identifying consumer linear PCM at 48 kHz.
- **FR-034**: The digital audio sample rate MUST be exactly the system clock divided by 512, and all output timing MUST derive from that same clock, so a clock deviation shifts all rates proportionally without breaking any output's framing.
- **FR-035**: Each output format MUST be individually disable-able by software; a disabled output holds a steady idle state and does not affect the others.

**Observability**

- **FR-040**: A spare output pin MUST carry a heartbeat at the audio sample rate whenever the chip is clocked and out of reset, independent of register contents.
- **FR-041**: A second spare output pin MUST pulse once for each completed register write.

**Physical**

- **FR-050**: The chip MUST run from a single clock input at a nominal 24.576 MHz and use the standard Tiny Tapeout pin allocation described in the design documents.
- **FR-051**: The chip MUST fit a 1x1 tile. If the full design does not fit, the S/PDIF output (Story 4) MUST be removed to stay at 1x1; the tile size MUST NOT grow, and the PWM and I2S outputs and all four sound channels MUST be kept. If S/PDIF is removed, its pin holds a steady idle level.

**Verification**

- **FR-060**: Each user story MUST be covered by automated simulation tests at both the behavioral and post-synthesis levels, including randomized bus timing (Story 1) and, if S/PDIF is retained, a closed-loop S/PDIF decode (Story 4).

### Key Entities

- **Tone channel** (×3): pitch (12-bit period), volume (4-bit), enable.
- **Noise channel** (×1): rate, volume (4-bit), enable.
- **Mixer / output control**: per-channel enables, per-output-format enables.
- **Mixed sample**: the single mono value, updated at the audio sample rate, presented on all three outputs.
- **Register**: a byte-wide location addressable by the CPU through the bus interface; its layout is set by FR-010.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Over at least 10,000 randomized bus cycles (random read/write mix, random register, random timing relative to the chip clock, including aborted cycles and cycles to other devices), every read returns the last value written with zero errors.
- **SC-002**: In every tested bus cycle, acknowledge and data bus are released within 50 ns of address strobe release, measured on the post-synthesis design.
- **SC-003**: Every equal-tempered note from A0 to C6 is reproduced within ±10 cents, and from C6 to C8 within ±25 cents.
- **SC-004**: With all channels disabled, 100% of decoded I2S and S/PDIF samples are exactly zero, and the PWM output holds a 50% duty cycle.
- **SC-005**: 100% of decoded I2S samples, over at least 1,000 consecutive frames, match a reference model of the mixed signal for the programmed channel setup.
- **SC-006**: *(If S/PDIF is retained per FR-051.)* 100% of S/PDIF subframes decoded over at least two full 192-frame blocks have valid preambles and parity, the channel status reports 48 kHz consumer PCM, and the audio matches the I2S output sample-for-sample.
- **SC-007**: On first-silicon bring-up, the heartbeat pin reads 48 kHz (±0.5%) on a frequency counter with only power, clock, and reset applied.
- **SC-008**: On first-silicon bring-up, at least one of the three audio outputs produces a correctly-pitched audible tone from a register-programmed channel.
- **SC-009**: The complete design fits the allocated silicon area and meets timing at the nominal clock rate with positive margin.

## Assumptions

- **Target bus**: 68000-family CPU clocked at up to 16 MHz. The 68000's allowed hold time for DTACK and data after address strobe negation tightens with CPU speed (on the order of 100 ns at 16 MHz), which is why FR-004 sets a 50 ns bound independent of the chip's clock rather than relying on a synchronized release.
- **External glue**: The board provides full address decode into the chip-select input and combines this chip's acknowledge with other peripherals' through a single AND gate (per the bus-interface design doc). Both are out of scope for this chip.
- **Mono only, no interrupt**: Resolved in the design doc; not revisited. The chip has no interrupt output.
- **Clock accuracy**: The board's clock generator may not hit 24.576 MHz exactly. Because all rates are exact ratios of one clock (FR-034), a small deviation only shifts absolute pitch and sample rate proportionally; that's acceptable.
- **Volume curve**: Logarithmic volume steps (FR-023) are assumed because a linear 4-bit volume makes the lower steps sound disproportionately loud and is a common complaint about naive PSG clones; the exact curve is a plan-level decision.
- **Aliasing**: The digital outputs sample the square waves at 48 kHz without band-limiting. High-pitched tones will alias; this is characteristic of the genre and accepted.
- **Noise rate**: 16 settings (vs. the AY-3-8910's 32), because the noise volume and rate share one register under FR-010's eight-register map.
- **No identification register**: The eight-register map is exactly full, so there is no read-only ID or version register. Software confirms the chip's presence by writing and reading back a register (FR-011); the heartbeat pin (FR-040) confirms it's alive without software.
- **Byte lane**: Whether the chip sits on UDS (even addresses, upper data byte) or LDS (odd addresses, lower data byte) is a board wiring choice and doesn't affect the chip.
- **Output idle states**: A disabled PWM output holds a steady level; disabled I2S/S-PDIF outputs stop toggling (receivers will lose lock, which is expected when software disables them).
- **Reuse**: The sibling SPI/I2C bridge project will reuse the bus interface. Its needs beyond FR-008 are out of scope here.
- **Deviations from the design docs**: This spec intentionally tightens the design docs in several places based on the design review — the tone clock rate (implied by FR-021), signed zero-centered digital output (FR-026, FR-032), atomic pitch update (FR-012), clock-independent bus release (FR-004), S/PDIF channel status content (FR-033), and use of the spare pins for bring-up visibility (FR-040, FR-041). Per the 2026-09-10 clarifications it also departs from the docs on bus variant (68000 single-lane instead of the recommended 68008) and register organization (eight direct registers instead of `sound-chip.md` §6's indirect address/data scheme). The design docs should be updated to match once this spec is accepted.

## Clarifications

### Session 2026-09-10

- Q: Which CPU bus variant is the board wired for? → A: 68000 using a single byte-lane strobe; register select on A1–A3, registers at every other byte address (FR-007).
- Q: Which register organization? → A: Eight directly-addressed registers with pitch and volume packed together; no indirect access, no ID register, 4-bit noise rate (FR-010, FR-022).
- Q: If the design doesn't fit, cut S/PDIF or grow the tile? → A: Cut S/PDIF and stay at 1x1 (FR-051).
