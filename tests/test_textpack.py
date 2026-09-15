"""Tests for gistlink/textpack.py - dictionary-based text compression."""

from gistlink.textpack import decode_text, encode_text


class TestEncodeDecodeRoundtrip:
    def test_all_dict_words(self):
        """All-dictionary text roundtrips exactly."""
        text = "the data link is nominal"
        assert decode_text(encode_text(text)) == text

    def test_space_after_colon(self):
        """Space must be present between punctuation and following word."""
        text = "status: nominal"
        result = decode_text(encode_text(text))
        assert ": " in result, f"Expected ': ' in {result!r}"

    def test_space_after_comma(self):
        text = "arm, deploy, record"
        result = decode_text(encode_text(text))
        assert ", " in result or result.count(",") == 2

    def test_raw_words_preserved(self):
        """Unknown words (not in dict) must survive encoding unchanged."""
        text = "KESTREL-2 transmitting"
        result = decode_text(encode_text(text))
        assert "KESTREL" in result
        assert "transmitting" in result

    def test_empty_string(self):
        assert decode_text(encode_text("")) == ""

    def test_case_is_preserved(self):
        """Capitalisation of dictionary words survives the roundtrip."""
        for text in ("Hello world", "THE DATA link", "Nominal status"):
            assert decode_text(encode_text(text)) == text

    def test_mixed_case_dict_word_preserved(self):
        """A word whose case does not fit a flag goes out as a literal."""
        text = "hELLo world"
        assert decode_text(encode_text(text)) == text

    def test_encode_returns_bytes(self):
        assert isinstance(encode_text("hello"), bytes)

    def test_encode_non_string_raises(self):
        try:
            encode_text(42)  # type: ignore[arg-type]
            assert False, "Should have raised"
        except (ValueError, TypeError):
            pass

    def test_decode_non_bytes_raises(self):
        try:
            decode_text("not bytes")  # type: ignore[arg-type]
            assert False, "Should have raised"
        except (ValueError, TypeError):
            pass
