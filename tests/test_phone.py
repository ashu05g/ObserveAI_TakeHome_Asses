import pytest

from api.utils.phone import InvalidPhoneNumber, normalize_phone


class TestNormalizePhone:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("4155550001", "+14155550001"),
            ("415-555-0001", "+14155550001"),
            ("(415) 555-0001", "+14155550001"),
            ("415.555.0001", "+14155550001"),
            ("415 555 0001", "+14155550001"),
            ("+14155550001", "+14155550001"),
            ("14155550001", "+14155550001"),
            ("1-415-555-0001", "+14155550001"),
            ("  +1 (415) 555-0001  ", "+14155550001"),
        ],
    )
    def test_normalizes_common_us_formats(self, raw, expected):
        assert normalize_phone(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "123",
            "415-555",
            "abc",
            "555-0001",
            "0000000000000000",
            "+44 20 7946 0958",
        ],
    )
    def test_rejects_invalid_inputs(self, raw):
        with pytest.raises(InvalidPhoneNumber):
            normalize_phone(raw)

    def test_rejects_11_digit_non_us(self):
        with pytest.raises(InvalidPhoneNumber):
            normalize_phone("24155550001")

    def test_rejects_none(self):
        with pytest.raises(InvalidPhoneNumber):
            normalize_phone(None)
