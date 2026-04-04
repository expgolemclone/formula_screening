"""Tests for display-width-aware string formatting."""

from formula_screening.fmt import display_width, ljust, truncate


class TestDisplayWidth:
    def test_ascii(self) -> None:
        assert display_width("hello") == 5

    def test_cjk(self) -> None:
        assert display_width("日本語") == 6

    def test_mixed(self) -> None:
        assert display_width("abc日本") == 7

    def test_empty(self) -> None:
        assert display_width("") == 0

    def test_fullwidth_symbols(self) -> None:
        assert display_width("＄") == 2


class TestLjust:
    def test_ascii(self) -> None:
        result: str = ljust("hi", 5)

        assert result == "hi   "

    def test_cjk_padding(self) -> None:
        result: str = ljust("日本", 6)

        assert result == "日本  "

    def test_no_pad_needed(self) -> None:
        result: str = ljust("hello", 3)

        assert result == "hello"


class TestTruncate:
    def test_ascii(self) -> None:
        result: str = truncate("hello world", 5)

        assert result == "hello"

    def test_cjk(self) -> None:
        result: str = truncate("日本語テスト", 4)

        assert result == "日本"

    def test_cjk_boundary(self) -> None:
        result: str = truncate("日本語", 5)

        assert result == "日本"

    def test_no_truncation(self) -> None:
        result: str = truncate("abc", 10)

        assert result == "abc"

    def test_empty(self) -> None:
        result: str = truncate("", 5)

        assert result == ""
