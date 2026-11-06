import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "mediaindex", Path(__file__).resolve().parents[1] / "scripts" / "index.py"
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            dict(
                id="1",
                filename="food.MOV",
                kind="video",
                start=0,
                end=12,
                description="夜景摊位中的肉干细节",
            ),
            dict(
                id="2",
                filename="stall.jpg",
                kind="image",
                start=None,
                end=None,
                description="白天摊位全景",
            ),
            dict(
                id="3",
                filename="serve.MOV",
                kind="video",
                start=12,
                end=32,
                description="夜晚摊位试吃",
            ),
        ]

    def test_chinese_keywords_and_synonyms(self):
        self.assertEqual([r["id"] for r in m.search(self.rows, "夜晚 牛肉 特写")], ["1"])

    def test_and_or_and_missing(self):
        self.assertEqual(m.search(self.rows, "白天 试吃"), [])
        self.assertEqual(len(m.search(self.rows, "白天 试吃", match="any")), 2)
        self.assertEqual(m.search(self.rows, "不存在"), [])

    def test_filters(self):
        self.assertEqual([r["id"] for r in m.search(self.rows, "", kind="image")], ["2"])
        self.assertEqual([r["id"] for r in m.search(self.rows, "", max_duration=12)], ["1"])

    def test_validation_detects_duplicates_and_gaps(self):
        source = str(Path(__file__).resolve())
        a = dict(id="a", filename="test", kind="video", duration=24)
        r = dict(
            id="s",
            asset_id="a",
            kind="video",
            start=0,
            end=12,
            sample_times=[1, 6, 11],
            description="摊位",
            tags=["摊位"],
            path=source,
            previews=[],
        )
        errors = m.validate([r, r], [a])
        self.assertTrue(any("duplicate" in e for e in errors))
        self.assertTrue(any("coverage" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
