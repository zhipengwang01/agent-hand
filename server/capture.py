# -*- coding: utf-8 -*-
"""抓屏与编码。

抓屏后端：Pillow ImageGrab（Windows 目标机）。编码器做成可替换模块：
  - v1：MjpegEncoder（JPEG 帧 + multipart 流，便于手机浏览器直接看）
  - v1.1 替换点：H264Encoder（DXGI 抓屏 + 硬件 H.264 + WebRTC），接口同为 encode(image)->bytes 帧
替换时只换 Encoder 类，stream 层不动（见 stream.py）。
"""
import io
import time


def grab_screen(bbox=None):
    """抓全屏或 bbox=[x,y,w,h] 矩形，返回 PIL Image（RGB）。非真机环境抛 RuntimeError。"""
    from PIL import ImageGrab
    if bbox is not None:
        x, y, w, h = bbox
        return ImageGrab.grab(bbox=(x, y, x + w, y + h))
    return ImageGrab.grab()


def downscale(img, max_width):
    """等比缩小到 max_width 以内；不放大。返回新图（或原图）。"""
    if max_width and img.width > max_width:
        ratio = max_width / img.width
        return img.resize((max_width, max(1, int(img.height * ratio))))
    return img


def frame_signature(img):
    """极小灰度签名（16x16 平均值序列），供静态检测/像素变化判定，便宜稳定。"""
    small = img.convert("L").resize((16, 16))
    return small.tobytes()


def signature_diff(a, b):
    """两签名平均绝对差（0-255）；长度不一致视为最大变化。"""
    if a is None or b is None or len(a) != len(b):
        return 255.0
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


class MjpegEncoder:
    """v1 编码器：一帧 = JPEG bytes。流层负责拼 multipart。"""

    name = "mjpeg"

    def __init__(self, quality=60):
        self.quality = quality

    def encode(self, img):
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=self.quality)
        return buf.getvalue()


class StreamPolicy:
    """屏幕流动/静帧率策略（纯逻辑，可单测）：静止降帧省电、变化升帧。

    喂入帧签名，返回当前应使用的帧间隔秒数。
    """

    def __init__(self, fps_max=10, fps_idle=2, diff_threshold=2.0, idle_after=2.0):
        self.interval_active = 1.0 / max(1, fps_max)
        self.interval_idle = 1.0 / max(1, fps_idle)
        self.diff_threshold = diff_threshold
        self.idle_after = idle_after
        self._last_sig = None
        self._last_change = 0.0

    def next_interval(self, sig, now=None):
        now = time.time() if now is None else now
        if signature_diff(self._last_sig, sig) >= self.diff_threshold:
            self._last_sig = sig
            self._last_change = now
            return self.interval_active
        if now - self._last_change >= self.idle_after:
            return self.interval_idle
        return self.interval_active
