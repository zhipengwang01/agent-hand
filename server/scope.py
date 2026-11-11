# -*- coding: utf-8 -*-
"""圈定规则（代码层硬限制，设计稿 §四最后一行）：

一切 AI 面向的读取（shot 截图取证）必须带圈定参数，三选一：
  - window:  窗口标题子串（截该窗口矩形）
  - rect:    [x, y, w, h] 屏幕矩形
  - selector: CDP 元素选择器（截元素区域）
没有圈定参数 → 直接拒绝（SCOPE_REQUIRED），任何调用方都绕不过。
另：目标窗口标题命中敏感词（密码/凭据类）→ 拒绝截图（SENSITIVE_WINDOW）。
注：屏幕流（MJPEG）是用户本人看自己屏幕的数据面，不属于 AI 读取，不走本规则（README 有说明）。
"""


class ScopeError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def extract_scope(action):
    """从 shot 动作里提取并校验圈定参数，返回归一化 dict；无圈定抛 ScopeError。"""
    scope = {}
    if action.get("window"):
        scope["window"] = str(action["window"])
    if action.get("rect"):
        rect = action["rect"]
        if (not isinstance(rect, (list, tuple)) or len(rect) != 4
                or not all(isinstance(v, (int, float)) for v in rect)
                or rect[2] <= 0 or rect[3] <= 0):
            raise ScopeError("SCOPE_INVALID", "rect 必须是 [x, y, w>0, h>0]")
        scope["rect"] = [int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])]
    if action.get("selector"):
        scope["selector"] = str(action["selector"])
    if not scope:
        raise ScopeError("SCOPE_REQUIRED", "截图必须圈定：window / rect / selector 三选一")
    if len(scope) > 1:
        raise ScopeError("SCOPE_INVALID", "圈定参数只能给一个：window / rect / selector")
    return scope


def is_sensitive_title(title, sensitive_words):
    if not title:
        return False
    low = title.lower()
    return any(w.lower() in low for w in sensitive_words)


def assert_not_sensitive(title, sensitive_words):
    if is_sensitive_title(title, sensitive_words):
        raise ScopeError("SENSITIVE_WINDOW", "目标窗口标题疑似敏感（密码/凭据类），拒绝截图")
