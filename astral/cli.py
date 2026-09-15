import argparse
import json
import sys
from .codec import (
    header_redundancy_for,
    pack_message,
    pack_mckay_message,
    unpack_mckay_stream,
    unpack_stream,
    pack_text_message,
    pack_voice_message,
    pack_cmd_message,
    pack_text_with_dict,
    pack_cmd_batch,
    unpack_stream_sp,
    unpack_frames_tm,
)
from .spacepacket import (
    SpacePacketSequenceCounter,
    APID_MAP,
    wrap as sp_wrap,
)
from .commands import PersistentReplayGuard
from .dictionary import (
    DictionaryRegistry,
    MissionDictionary,
    configured_paths,
    train as train_dict,
)
from .voice import decode_bitstream_to_wav


def jsonable(value):
    """Make a decode result printable: bytes become hex, recursively."""
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


def print_json(value) -> None:
    print(json.dumps(jsonable(value), indent=2))


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
        print(f"Wrote {len(blob)} bytes to {args.output} " f"({len(blob)//32} atoms).")
    except Exception as e:
        print(f"Error packing message: {e}")
        return 1
    return 0


def cmd_unpack(args):
    try:
        stream = read_bin(args.input)
        key = bytes.fromhex(args.key) if getattr(args, "key", None) else None
        guard = None
        if getattr(args, "replay_state", None):
            guard = PersistentReplayGuard(args.replay_state, args.link_id)
        elif key is not None:
            print(
                "WARNING: no --replay-state given, so a command already "
                "received will be accepted again after a restart.",
                file=sys.stderr,
            )
        result = unpack_stream(stream, key=key, replay_guard=guard)
        if result.get("command_authenticated") is False and key is None:
            print(
                "WARNING: this is a command and no --key was given, so it is "
                "NOT authenticated. Do not act on it.",
                file=sys.stderr,
            )
        print_json(result)
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

        rng = random.Random(args.seed)
        for i in range(0, len(data), atom_size):
            atom = data[i : i + atom_size]
            if len(atom) < atom_size:
                break
            if rng.random() >= args.drop:
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
            print_json({"error": "incomplete", "gist": result.get("gist", {})})
            return 0
        msg = result.get("message")
        if not isinstance(msg, dict) or msg.get("type") != "VOICE":
            print_json(result)
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
        blob = pack_cmd_message(
            cmd, extra_fountain=args.extra, key=key, counter=args.counter
        )
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
        blob = pack_cmd_batch(
            batch, extra_fountain=args.extra, key=key, counter=args.counter
        )
        write_bin(args.output, blob)
        print(
            f"Wrote {len(blob)} bytes to {args.output} "
            f"({len(blob)//32} atoms). TYPE=CMD_BATCH"
        )
    except Exception as e:
        print(f"Error packing command batch: {e}")
        return 1
    return 0


def cmd_train_dict(args):
    """Train a mission dictionary from sample messages."""
    try:
        import glob as _glob

        paths = []
        for pattern in args.samples:
            matched = sorted(_glob.glob(pattern))
            paths.extend(matched if matched else [pattern])
        samples = [read_bin(p) for p in paths]
        dictionary = train_dict(samples, size=args.size, name=args.output)
        dictionary.save(args.output)
        print(
            f"Trained on {len(samples)} samples "
            f"({sum(len(s) for s in samples):,} bytes) -> {args.output}"
        )
        print(f"  dictionary id {dictionary.dict_id}, {len(dictionary.to_bytes()):,} bytes")
        print("  Ship this file to both ends; a receiver without it cannot decode.")
    except Exception as e:
        print(f"Error training dictionary: {e}")
        return 1
    return 0


def _load_dictionaries(paths):
    """Dictionaries from --dict, falling back to the ASTRAL_DICT environment."""
    registry = DictionaryRegistry()
    for path in list(paths or []) or configured_paths():
        registry.load(path)
    return registry if len(registry) else None


def _sending_dictionary(path):
    """The dictionary to compress with: --dict, else the first configured."""
    if path:
        return MissionDictionary.load(path)
    configured = configured_paths()
    return MissionDictionary.load(configured[0]) if configured else None


