"""CLI integration tests using deterministic, synthetic media only.

The geometric fixtures and the three color-only video windows were visually
reviewed during development. Their labels are test data, not auto-annotation.
Run with a Python environment containing Pillow and FFmpeg/ffprobe on PATH.
"""

import html
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

SKILL = Path(__file__).resolve().parents[1]
PREPARE = SKILL / "scripts" / "prepare.py"
INDEX = SKILL / "scripts" / "index.py"
RED_PHOTO = "红色 方块.png"
RED_COPY = "红色 复制.png"
BLUE_PHOTO = '圆形 <img onerror="alert(1)"> & "图".png'
VIDEO = "颜色 切换.mp4"


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def draw_photo(path, shape):
    image = Image.new("RGB", (320, 200), "#eeeeee" if shape == "red" else "#112244")
    draw = ImageDraw.Draw(image)
    if shape == "red":
        draw.rectangle((90, 45, 230, 155), fill="#cc2233")
    elif shape == "blue":
        draw.ellipse((95, 35, 225, 165), fill="#2299ee")
    elif shape == "yellow":
        draw.polygon([(160, 30), (250, 170), (70, 170)], fill="#ffdd22")
    else:
        raise ValueError(shape)
    image.save(path)


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_dir = tempfile.TemporaryDirectory(prefix="media-index-fixtures-")
        cls.addClassCleanup(cls.fixture_dir.cleanup)
        cls.fixture = Path(cls.fixture_dir.name) / "素材 library"
        cls.fixture.mkdir()
        draw_photo(cls.fixture / RED_PHOTO, "red")
        shutil.copyfile(cls.fixture / RED_PHOTO, cls.fixture / RED_COPY)
        draw_photo(cls.fixture / BLUE_PHOTO, "blue")
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=red:s=160x100:r=10:d=12",
                "-f",
                "lavfi",
                "-i",
                "color=c=green:s=160x100:r=10:d=12",
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=160x100:r=10:d=2",
                "-filter_complex",
                "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
                "-map",
                "[v]",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(cls.fixture / VIDEO),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    def setUp(self):
        self.work_dir = tempfile.TemporaryDirectory(prefix="media-index-pipeline-")
        self.addCleanup(self.work_dir.cleanup)
        self.root = Path(self.work_dir.name)
        self.source = self.root / "素材 library"
        shutil.copytree(self.fixture, self.source)
        self.output = self.root / "检索 catalog"
        self.prepare()

    def command(self, script, *args, success=True):
        result = subprocess.run(
            [sys.executable, str(script), *map(str, args)],
            capture_output=True,
            text=True,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def prepare(self, *, output=None):
        return self.command(
            PREPARE,
            "--source",
            self.source,
            "--output",
            output or self.output,
            "--recursive",
            "--jobs",
            "2",
        )

    def index(self, *args, success=True):
        return self.command(INDEX, "--index", self.output, *args, success=success)

    def annotation(self, row):
        if row["kind"] == "video":
            color = {0: "红色", 12: "绿色", 24: "蓝色"}[row["start"]]
            return {
                "id": row["id"],
                "description": f"三张抽帧均为{color}色块。",
                "tags": [color, "色块"],
            }
        if row["filename"] == BLUE_PHOTO:
            return {
                "id": row["id"],
                "description": "深蓝背景上的蓝色圆形。",
                "tags": ["蓝色", "圆形"],
            }
        return {
            "id": row["id"],
            "description": "浅灰背景中央的红色矩形。",
            "tags": ["红色", "矩形"],
        }

    def annotate_all(self):
        annotations = [self.annotation(row) for row in read_json(self.output / "windows.json")]
        write_json(self.output / "annotations" / "reviewed.json", annotations)
        return annotations

    def build(self):
        self.annotate_all()
        return json.loads(self.index("build").stdout)

    def test_build_refuses_missing_annotations(self):
        self.assertEqual(len(json.loads(self.index("pending").stdout)), 6)
        result = self.index("build", success=False)
        self.assertIn("missing or invalid content annotation", result.stderr)
        self.assertFalse((self.output / "index.sqlite").exists())

    def test_annotations_cannot_override_source_path_or_time(self):
        for field, value in (("path", "/tmp/unrelated-source.png"), ("start", 999)):
            with self.subTest(field=field):
                annotations = self.annotate_all()
                annotations[0][field] = value
                write_json(self.output / "annotations" / "reviewed.json", annotations)
                result = self.index("build", success=False)
                self.assertIn("cannot override structural/unknown fields", result.stderr)
                self.assertIn(field, result.stderr)

    def test_cli_build_search_duplicates_timeline_and_html_escaping(self):
        annotations = self.annotate_all()
        payload = '<script>alert("fixture")</script> & "label"'
        annotations[0]["possible_uses"] = [payload]
        write_json(self.output / "annotations" / "reviewed.json", annotations)
        report = json.loads(self.index("build").stdout)
        self.assertEqual(
            (report["assets"], report["images"], report["videos"], report["segments"]), (4, 3, 1, 6)
        )
        self.assertEqual(report["exact_duplicate_files"], 1)
        self.assertEqual(json.loads(self.index("validate").stdout)["errors"], [])
        self.assertEqual(json.loads(self.index("pending").stdout), [])

        assets = read_json(self.output / "assets.json")
        duplicates = [asset for asset in assets if "duplicate_of" in asset]
        self.assertEqual(len(duplicates), 1)
        original = next(asset for asset in assets if asset["id"] == duplicates[0]["duplicate_of"])
        self.assertEqual(original["sha256"], duplicates[0]["sha256"])

        windows = read_json(self.output / "windows.json")
        video_windows = [row for row in windows if row["kind"] == "video"]
        self.assertEqual(
            [(row["start"], row["end"]) for row in video_windows], [(0, 12), (12, 24), (24, 26)]
        )
        for row in video_windows:
            self.assertEqual(len(row["previews"]), 3)
            for time, fraction in zip(row["sample_times"], (0.1, 0.5, 0.9)):
                self.assertAlmostEqual(time, row["start"] + (row["end"] - row["start"]) * fraction)

        circles = json.loads(self.index("search", "蓝色 圆形", "--kind", "image").stdout)
        self.assertEqual([row["filename"] for row in circles], [BLUE_PHOTO])
        red = json.loads(self.index("search", "红色 矩形", "--kind", "image").stdout)
        self.assertEqual(len(red), 2)
        tail = json.loads(
            self.index("search", "蓝色", "--kind", "video", "--max-duration", "2").stdout
        )
        self.assertEqual([(row["start"], row["end"]) for row in tail], [(24, 26)])
        self.assertEqual(
            json.loads(self.index("show", BLUE_PHOTO).stdout)[0]["filename"], BLUE_PHOTO
        )

        page = (self.output / "index.html").read_text(encoding="utf-8")
        self.assertNotIn(payload, page)
        self.assertIn(html.escape(payload), page)
        self.assertNotIn(BLUE_PHOTO, page)
        self.assertIn(html.escape(BLUE_PHOTO), page)

    def test_refresh_changes_only_modified_identity_and_blocks_stale_query(self):
        self.build()
        before = read_json(self.output / "windows.json")
        old = next(row for row in before if row["filename"] == RED_PHOTO)
        preserved = {
            row["id"]: (self.output / row["previews"][0]).stat().st_mtime_ns
            for row in before
            if row["filename"] != RED_PHOTO
        }
        draw_photo(self.source / RED_PHOTO, "yellow")
        self.prepare()
        after = read_json(self.output / "windows.json")
        updated = next(row for row in after if row["filename"] == RED_PHOTO)
        self.assertEqual(updated["asset_id"], old["asset_id"])
        self.assertNotEqual(updated["id"], old["id"])
        self.assertNotEqual(updated["source_sha256"], old["source_sha256"])
        self.assertEqual(
            {row["id"] for row in after if row["filename"] != RED_PHOTO}, set(preserved)
        )
        for row in after:
            if row["id"] in preserved:
                self.assertEqual(
                    (self.output / row["previews"][0]).stat().st_mtime_ns, preserved[row["id"]]
                )
        self.assertEqual(
            [row["id"] for row in json.loads(self.index("pending").stdout)], [updated["id"]]
        )
        self.assertIn("stale", self.index("search", "红色", success=False).stderr)
        self.assertIn(
            "missing or invalid content annotation", self.index("build", success=False).stderr
        )

        write_json(
            self.output / "annotations" / "update.json",
            [
                {
                    "id": updated["id"],
                    "description": "深蓝背景上的黄色三角形。",
                    "tags": ["黄色", "三角形"],
                }
            ],
        )
        self.index("build")
        self.assertEqual(json.loads(self.index("validate").stdout)["errors"], [])
        yellow = json.loads(self.index("search", "黄色 三角形", "--kind", "image").stdout)
        self.assertEqual([row["id"] for row in yellow], [updated["id"]])
        red = json.loads(self.index("search", "红色 矩形", "--kind", "image").stdout)
        self.assertEqual([row["filename"] for row in red], [RED_COPY])

    def test_validate_and_build_detect_source_changes_before_prepare(self):
        self.build()
        draw_photo(self.source / RED_PHOTO, "yellow")
        self.assertIn("source changed", self.index("validate", success=False).stdout)
        self.assertIn("source changed", self.index("build", success=False).stderr)

    def test_recursive_refresh_excludes_generated_output(self):
        self.output = self.source / "generated catalog"
        self.prepare()
        self.build()
        before = read_json(self.output / "scan.json")
        self.prepare()
        after = read_json(self.output / "scan.json")
        self.assertEqual((after["assets"], after["segments"]), (4, 6))
        self.assertEqual(before["scan_id"], after["scan_id"])
        self.assertEqual(after["pending"], 0)
        self.assertEqual(json.loads(self.index("validate").stdout)["errors"], [])

    def test_build_rejects_video_timeline_gap(self):
        self.annotate_all()
        windows = read_json(self.output / "windows.json")
        first_video = next(row for row in windows if row["kind"] == "video")
        first_video["start"] = 1
        write_json(self.output / "windows.json", windows)
        self.assertIn("incomplete window coverage", self.index("build", success=False).stderr)

    def add_low_fps_video(self, filename, duration):
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c=red:s=160x100:r=1:d={duration}",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(self.source / filename),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self.prepare()
        assets = read_json(self.output / "assets.json")
        windows = read_json(self.output / "windows.json")
        asset = next(row for row in assets if row["filename"] == filename)
        rows = [row for row in windows if row["asset_id"] == asset["id"]]
        return asset, rows

    def assert_valid_red_previews(self, rows):
        for row in rows:
            self.assertEqual(len(row["previews"]), 3)
            for relative in row["previews"]:
                with self.subTest(preview=relative):
                    path = self.output / relative
                    self.assertTrue(path.is_file())
                    with Image.open(path) as preview:
                        preview.verify()
                    with Image.open(path) as preview:
                        self.assertGreater(min(preview.size), 0)
                        red, green, blue = preview.convert("RGB").getpixel(
                            (preview.width // 2, preview.height // 2)
                        )
                    self.assertGreater(red, 200)
                    self.assertLess(green, 30)
                    self.assertLess(blue, 30)

    def test_low_fps_last_sample_uses_last_displayed_frame(self):
        asset, rows = self.add_low_fps_video("低帧率 26 秒.mp4", 26)
        self.assertEqual(asset["last_frame_time"], 25)
        self.assertEqual(
            [(row["start"], row["end"]) for row in rows], [(0, 12), (12, 24), (24, 26)]
        )
        self.assertAlmostEqual(rows[-1]["sample_times"][-1], 25.8)
        self.assertEqual(rows[-1]["preview_seek_times"][-1], 25)
        self.assert_valid_red_previews(rows)

    def test_single_frame_tail_window_keeps_targets_and_valid_previews(self):
        asset, rows = self.add_low_fps_video("末窗单帧 13 秒.mp4", 13)
        self.assertEqual(asset["last_frame_time"], 12)
        self.assertEqual([(row["start"], row["end"]) for row in rows], [(0, 12), (12, 13)])
        self.assertEqual(rows[-1]["sample_times"], [12.1, 12.5, 12.9])
        self.assertEqual(rows[-1]["preview_seek_times"], [12, 12, 12])
        self.assert_valid_red_previews(rows)


if __name__ == "__main__":
    unittest.main()
