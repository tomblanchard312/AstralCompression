import argparse
import json
from .codec import (
    pack_message,
    unpack_stream,
    pack_text_message,
    pack_voice_message,
    pack_cmd_message,
    pack_text_with_dict,
    pack_cmd_batch,
    pack_message_sp,
    unpack_stream_sp,
)
from .spacepacket import (
    SpacePacketSequenceCounter,
    APID_MAP,
)
from .voice import decode_bitstream_to_wav


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Input file not found: {path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in {path}: {e}")
    except Exception as e:
        raise IOError(f"Error reading {path}: {e}")


def write_bin(path, b: bytes):
    try:
        with open(path, "wb") as f:
            f.write(b)
    except Exception as e:
        raise IOError(f"Error writing {path}: {e}")


def read_bin(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        raise FileNotFoundError(f"Input file not found: {path}")
    except Exception as e:
        raise IOError(f"Error reading {path}: {e}")


def cmd_pack(args):
    try:
        msg = read_json(args.input)
        blob = pack_message(msg, extra_fountain=args.extra)
        write_bin(args.output, blob)
        print(
            f"Wrote {len(blob)} bytes to {args.output} "
            f"({len(blob)//32} atoms)."
        )
    except Exception as e:
        print(f"Error packing message: {e}")
        return 1
    return 0


def cmd_unpack(args):
    try:
        stream = read_bin(args.input)
        result = unpack_stream(stream)
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"Error unpacking stream: {e}")
        return 1
    return 0


def cmd_simulate(args):
    try:
        data = read_bin(args.input)
        atom_size = 32
        out = bytearray()
        import random

        for i in range(0, len(data), atom_size):
            atom = data[i:i + atom_size]
            if len(atom) < atom_size:
                break
            if random.random() >= args.drop:
                out += atom
        write_bin(args.output, bytes(out))
        print(
            f"Simulated drop rate {args.drop:.2f}. "
            f"Input atoms={len(data)//32}, output atoms={len(out)//32}."
        )
    except Exception as e:
        print(f"Error simulating packet loss: {e}")
        return 1
    return 0


def cmd_pack_text(args):
    try:
        blob = pack_text_message(args.text, extra_fountain=args.extra)
        write_bin(args.output, blob)
        print(
            f"Wrote {len(blob)} bytes to {args.output} "
            f"({len(blob)//32} atoms). TYPE=TEXT"
        )
    except Exception as e:
        print(f"Error packing text message: {e}")
        return 1
    return 0


def cmd_pack_text_dict(args):
    try:
        words = [w.strip() for w in args.words.split(",") if w.strip()]
        blob = pack_text_with_dict(
            words,
            args.text,
            extra_fountain=args.extra,
        )
        write_bin(args.output, blob)
        print(
            f"Wrote {len(blob)} bytes to {args.output} "
            f"({len(blob)//32} atoms). TYPE=TEXT with DICT_UPDATE"
        )
    except Exception as e:
        print(f"Error packing text with dictionary: {e}")
        return 1
    return 0


def cmd_pack_voice(args):
    try:
        blob = pack_voice_message(args.input, extra_fountain=args.extra)
        write_bin(args.output, blob)
        print(
            f"Wrote {len(blob)} bytes to {args.output} "
            f"({len(blob)//32} atoms). TYPE=VOICE"
        )
    except Exception as e:
        print(f"Error packing voice message: {e}")
        return 1
    return 0


def cmd_unpack_voice(args):
    try:
        result = unpack_stream(read_bin(args.input))
        if not result.get("complete"):
            print(
                json.dumps(
                    {"error": "incomplete", "gist": result.get("gist", {})}
                )
            )
            return 0
        msg = result.get("message")
        if not isinstance(msg, dict) or msg.get("type") != "VOICE":
            print(json.dumps(result, indent=2))
            return 0
        data = msg.get("bytes", b"")
        decode_bitstream_to_wav(data, args.output)
        print(f"Decoded VOICE to WAV: {args.output}")
    except Exception as e:
        print(f"Error unpacking voice message: {e}")
        return 1
    return 0


def cmd_pack_cmd(args):
    try:
        cmd = json.loads(args.json)
        key = bytes.fromhex(args.key) if args.key else None
        blob = pack_cmd_message(cmd, extra_fountain=args.extra, key=key)
        write_bin(args.output, blob)
        print(
            f"Wrote {len(blob)} bytes to {args.output} "
            f"({len(blob)//32} atoms). TYPE=CMD"
        )
    except Exception as e:
        print(f"Error packing command message: {e}")
        return 1
    return 0


def cmd_pack_cmd_batch(args):
    try:
        batch = json.loads(args.json)
        key = bytes.fromhex(args.key) if args.key else None
        blob = pack_cmd_batch(batch, extra_fountain=args.extra, key=key)
        write_bin(args.output, blob)
        print(
            f"Wrote {len(blob)} bytes to {args.output} "
            f"({len(blob)//32} atoms). TYPE=CMD_BATCH"
        )
    except Exception as e:
        print(f"Error packing command batch: {e}")
        return 1
    return 0


def cmd_wrap_sp(args):
    try:
        astral_stream = read_bin(args.input)
        if args.msg_type not in APID_MAP:
            valid_types = list(APID_MAP.keys())
            print(
                f"Error: msg_type '{args.msg_type}' not in APID_MAP. "
                f"Valid types: {valid_types}"
            )
            return 1
        counter = SpacePacketSequenceCounter()
        apid = APID_MAP[args.msg_type][0]
        if args.seq_count is not None:
            counter._counts[apid] = args.seq_count % 16384
        packet = pack_message_sp(
            {"type": args.msg_type, "data": astral_stream},
            counter,
        )
        write_bin(args.output, packet)
        print(
            f"Wrapped {len(astral_stream)} bytes into Space Packet "
            f"({len(packet)} bytes total)."
        )
    except Exception as e:
        print(f"Error wrapping Space Packet: {e}")
        return 1
    return 0


def cmd_unwrap_sp(args):
    try:
        packet = read_bin(args.input)
        result = unpack_stream_sp(packet)
        if "error" in result:
            print(f"Error: {result['error']}")
            return 1
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"Error unwrapping Space Packet: {e}")
        return 1
    return 0