def cmd_pack_mckay(args):
    """Compress a file with McKay and send it as gist-first atoms."""
    try:
        data = read_bin(args.input)
        hr = args.header_redundancy
        if hr is None and args.survive_loss is not None:
            hr = header_redundancy_for(args.survive_loss)
        dictionary = _sending_dictionary(args.dict)
        blob = pack_mckay_message(
            data,
            data_type=args.type,
            dictionary=dictionary,
            extra_fountain=args.extra,
            channels=args.channels,
            min_redundancy=args.min_redundancy,
            redundancy=args.redundancy,
            header_redundancy=hr,
        )
        write_bin(args.output, blob)
        ratio = len(data) / len(blob) if blob else 0.0
        print(
            f"Wrote {len(blob)} bytes to {args.output} "
            f"({len(blob)//32} atoms). TYPE=MCKAY/{args.type} "
            f"source={len(data)} bytes, wire ratio={ratio:.2f}x"
        )
    except Exception as e:
        print(f"Error packing McKay message: {e}")
        return 1
    return 0


def cmd_unpack_mckay(args):
    """Decode a McKay atom stream, writing the recovered bytes out."""
    try:
        result = unpack_mckay_stream(
            read_bin(args.input), dictionaries=_load_dictionaries(args.dict)
        )
        data = result.get("data")
        if data is not None and args.output:
            write_bin(args.output, data)
            print(f"Recovered {len(data)} bytes to {args.output}")
        summary = {k: v for k, v in result.items() if k != "data"}
        summary["recovered_bytes"] = len(data) if data is not None else 0
        print_json(summary)
    except Exception as e:
        print(f"Error unpacking McKay stream: {e}")
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
            counter.set(apid, args.seq_count)
        # Wrap the stream that was read, rather than packing a new message:
        # this subcommand is a framing step, not an encoder.
        packet = sp_wrap(astral_stream, args.msg_type, counter)
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
        print_json(result)
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
        atom_stream, n_corrected, n_uncorrectable = decode_stream(rs_stream, e=args.e)
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
        print_json(stats)
    except Exception as exc:
        print(f"Error decoding RS stream: {exc}")
        return 1
    return 0


def cmd_frame_tm(args):
    """Segment an ASTRAL binary into CCSDS TM Transfer Frames."""
    try:
        from .tmframe import TmFrameCounter, WIRE_FRAME_SIZE, encode_frames

        data = read_bin(args.input)
        counter = TmFrameCounter()
        wire = encode_frames(
            data,
            scid=args.scid,
            vcid=args.vcid,
            counter=counter,
            randomise=not args.no_randomise,
            mode=args.mode,
        )
        write_bin(args.output, wire)
        n_frames = len(wire) // WIRE_FRAME_SIZE
        print(
            f"Framed {len(data)} bytes into {n_frames} TM frames "
            f"({len(wire)} bytes), SCID={args.scid}, VCID={args.vcid}"
        )
    except Exception as exc:
        print(f"Error framing TM stream: {exc}")
        return 1
    return 0


