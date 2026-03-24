# Changelog

## 2026-03-24

### Removed
- Archived legacy standalone scripts removed:
  - compress_text_demo.py
  - compress_video.py
  - compress_video_enhanced.py
  - mckay_comprehensive_test.py
  - test_fountain_status.py
  - test_mckay_fountain_corrected.py
  - test_mckay_fountain_integration.py
  - test_mckay_fountain_multitype.py
  - test_mckay_gist_first.py
  - test_voice_optimization.py

### Changed
- CLI cleanup in astral/cli.py:
  - Removed no-op options that were parsed but not used:
    - pack-text: --refine
    - pack-text-with-dict: --refine
    - pack-cmd: --refine
    - pack-cmd-batch: --refine, --key-id, --counter, --contact
- Removed dead contact parsing path in cmd_pack_cmd_batch.
- Removed redundant local crc import in astral/container.py parse_atoms.
- Updated README to remove references to deleted standalone scripts and point users to maintained astral.cli workflows.
- Updated .flake8 excludes to reflect removed files.
