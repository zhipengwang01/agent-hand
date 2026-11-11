# -*- coding: utf-8 -*-
"""Windows 平台探针（ctypes，零第三方依赖）。

非 Windows 平台（云端自检）下所有探针返回 None/空，模块可安全 import。
每个探针失败都返回 None（=未知），绝不抛异常冒充状态——状态如实上报是硬指标。
"""
import os
import sys

IS_WIN = sys.platform.startswith("win")

if IS_WIN:
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32


def current_session_id():
    """当前进程所在会话 ID；非 Windows 返回 None。"""
    return _session_of_self()


def _session_of_self():
    if not IS_WIN:
        return None
    sid = wintypes.DWORD(0)
    try:
        if _kernel32.ProcessIdToSessionId(_kernel32.GetCurrentProcessId(), ctypes.byref(sid)):
            return sid.value
    except Exception:
        pass
    return None


def foreground_window():
    """前台窗口 {hwnd, title}；无前台或失败返回 None。"""
    if not IS_WIN:
        return None
    try:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return None
        buf = ctypes.create_unicode_buffer(512)
        _user32.GetWindowTextW(hwnd, buf, 512)
        return {"hwnd": int(hwnd), "title": buf.value}
    except Exception:
        return None


def last_input_age_sec():
    """距最后一次键鼠输入的秒数（GetLastInputInfo）；失败返回 None。"""
    if not IS_WIN:
        return None

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    try:
        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if not _user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        now = _kernel32.GetTickCount()
        return max(0.0, (now - info.dwTime) / 1000.0)
    except Exception:
        return None


def is_locked():
    """锁屏检测，双信号任一命中即 True；都不可用返回 None。

    信号 1：LogonUI.exe 进程在跑（锁屏界面宿主）。
    信号 2：OpenInputDesktop 拿到的桌面名为 Winlogon（安全桌面）。
    历史教训：锁屏下截屏按设计无解，状态机必须先如实报锁屏，不许重试误诊。
    """
    if not IS_WIN:
        return None
    votes = []
    # 信号 1：进程枚举（Toolhelp 快照）
    try:
        TH32CS_SNAPPROCESS = 0x00000002

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                        ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.POINTER(wintypes.ULONG)),
                        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                        ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

        snap = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap != -1:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            found = False
            if _kernel32.Process32FirstW(snap, ctypes.byref(entry)):
                while True:
                    if entry.szExeFile.lower() == "logonui.exe":
                        found = True
                        break
                    if not _kernel32.Process32NextW(snap, ctypes.byref(entry)):
                        break
            _kernel32.CloseHandle(snap)
            votes.append(found)
    except Exception:
        pass
    # 信号 2：输入桌面名
    try:
        GENERIC_READ = 0x80000000
        desk = _user32.OpenInputDesktop(0, False, GENERIC_READ)
        if desk:
            name = ctypes.create_unicode_buffer(256)
            needed = wintypes.DWORD(0)
            _user32.GetUserObjectInformationW(desk, 2, name, 256 * 2, ctypes.byref(needed))  # UOI_NAME=2
            _user32.CloseDesktop(desk)
            votes.append(name.value.lower() == "winlogon")
        else:
            # 打不开输入桌面通常正是因为处在安全桌面（锁屏）
            votes.append(True)
    except Exception:
        pass
    if not votes:
        return None
    return any(votes)


def find_window_by_title(substring):
    """按标题子串（大小写不敏感）找可见窗口，返回 {hwnd, title, rect:[x,y,w,h]} 或 None。"""
    if not IS_WIN or not substring:
        return None
    needle = substring.lower()
    result = []

    class RECT(ctypes.Structure):
        _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                    ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

    EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _lparam):
        if not _user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(512)
        _user32.GetWindowTextW(hwnd, buf, 512)
        title = buf.value
        if title and needle in title.lower():
            r = RECT()
            if _user32.GetWindowRect(hwnd, ctypes.byref(r)):
                result.append({"hwnd": int(hwnd), "title": title,
                               "rect": [r.left, r.top, r.right - r.left, r.bottom - r.top]})
                return False
        return True

    try:
        _user32.EnumWindows(EnumProc(cb), 0)
    except Exception:
        return None
    return result[0] if result else None


def create_mutex(name):
    """命名互斥体实现单实例；返回 (handle, already_exists)。非 Windows 返回 (None, False)。"""
    if not IS_WIN:
        return None, False
    try:
        ERROR_ALREADY_EXISTS = 183
        _kernel32.SetLastError(0)
        handle = _kernel32.CreateMutexW(None, True, name)
        exists = _kernel32.GetLastError() == ERROR_ALREADY_EXISTS
        return handle, exists
    except Exception:
        return None, False