def cmd_deframe_tm(args):
    """Decode CCSDS TM Transfer Frames and recover the ASTRAL stream."""
    try:
        wire = read_bin(args.input)
        result = unpack_frames_tm(wire, randomise=not args.no_randomise)

        print_json(result)
    except Exception as exc:
        print(f"Error deframing TM stream: {exc}")
        return 1
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="astral")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_pack = sub.add_parser("pack", help="pack JSON message to atomized binary")
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
    p_unpack.add_argument(
        "--key",
        default=None,
        help="hex HMAC key; required to authenticate a CMD or CMD_BATCH",
    )
    p_unpack.add_argument(
        "--replay-state",
        default=None,
        metavar="PATH",
        help=(
            "file holding the last accepted command counter; required for "
            "replay protection to survive a restart"
        ),
    )
    p_unpack.add_argument(
        "--link-id",
        default="default",
        help="which uplink inside the replay state file (default: default)",
    )
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
    p_sim.add_argument(
        "--seed",
        type=int,
        default=None,
        help="RNG seed, for a reproducible loss pattern",
    )
    p_sim.set_defaults(func=cmd_simulate)

    p_train = sub.add_parser(
        "train-dict",
        help="train a mission dictionary from sample messages",
    )
    p_train.add_argument(
        "samples", nargs="+", help="sample message files (globs accepted)"
    )
    p_train.add_argument("-o", "--output", required=True, help="dictionary file")
    p_train.add_argument(
        "--size", type=int, default=16384, help="dictionary size in bytes"
    )
    p_train.set_defaults(func=cmd_train_dict)

    p_pack_mckay = sub.add_parser(
        "pack-mckay",
        help="compress a file with McKay and pack it as gist-first atoms",
    )
    p_pack_mckay.add_argument("input", help="file to compress and transmit")
    p_pack_mckay.add_argument("output", help="ASTRAL atom stream output")
    p_pack_mckay.add_argument(
        "--type",
        default="AUTO",
        choices=["AUTO", "TEXT", "TELEMETRY", "VOICE", "BINARY", "IMAGE"],
        help="data type hint for the compressor (default: AUTO)",
    )
    p_pack_mckay.add_argument("--extra", type=int, default=0)
    p_pack_mckay.add_argument(
        "--dict",
        default=None,
        metavar="PATH",
        help=(
            "mission dictionary to compress against (see train-dict); "
            "defaults to $ASTRAL_DICT"
        ),
    )
    p_pack_mckay.add_argument(
        "--channels",
        type=int,
        default=0,
        help="TELEMETRY channel count (0 = auto-detect)",
    )
    p_pack_mckay.add_argument(
        "--redundancy",
        type=float,
        default=1.0,
        help=(
            "proportional fountain overhead: 1.0 (default) doubles the "
            "compressed payload, 0.3 sends 30%% extra"
        ),
    )
    p_pack_mckay.add_argument(
        "--min-redundancy",
        type=int,
        default=10,
        help=(
            "floor on fountain atoms; the encoder sends "
            "K + max(min_redundancy, K) + extra, so the default doubles the "
            "compressed payload"
        ),
    )
    p_pack_mckay.add_argument(
        "--header-redundancy",
        type=int,
        default=None,
        help="copies of the gist/header atoms (default: scaled with size)",
    )
    p_pack_mckay.add_argument(
        "--survive-loss",
        type=float,
        default=None,
        help="pick header redundancy that keeps the gist at this loss rate",
    )
    p_pack_mckay.set_defaults(func=cmd_pack_mckay)

    p_unpack_mckay = sub.add_parser(
        "unpack-mckay", help="decode a McKay atom stream back to the source file"
    )
    p_unpack_mckay.add_argument("input")
    p_unpack_mckay.add_argument("output", nargs="?", default=None)
    p_unpack_mckay.add_argument(
        "--dict",
        action="append",
        default=None,
        metavar="PATH",
        help="mission dictionary to decode with; repeatable, defaults to $ASTRAL_DICT",
    )
    p_unpack_mckay.set_defaults(func=cmd_unpack_mckay)

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

    p_frame_tm = sub.add_parser(
        "frame-tm",
        help="segment an ASTRAL binary into CCSDS TM Transfer Frames",
    )
    p_frame_tm.add_argument("input", help="ASTRAL binary (or RS-protected) file")
    p_frame_tm.add_argument("output", help="TM wire stream output file")
    p_frame_tm.add_argument(
        "--scid",
        type=int,
        default=42,
        help="Spacecraft ID 0-1023 (default: 42)",
    )
    p_frame_tm.add_argument(
        "--vcid",
        type=int,
        default=0,
        help="Virtual Channel ID 0-7 (default: 0)",
    )
    p_frame_tm.add_argument(
        "--mode",
        default="VCA",
        choices=["VCA", "PACKET"],
        help="data field contents: opaque SDU (VCA) or CCSDS Space Packets",
    )
    p_frame_tm.add_argument(
        "--no-randomise",
        action="store_true",
        help="disable CCSDS pseudo-randomizer (default: randomiser ON)",
    )
    p_frame_tm.set_defaults(func=cmd_frame_tm)

    p_deframe_tm = sub.add_parser(
        "deframe-tm",
        help="decode CCSDS TM Transfer Frames and recover the ASTRAL payload",
    )
    p_deframe_tm.add_argument("input", help="TM wire stream file")
    p_deframe_tm.add_argument(
        "--no-randomise",
        action="store_true",
        help="disable de-randomizer (must match encoding, default: ON)",
    )
    p_deframe_tm.set_defaults(func=cmd_deframe_tm)

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

    p_pack_voice = sub.add_parser("pack-voice", help="pack a VOICE message from WAV")
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

    p_pack_cmd = sub.add_parser("pack-cmd", help="pack a CMD message from JSON string")
    p_pack_cmd.add_argument("json")
    p_pack_cmd.add_argument("output")
    p_pack_cmd.add_argument("--extra", type=int, default=0)
    p_pack_cmd.add_argument("--key", help="hex key for HMAC auth", default=None)
    p_pack_cmd.add_argument(
        "--counter",
        type=int,
        default=0,
        help="anti-replay counter; must increase on every command sent",
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
    p_pack_cmd_batch.add_argument(
        "--counter",
        type=int,
        default=0,
        help="anti-replay counter; must increase on every batch sent",
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
    sys.exit(main())
