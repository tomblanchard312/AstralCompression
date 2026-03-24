import json, argparse
from .codec import (
    pack_message,
    unpack_stream,
    pack_text_message,
    pack_voice_message,
    pack_cmd_message,
    pack_text_with_dict,
    pack_cmd_batch,
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
        print(f"Wrote {len(blob)} bytes to {args.output} ({len(blob)//32} atoms).")
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
            atom = data[i : i + atom_size]
            if len(atom) < atom_size:
                break
            if random.random() >= args.drop:
                out += atom
        write_bin(args.output, bytes(out))
        print(
            f"Simulated drop rate {args.drop:.2f}. Input atoms={len(data)//32}, output atoms={len(out)//32}."
        )
    except Exception as e:
        print(f"Error simulating packet loss: {e}")
        return 1
    return 0


def cmd_pack_text(args):
    try:
        blob = pack_text_message(
            args.text, extra_fountain=args.extra, refine=args.refine
        )
        write_bin(args.output, blob)
        print(
            f"Wrote {len(blob)} bytes to {args.output} ({len(blob)//32} atoms). TYPE=TEXT"
        )
    except Exception as e:
        print(f"Error packing text message: {e}")
        return 1
    return 0


def cmd_pack_text_dict(args):
    try:
        words = [w.strip() for w in args.words.split(",") if w.strip()]
        blob = pack_text_with_dict(
            words, args.text, extra_fountain=args.extra, refine=args.refine
        )
        write_bin(args.output, blob)
        print(
            f"Wrote {len(blob)} bytes to {args.output} ({len(blob)//32} atoms). TYPE=TEXT with DICT_UPDATE"
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
            f"Wrote {len(blob)} bytes to {args.output} ({len(blob)//32} atoms). TYPE=VOICE"
        )
    except Exception as e:
        print(f"Error packing voice message: {e}")
        return 1
    return 0


def cmd_unpack_voice(args):
    try:
        result = unpack_stream(read_bin(args.input))
        if not result.get("complete"):
            print(json.dumps({"error": "incomplete", "gist": result.get("gist", {})}))
            return 0
        msg = result.get("message", {})
        if msg.get("type") != "VOICE":
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
        blob = pack_cmd_message(
            cmd, extra_fountain=args.extra, key=key, refine=args.refine
        )
        write_bin(args.output, blob)
        print(
            f"Wrote {len(blob)} bytes to {args.output} ({len(blob)//32} atoms). TYPE=CMD"
        )
    except Exception as e:
        print(f"Error packing command message: {e}")
        return 1
    return 0


def cmd_pack_cmd_batch(args):
    try:
        batch = json.loads(args.json)
        key = bytes.fromhex(args.key) if args.key else None
        contact = None
        if args.contact:
            contact = []
            for win in args.contact.split(","):
                if "-" in win:
                    s, e = win.split("-", 1)
                    contact.append((int(s), int(e)))
        blob = pack_cmd_batch(
            batch,
            extra_fountain=args.extra,
            key=key,
            key_id=args.key_id,
            counter=args.counter,
            refine=args.refine,
            contact_plan=contact,
        )
        write_bin(args.output, blob)
        print(
            f"Wrote {len(blob)} bytes to {args.output} ({len(blob)//32} atoms). TYPE=CMD_BATCH"
        )
    except Exception as e:
        print(f"Error packing command batch: {e}")
        return 1
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="astral")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_pack = sub.add_parser("pack", help="pack JSON message to atomized binary")
    p_pack.add_argument("input")
    p_pack.add_argument("output")
    p_pack.add_argument(
        "--extra", type=int, default=0, help="extra fountain packets for redundancy"
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
        "--drop", type=float, default=0.3, help="probability to drop each atom [0..1]"
    )
    p_sim.set_defaults(func=cmd_simulate)

    p_pack_text = sub.add_parser("pack-text", help="pack a TEXT message")
    p_pack_text.add_argument("text")
    p_pack_text.add_argument("output")
    p_pack_text.add_argument("--extra", type=int, default=0)
    p_pack_text.add_argument(
        "--refine", type=int, default=0, help="extra REFINE packets"
    )
    p_pack_text.set_defaults(func=cmd_pack_text)

    p_pack_text_dict = sub.add_parser(
        "pack-text-with-dict", help="pack TEXT with a mission lexicon update"
    )
    p_pack_text_dict.add_argument("words", help="comma-separated new words")
    p_pack_text_dict.add_argument("text")
    p_pack_text_dict.add_argument("output")
    p_pack_text_dict.add_argument("--extra", type=int, default=0)
    p_pack_text_dict.add_argument("--refine", type=int, default=0)
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
    p_pack_cmd.add_argument("--refine", type=int, default=0)
    p_pack_cmd.add_argument("--key", help="hex key for HMAC auth", default=None)
    p_pack_cmd.set_defaults(func=cmd_pack_cmd)

    p_pack_cmd_batch = sub.add_parser(
        "pack-cmd-batch", help="pack a batch of time-tagged commands (JSON)"
    )
    p_pack_cmd_batch.add_argument("json")
    p_pack_cmd_batch.add_argument("output")
    p_pack_cmd_batch.add_argument("--extra", type=int, default=0)
    p_pack_cmd_batch.add_argument("--refine", type=int, default=0)
    p_pack_cmd_batch.add_argument(
        "--key", help="hex key for HMAC over batch", default=None
    )
    p_pack_cmd_batch.add_argument(
        "--key-id", type=int, default=0, help="key slot id (0-255)"
    )
    p_pack_cmd_batch.add_argument(
        "--counter", type=int, default=0, help="anti-replay counter"
    )
    p_pack_cmd_batch.add_argument(
        "--contact", help="contact windows 'start-end,start-end' seconds", default=None
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
