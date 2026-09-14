# McKay + ASTRAL

This file used to duplicate the integration guide against an API that no
longer exists (`McKayASTRALIntegration.compress_and_encode`,
`get_compression_stats`, `get_integration_stats`). The working documentation
now lives in two places:

- **[MCKAY_ASTRAL_INTEGRATION.md](MCKAY_ASTRAL_INTEGRATION.md)**: how the
  compressor and the transmission layer fit together, measured compression and
  loss-recovery numbers, stream format, redundancy sizing.
- **[QUICK_REFERENCE.md](QUICK_REFERENCE.md)**: command and API cheat sheet.

The short version:

```python
from astral import pack_mckay_message, unpack_mckay_stream

stream = pack_mckay_message(data, "TELEMETRY", channels=4)
result = unpack_mckay_stream(stream)
result["mckay"]  # metadata gist, survives loss of the entire body
result["data"]   # recovered bytes
```

```bash
python -m astral.cli pack-mckay input.bin out.bin --type TEXT
python -m astral.cli unpack-mckay out.bin recovered.bin
python -m astral.mckay_usage_example      # runnable demonstrations
```

The older `McKayCompressor` / `McKayASTRALIntegration` classes still exist as
thin wrappers over `compress()` and `decompress()`. They perform compression
only: they do not atomize or fountain-code. Use `pack_mckay_message` for the
full path.