def cmd_encode_rs(args):
    """Apply RS FEC to an ASTRAL binary file."""
    try:
        from .rs_fec import encode_stream, CODEWORD_SIZE

        atom_stream = read_bin(args.input)
        if len(atom_stream) % 32 != 0:
            print(
                f"Error: input length {len(atom_stream)} "
                "is not a multiple of 32. "
                "Is this a valid ASTRAL stream?"
            )
            return 1
        rs_stream = encode_stream(atom_stream, e=args.e)
        write_bin(args.output, rs_stream)
        n_atoms = len(atom_stream) // 32
        print(
            f"RS-E{args.e} encoded {n_atoms} atoms: "
            f"{len(atom_stream)} -> {len(rs_stream)} bytes "
            f"(codeword_size={CODEWORD_SIZE[args.e]})"
        )
    except Exception as exc:
        print(f"Error encoding RS stream: {exc}")
        return 1
    return 0


def cmd_decode_rs(args):
    """Decode an RS-protected stream, correcting bit errors."""
    try:
        from .rs_fec import decode_stream, CODEWORD_SIZE

        rs_stream = read_bin(args.input)
        atom_stream, n_corrected, n_uncorrectable = decode_stream(
            rs_stream, e=args.e
        )
        write_bin(args.output, atom_stream)
        n_atoms = len(atom_stream) // 32
        stats = {
            "e": args.e,
            "input_bytes": len(rs_stream),
            "output_atoms": n_atoms,
            "corrected_symbols": n_corrected,
            "uncorrectable_atoms": n_uncorrectable,
            "codeword_size": CODEWORD_SIZE[args.e],
        }
        print(json.dumps(stats, indent=2))
    except Exception as exc:
        print(f"Error decoding RS stream: {exc}")
        return 1
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="astral")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_pack = sub.add_parser(
        "pack", help="pack JSON message to atomized binary"
    )
    p_pack.add_argument("input")
    p_pack.add_argument("output")
    p_pack.add_argument(
        "--extra",
        type=int,
        default=0,
        help="extra fountain packets for redundancy",
    )
    p_pack.set_defaults(func=cmd_pack)

    p_unpack = sub.add_parser(
        "unpack", help="unpack atomized binary to JSON (best-effort)"
    )
    p_unpack.add_argument("input")
    p_unpack.set_defaults(func=cmd_unpack)

    p_sim = sub.add_parser("simulate", help="simulate random atom loss")
    p_sim.add_argument("input")
    p_sim.add_argument("output")
    p_sim.add_argument(
        "--drop",
        type=float,
        default=0.3,
        help="probability to drop each atom [0..1]",
    )
    p_sim.set_defaults(func=cmd_simulate)

    p_wrap_sp = sub.add_parser(
        "wrap-sp", help="wrap ASTRAL binary in CCSDS Space Packet"
    )
    p_wrap_sp.add_argument("input", help="ASTRAL binary stream")
    p_wrap_sp.add_argument("output", help="Space Packet output file")
    p_wrap_sp.add_argument(
        "--msg-type",
        default="DETECT",
        help="message type (default: DETECT)",
    )
    p_wrap_sp.add_argument(
        "--seq-count",
        type=int,
        default=None,
        help="optional initial sequence counter (0-16383)",
    )
    p_wrap_sp.set_defaults(func=cmd_wrap_sp)

    p_unwrap_sp = sub.add_parser(
        "unwrap-sp", help="unwrap CCSDS Space Packet to ASTRAL"
    )
    p_unwrap_sp.add_argument("input", help="Space Packet input file")
    p_unwrap_sp.set_defaults(func=cmd_unwrap_sp)

    p_encode_rs = sub.add_parser(
        "encode-rs",
        help="protect an ASTRAL binary with CCSDS Reed-Solomon FEC",
    )
    p_encode_rs.add_argument("input", help="ASTRAL binary file")
    p_encode_rs.add_argument("output", help="RS-protected output file")
    p_encode_rs.add_argument(
        "--e",
        type=int,
        default=16,
        choices=[8, 16],
        help="error-correction strength E=8 or E=16 (default: 16)",
    )
    p_encode_rs.set_defaults(func=cmd_encode_rs)

    p_decode_rs = sub.add_parser(
        "decode-rs",
        help="decode a CCSDS RS-protected stream, correcting bit errors",
    )
    p_decode_rs.add_argument("input", help="RS-protected binary file")
    p_decode_rs.add_argument("output", help="recovered ASTRAL binary file")
    p_decode_rs.add_argument(
        "--e",
        type=int,
        default=16,
        choices=[8, 16],
        help="error-correction strength used during encoding (default: 16)",
    )
    p_decode_rs.set_defaults(func=cmd_decode_rs)

    p_pack_text = sub.add_parser("pack-text", help="pack a TEXT message")
    p_pack_text.add_argument("text")
    p_pack_text.add_argument("output")
    p_pack_text.add_argument("--extra", type=int, default=0)
    p_pack_text.set_defaults(func=cmd_pack_text)

    p_pack_text_dict = sub.add_parser(
        "pack-text-with-dict", help="pack TEXT with a mission lexicon update"
    )
    p_pack_text_dict.add_argument("words", help="comma-separated new words")
    p_pack_text_dict.add_argument("text")
    p_pack_text_dict.add_argument("output")
    p_pack_text_dict.add_argument("--extra", type=int, default=0)
    p_pack_text_dict.set_defaults(func=cmd_pack_text_dict)

    p_pack_voice = sub.add_parser(
        "pack-voice", help="pack a VOICE message from WAV"
    )
    p_pack_voice.add_argument("input")
    p_pack_voice.add_argument("output")
    p_pack_voice.add_argument("--extra", type=int, default=0)
    p_pack_voice.set_defaults(func=cmd_pack_voice)

    p_unpack_voice = sub.add_parser(
        "unpack-voice", help="unpack a VOICE message to WAV"
    )
    p_unpack_voice.add_argument("input")
    p_unpack_voice.add_argument("output")
    p_unpack_voice.set_defaults(func=cmd_unpack_voice)

    p_pack_cmd = sub.add_parser(
        "pack-cmd", help="pack a CMD message from JSON string"
    )
    p_pack_cmd.add_argument("json")
    p_pack_cmd.add_argument("output")
    p_pack_cmd.add_argument("--extra", type=int, default=0)
    p_pack_cmd.add_argument(
        "--key", help="hex key for HMAC auth", default=None
    )
    p_pack_cmd.set_defaults(func=cmd_pack_cmd)

    p_pack_cmd_batch = sub.add_parser(
        "pack-cmd-batch", help="pack a batch of time-tagged commands (JSON)"
    )
    p_pack_cmd_batch.add_argument("json")
    p_pack_cmd_batch.add_argument("output")
    p_pack_cmd_batch.add_argument("--extra", type=int, default=0)
    p_pack_cmd_batch.add_argument(
        "--key", help="hex key for HMAC over batch", default=None
    )
    p_pack_cmd_batch.set_defaults(func=cmd_pack_cmd_batch)

    try:
        args = p.parse_args(argv)
        result = args.func(args)
        return result if result is not None else 0
    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        return 1
    except Exception as e:
        print(f"Error: {e}")
        return 1


if __name__ == "__main__":
    exit(main())
