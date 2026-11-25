"""Bidi layout for overlay lines (services/pdf_fonts/bidi.py)."""

import pytest

from services import pdf_fonts
from services.pdf_fonts.bidi import resolve_levels, visual_order
from tests.test_template import TestTemplate


def _drawn(text: str) -> list[str]:
    return [run.text for run in pdf_fonts.split_runs(text)]


class TestResolveLevels(TestTemplate):
    def test_latin_is_level_zero(self):
        assert resolve_levels("Ann Lee") == [0] * 7

    def test_neutral_between_rtl_words_is_rtl(self):
        assert resolve_levels("اب جد") == [1, 1, 1, 1, 1]

    def test_neutral_between_directions_takes_base(self):
        assert resolve_levels("a اب b") == [0, 0, 1, 1, 0, 0]

    def test_number_inside_rtl_is_level_two(self):
        # W2: European digits after Arabic letters act as Arabic numbers.
        assert resolve_levels("شارع 12 الملك") == [1] * 5 + [2, 2] + [1] * 6

    def test_number_after_latin_stays_ltr(self):
        assert resolve_levels("No 12") == [0] * 5

    def test_decimal_separator_joins_the_number(self):
        # W4 keeps "12.5" one number; the trailing '%' follows an Arabic
        # number (not a European one), so per W5/N2 it falls back to base L.
        assert resolve_levels("اب 12.5%") == [1, 1, 1, 2, 2, 2, 2, 0]

    def test_brackets_around_rtl_after_rtl_stay_rtl(self):
        # N0: the pair holds only Arabic and follows Arabic, so it is R.
        assert resolve_levels("محمد (علي)") == [1] * 10

    def test_brackets_with_ltr_content_are_ltr(self):
        assert resolve_levels("محمد (Ali)") == [1, 1, 1, 1, 0, 0, 0, 0, 0, 0]

    def test_rtl_brackets_after_ltr_take_base_direction(self):
        # N0 c.2: opposite-direction content but left-to-right context.
        assert resolve_levels("by (علي)") == [0, 0, 0, 0, 1, 1, 1, 0]

    def test_combining_mark_follows_its_letter(self):
        assert resolve_levels("مُ") == [1, 1]


class TestVisualOrder(TestTemplate):
    @pytest.mark.parametrize(
        ("levels", "expected"),
        [
            ([0, 0, 0], [0, 1, 2]),
            ([0, 1, 1, 0], [0, 2, 1, 3]),
            ([1, 1, 2, 2, 1], [4, 2, 3, 1, 0]),
            ([], []),
        ],
    )
    def test_l2_reversal(self, levels, expected):
        assert visual_order(levels) == expected


class TestSplitRunsBidi(TestTemplate):
    def test_arabic_indic_number_left_of_name(self):
        runs = pdf_fonts.split_runs("محمد ١٢٣")
        assert [r.text for r in runs] == ["١٢٣", " ", "محمد"]
        digits = runs[0]
        assert digits.level == 2
        assert not digits.rtl  # shaped left-to-right: reads ١٢٣

    def test_european_number_stays_inside_rtl_span(self):
        assert _drawn("شارع 12 الملك") == ["الملك", " ", "12", " ", "شارع"]

    def test_parenthesized_rtl_drawn_as_one_span(self):
        # Whole line is RTL: drawing order is the reverse of logical order,
        # and HarfBuzz mirrors the parentheses inside the RTL runs.
        runs = pdf_fonts.split_runs("محمد (علي)")
        assert [r.text for r in runs] == [")", "علي", " (", "محمد"]
        assert all(r.rtl for r in runs)

    def test_rtl_name_inside_latin_line(self):
        assert _drawn("by محمد علي on") == ["by ", "علي", " ", "محمد", " on"]
