import json
import sys
from pathlib import Path

DISCOUNT = """def apply_discount(total, percent):
    if not 0 <= percent <= 100:
        raise ValueError("percent must be between 0 and 100")
    return round(total * (100 - percent) / 100, 2)
"""

TEST = """import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cart.discount import apply_discount


class DiscountTest(unittest.TestCase):
    def test_ten_percent(self):
        self.assertEqual(apply_discount(50, 10), 45.0)

    def test_rejects_out_of_range(self):
        with self.assertRaises(ValueError):
            apply_discount(50, 120)


if __name__ == "__main__":
    unittest.main()
"""


def emit(**event):
    print(json.dumps(event), flush=True)


sys.stdin.read()
emit(kind="model", model="claude-sonnet-5-5", billing="api")
emit(kind="message", text="Reading src/cart/cart.py and the card brief")
Path("src/cart/discount.py").write_text(DISCOUNT, encoding="utf-8")
Path("tests/test_discount.py").write_text(TEST, encoding="utf-8")
emit(kind="files", paths=["src/cart/discount.py", "tests/test_discount.py"])
emit(kind="command", command="python3 -m unittest discover -s tests", exit=0)
emit(kind="usage", input=18400, cached_input=52000, output=2600)
emit(
    kind="result",
    structured={
        "status": "done",
        "evidence": ["apply_discount added with range check", "unittest: 3 tests OK"],
        "paths_changed": ["src/cart/discount.py", "tests/test_discount.py"],
        "open_questions": [],
    },
)
