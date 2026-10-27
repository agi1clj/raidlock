import pytest

from raidlock.treasury import _measure, forecast


@pytest.mark.parametrize("value", [float("inf"), float("nan"), -float("inf")])
def test_nonfinite_aggregation_is_invalid(value):
    with pytest.raises(ValueError, match="invalid-data"):
        _measure([{"usd": value}], True)


def test_invalid_forecast_result():
    class Board:
        name = "invalid"

        def get_setting(self):
            return {}

        def spend(self, **kwargs):
            return {"rows": [{"usd": float("inf")}]}

    assert forecast(Board())["error"] == "invalid-data"
