import wave
import struct
import math
import random
import os

FRAME_MS = 20
FS = 8000
FRAME_SAMPLES = int(FS * FRAME_MS / 1000)
BAND_FREQS = [300, 600, 900, 1200, 1600, 2000, 2600, 3200]


def _read_wav_mono_16(path):
    if not isinstance(path, str):
        raise ValueError("path must be a string")
    if not os.path.exists(path):
        raise FileNotFoundError(f"WAV file not found: {path}")

    try:
        w = wave.open(path, "rb")
        nch = w.getnchannels()
        fs = w.getframerate()
        n = w.getnframes()
        sampwidth = w.getsampwidth()
        raw = w.readframes(n)
        w.close()
    except Exception as e:
        raise ValueError(f"Failed to read WAV file: {e}")

    if sampwidth != 2:
        raise ValueError("Only 16-bit PCM supported")
    if len(raw) % 2 != 0:
        raise ValueError("Invalid WAV data length")

    # Unpack samples
    samples = list(struct.unpack("<" + "h" * (len(raw) // 2), raw))

    # Downmix stereo to mono
    if nch == 2 and len(samples) % 2 == 0:
        samples = [
            (samples[i * 2] + samples[i * 2 + 1]) // 2 for i in range(len(samples) // 2)
        ]

    # Simple resampling if needed
    if fs != FS and fs > 0:
        ratio = fs / FS
        out = []
        i = 0.0
        while int(i) < len(samples):
            out.append(samples[int(i)])
            i += ratio
        samples = out

    return samples


def encode_wav_to_bitstream(path):
    if not isinstance(path, str):
        raise ValueError("path must be a string")

    try:
        samples = _read_wav_mono_16(path)
    except Exception as e:
        raise ValueError(f"Failed to read audio: {e}")

    if not samples:
        raise ValueError("No audio samples found")

    # Pad to frame boundary
    while len(samples) % FRAME_SAMPLES != 0:
        samples.append(0)

    # Apply pre-emphasis
    def preemphasis(samples, a=0.95):
        out = []
        prev = 0
        for x in samples:
            y = x - a * prev
            out.append(y)
            prev = x
        return out

    samples = preemphasis(samples)
    frames = [
        samples[i : i + FRAME_SAMPLES] for i in range(0, len(samples), FRAME_SAMPLES)
    ]

    if not frames:
        raise ValueError("No frames generated")

    # Encode frames
    bits = []
    for fr in frames:
        try:
            # Pitch detection
            voiced, lag = _detect_pitch(fr)
            pitch_q = max(0, min(127, lag - 20))  # 7 bits

            # Band analysis
            mags = [_analyze_band(fr, f) for f in BAND_FREQS]
            gain = sum(abs(x) for x in fr) / len(fr)

            # Quantization
            gain_q = _quantize_log(gain, 5)
            band_q = [_quantize_log(m, 5) for m in mags]

            # Pack bits
            frame_bits = [voiced & 1]
            frame_bits.extend([(pitch_q >> i) & 1 for i in range(7)])
            for q in band_q:
                frame_bits.extend([(q >> i) & 1 for i in range(5)])
            frame_bits.extend([(gain_q >> i) & 1 for i in range(5)])

            bits.extend(frame_bits)

        except Exception as e:
            raise ValueError(f"Failed to encode frame: {e}")

    if not bits:
        raise ValueError("No bits generated")

    # Pack header and bits
    nbits = len(bits)
    nframes = len(frames)

    if nbits > 0xFFFFFFFF or nframes > 0xFFFFFFFF:
        raise ValueError("Data too large for 32-bit encoding")

    out = bytearray()
    out.extend(b"VX")  # Magic
    out.append(1)  # Version
    out.extend(nframes.to_bytes(4, "little"))
    out.extend(nbits.to_bytes(4, "little"))

    # Pack bits into bytes
    byte_val = 0
    bit_pos = 0

    for bit in bits:
        byte_val |= (bit & 1) << bit_pos
        bit_pos += 1

        if bit_pos == 8:
            out.append(byte_val)
            byte_val = 0
            bit_pos = 0

    # Handle remaining bits
    if bit_pos > 0:
        out.append(byte_val)

    return bytes(out)


def _detect_pitch(frame):
    """Simple pitch detection using autocorrelation"""
    if len(frame) < 160:
        return 0, 50

    best_lag = 50
    best_corr = 0.0

    for lag in range(20, 160):
        if lag >= len(frame):
            break

        correlation = 0.0
        energy1 = 0.0
        energy2 = 0.0

        for i in range(len(frame) - lag):
            s1 = frame[i]
            s2 = frame[i + lag]
            correlation += s1 * s2
            energy1 += s1 * s1
            energy2 += s2 * s2

        if energy1 > 0 and energy2 > 0:
            normalized_corr = correlation / math.sqrt(energy1 * energy2)
            if normalized_corr > best_corr:
                best_corr = normalized_corr
                best_lag = lag

    voiced = 1 if best_corr > 0.3 else 0
    return voiced, best_lag


def _analyze_band(frame, freq):
    """Analyze energy in frequency band using simple DFT"""
    if not frame:
        return 0.0

    N = len(frame)
    k = int(0.5 + (N * freq) / FS)

    real_sum = 0.0
    imag_sum = 0.0

    for n, x in enumerate(frame):
        angle = 2.0 * math.pi * k * n / N
        real_sum += x * math.cos(angle)
        imag_sum += x * math.sin(angle)

    magnitude = math.sqrt(real_sum * real_sum + imag_sum * imag_sum)
    return magnitude / N


def _quantize_log(x, bits=5, eps=1e-9):
    """Logarithmic quantization"""
    if not isinstance(x, (int, float)) or x < 0:
        return 0

    if x <= eps:
        return 0

    log_val = math.log10(x)
    # Map to [0, 1] range assuming dynamic range of [-5, 0] in log10
    normalized = (log_val + 5.0) / 5.0
    normalized = max(0.0, min(1.0, normalized))

    quantized = int(normalized * ((1 << bits) - 1) + 0.5)
    return max(0, min((1 << bits) - 1, quantized))


def _dequantize_log(q, bits=5):
    """Reverse logarithmic quantization"""
    if q <= 0:
        return 1e-9

    normalized = q / ((1 << bits) - 1)
    log_val = normalized * 5.0 - 5.0
    return 10.0**log_val


def decode_bitstream_to_wav(data, out_path):
    """Fixed voice decoder with better error handling"""
    if not isinstance(data, bytes):
        raise ValueError("data must be bytes")
    if len(data) < 11:
        raise ValueError("data too short for voice bitstream")

    # Parse header
    if data[:2] != b"VX":
        raise ValueError("Invalid voice bitstream magic")

    try:
        version = data[2]
        nframes = int.from_bytes(data[3:7], "little")
        nbits = int.from_bytes(data[7:11], "little")
        bitbytes = data[11:]
    except Exception as e:
        raise ValueError(f"Failed to parse header: {e}")

    if nframes <= 0 or nbits <= 0:
        raise ValueError(f"Invalid header: nframes={nframes}, nbits={nbits}")
    if nframes > 100000 or nbits > 10000000:  # Sanity limits
        raise ValueError("Data size exceeds reasonable limits")

    # Unpack bits
    bits = []
    for byte_val in bitbytes:
        for bit_pos in range(8):
            bits.append((byte_val >> bit_pos) & 1)
            if len(bits) >= nbits:
                break
        if len(bits) >= nbits:
            break

    bits = bits[:nbits]  # Trim to exact count

    # Decode frames
    samples = []
    bit_pos = 0

    for frame_idx in range(nframes):
        try:
            # Extract frame parameters
            if bit_pos + 53 > len(bits):  # 1+7+8*5+5 = 53 bits per frame
                break

            voiced = bits[bit_pos]
            bit_pos += 1

            pitch_q = 0
            for i in range(7):
                if bit_pos < len(bits):
                    pitch_q |= bits[bit_pos] << i
                    bit_pos += 1

            band_q = []
            for band in range(8):
                q = 0
                for i in range(5):
                    if bit_pos < len(bits):
                        q |= bits[bit_pos] << i
                        bit_pos += 1
                band_q.append(q)

            gain_q = 0
            for i in range(5):
                if bit_pos < len(bits):
                    gain_q |= bits[bit_pos] << i
                    bit_pos += 1

            # Synthesize frame
            lag = pitch_q + 20
            mags = [_dequantize_log(q, 5) for q in band_q]
            gain = _dequantize_log(gain_q, 5)

            frame_samples = []
            for n in range(FRAME_SAMPLES):
                sample = 0.0

                # Band synthesis
                for band_idx, freq in enumerate(BAND_FREQS):
                    amp = mags[band_idx]
                    phase = 2.0 * math.pi * freq * n / FS
                    sample += amp * math.sin(phase)

                # Add excitation
                if voiced and lag > 0:
                    if n % lag < 5:  # Pulse train
                        sample += gain * 10.0
                else:
                    # White noise
                    sample += gain * (random.random() - 0.5) * 2.0

                frame_samples.append(sample)

            # De-emphasis filter
            de_emphasized = []
            prev = 0.0
            for s in frame_samples:
                output = s + 0.95 * prev
                de_emphasized.append(output)
                prev = output

            # Normalize and convert to int16
            if de_emphasized:
                max_val = max(abs(s) for s in de_emphasized)
                if max_val > 0:
                    scale = 16000.0 / max_val
                    frame_ints = [int(s * scale) for s in de_emphasized]
                else:
                    frame_ints = [0] * len(de_emphasized)

                samples.extend(frame_ints)

        except Exception as e:
            print(f"Warning: Failed to decode frame {frame_idx}: {e}")
            # Add silence for failed frame
            samples.extend([0] * FRAME_SAMPLES)

    if not samples:
        raise ValueError("No samples generated")

    # Write WAV file
    try:
        with wave.open(out_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(FS)

            # Clamp samples to int16 range
            clamped = [max(-32768, min(32767, int(s))) for s in samples]
            raw_data = struct.pack("<" + "h" * len(clamped), *clamped)
            w.writeframes(raw_data)
    except Exception as e:
        raise IOError(f"Failed to write WAV file: {e}")
