# -*- coding: utf-8 -*-
"""坐标线输入：剪贴板（CF_UNICODETEXT）＋ SendInput 键鼠，ctypes 实现，零第三方依赖。

paste_text 是默认输入法（设计稿：剪贴板粘贴代替逐字敲）：
  set_clipboard_text → 聚焦目标 → Ctrl+V。
非 Windows 环境调用即抛 RuntimeError（云端自检不会真调用，只走协议层）。
"""
import sys
import time

IS_WIN = sys.platform.startswith("win")

if IS_WIN:
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32


def _require_win():
    if not IS_WIN:
        raise RuntimeError("wininput 只能在 Windows 目标机运行")


def set_clipboard_text(text):
    _require_win()
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    data = (text + "\0").encode("utf-16-le")
    for _ in range(5):  # 剪贴板可能被别的进程短暂占用，重试几次
        if _user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("打不开剪贴板")
    try:
        _user32.EmptyClipboard()
        # 64 位下必须声明指针返回类型，否则 GlobalLock 返回值被截断为 32 位 int，
        # memmove 写截断指针必崩（2026-11-11 笔记本真机实证：access violation writing 0x0）
        _kernel32.GlobalAlloc.restype = ctypes.c_void_p
        _kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
        _kernel32.GlobalLock.restype = ctypes.c_void_p
        _kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        _kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        _user32.SetClipboardData.restype = ctypes.c_void_p
        _user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
        h = _kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        ptr = _kernel32.GlobalLock(h)
        ctypes.memmove(ptr, data, len(data))
        _kernel32.GlobalUnlock(h)
        if not _user32.SetClipboardData(CF_UNICODETEXT, h):
            raise RuntimeError("SetClipboardData 失败")
    finally:
        _user32.CloseClipboard()


def _send_key(vk, key_up=False):
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_UNICODE = 0x0004
    flags = KEYEVENTF_KEYUP if key_up else 0
    if vk is None:
        flags |= KEYEVENTF_UNICODE
    _user32.keybd_event(vk or 0, 0, flags, 0)


def press_combo(keys):
    """keys 如 ['ctrl','v'] 或 ['enter']：按下修饰键→主键→逆序释放。"""
    _require_win()
    VK = {"ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B,
          "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
          "backspace": 0x08, "delete": 0x2E, "space": 0x20,
          "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
          "a": 0x41, "c": 0x43, "v": 0x56, "x": 0x58, "z": 0x5A, "s": 0x53}
    vks = []
    for k in keys:
        lk = k.strip().lower()
        if lk in VK:
            vks.append(VK[lk])
        elif len(lk) == 1 and lk.isalpha():
            vks.append(ord(lk.upper()))
        elif len(lk) == 1 and lk.isdigit():
            vks.append(ord(lk))
        else:
            raise RuntimeError("不支持的按键: %s" % k)
    for vk in vks:
        _send_key(vk, False)
    for vk in reversed(vks):
        _send_key(vk, True)


def paste_text(text):
    """剪贴板粘贴：写入剪贴板后发 Ctrl+V。返回粘贴字符数。"""
    set_clipboard_text(text)
    time.sleep(0.05)
    press_combo(["ctrl", "v"])
    return len(text)


def mouse_click(x, y):
    _require_win()
    _user32.SetCursorPos(int(x), int(y))
    time.sleep(0.03)
    _user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
    _user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
