# -*- coding: utf-8 -*-
"""编码器/帧策略自检：MJPEG 帧为合法 JPEG、签名差分、静止降帧/变化升帧。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import capture
from PIL import Image


class TestEncoder(unittest.TestCase):
    def test_mjpeg_frame_is_jpeg(self):
        enc = capture.MjpegEncoder(quality=60)
        data = enc.encode(Image.new("RGB", (32, 24), (10, 20, 30)))
        self.assertTrue(data.startswith(b"\xff\xd8"))  # JPEG SOI
        self.assertTrue(data.endswith(b"\xff\xd9"))    # JPEG EOI

    def test_signature_diff(self):
        a = Image.new("RGB", (16, 16), (100, 100, 100))
        b = Image.new("RGB", (16, 16), (100, 100, 100))
        c = Image.new("RGB", (16, 16), (200, 200, 200))
        sa, sb, sc = (capture.frame_signature(x) for x in (a, b, c))
        self.assertEqual(capture.signature_diff(sa, sb), 0.0)
        self.assertGreater(capture.signature_diff(sa, sc), 50)
        self.assertEqual(capture.signature_diff(None, sa), 255.0)

    def test_stream_policy_idle_and_active(self):
        pol = capture.StreamPolicy(fps_max=10, fps_idle=2, diff_threshold=2.0, idle_after=1.0)
        sig_a = bytes([100] * 256)
        sig_b = bytes([200] * 256)
        # 首帧算变化 → 高帧率间隔
        self.assertAlmostEqual(pol.next_interval(sig_a, now=100.0), 0.1)
        # 静止未满 idle_after → 仍高帧率
        self.assertAlmostEqual(pol.next_interval(sig_a, now=100.5), 0.1)
        # 静止超时 → 降到 idle
        self.assertAlmostEqual(pol.next_interval(sig_a, now=102.0), 0.5)
        # 再变化 → 立刻回到高帧率
        self.assertAlmostEqual(pol.next_interval(sig_b, now=102.1), 0.1)

    def test_downscale(self):
        img = Image.new("RGB", (200, 100))
        out = capture.downscale(img, 100)
        self.assertEqual((out.width, out.height), (100, 50))
        same = capture.downscale(img, 500)
        self.assertEqual(same.width, 200)


if __name__ == "__main__":
    unittest.main()
