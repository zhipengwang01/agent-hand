# -*- coding: utf-8 -*-
"""批量动作协议＋执行器。

协议（JSON）：POST /actions 的 body：
  {"script": [ {动作}, ... ], "stop_on_error": true}
每个动作是一个对象，"action" 字段定类型：

  cdp_connect {url}                       连 Edge/Chrome 调试端口（如 http://127.0.0.1:9222）
  navigate    {url}                       CDP 导航                       [改变状态]
  find_click  {selector|text}             跨帧查找并点击（可穿 iframe）    [改变状态]
  click_at    {x, y}                      坐标线点击                     [改变状态]
  paste_text  {text}                      剪贴板粘贴（默认输入法）        [改变状态]
  type_text   {text}                      CDP Input.insertText 输入      [改变状态]
  set_files   {selector, files[]}         给文件输入框塞文件（不弹系统框）[改变状态]
  key         {key}                       如 "Enter"、"ctrl+v"          [改变状态]
  wait_for    {selector|text|pixel_diff, timeout_ms}  本地等待，不回云端轮询
  shot        {window|rect|selector, max_width}       圈定截图取证（硬规则见 scope.py）

执行语义：顺序执行；stop_on_error=true 时首个失败即停。每动作回
  {"action":..., "ok":bool, "detail":"...", "data":{...}|null, "evidence":"文件名"|null}
observe_only=true 时 [改变状态] 类动作一律拒绝（OBSERVE_ONLY）——新能力先观察后放权。
锁屏时 shot/输入类动作直接报 LOCKED，不重试（历史教训：锁屏截屏按设计无解）。
"""
import hashlib
import os
import time

import capture
import scope as scope_mod
import winutil
from cdp import CDPBridge, CDPError


class ProtocolError(Exception):
    def __init__(self, message, index=None):
        super().__init__(message)
        self.index = index


# action -> (必填字段列表, 是否改变机器状态)
ACTION_SCHEMA = {
    "cdp_connect": (["url"], False),
    "navigate": (["url"], True),
    "find_click": ([], True),          # selector/text 二选一，下面单独校验
    "click_at": (["x", "y"], True),
    "paste_text": (["text"], True),
    "type_text": (["text"], True),
    "set_files": (["selector", "files"], True),
    "key": (["key"], True),
    "wait_for": ([], False),           # selector/text/pixel_diff 三选一
    "shot": ([], False),               # 圈定校验走 scope.extract_scope
}

MUTATING = {name for name, (_, mut) in ACTION_SCHEMA.items() if mut}


def validate_script(script):
    """纯校验（无副作用）：结构、动作名、必填、二选一约束、shot 圈定。返回归一化后的列表。"""
    if not isinstance(script, list) or not script:
        raise ProtocolError("script 必须是非空数组")
    out = []
    for i, act in enumerate(script):
        if not isinstance(act, dict) or "action" not in act:
            raise ProtocolError("每个动作必须是带 action 字段的对象", i)
        name = act["action"]
        if name not in ACTION_SCHEMA:
            raise ProtocolError("未知动作: %s" % name, i)
        required, _ = ACTION_SCHEMA[name]
        for field in required:
            if field not in act:
                raise ProtocolError("动作 %s 缺必填字段 %s" % (name, field), i)
        if name == "find_click" and not (act.get("selector") or act.get("text")):
            raise ProtocolError("find_click 需要 selector 或 text 之一", i)
        if name == "wait_for" and not (act.get("selector") or act.get("text") or act.get("pixel_diff")):
            raise ProtocolError("wait_for 需要 selector / text / pixel_diff 之一", i)
        if name == "shot":
            try:
                scope_mod.extract_scope(act)
            except scope_mod.ScopeError as e:
                raise ProtocolError("%s: %s" % (e.code, e.message), i)
        out.append(dict(act))
    return out


class Executor:
    """依赖注入式执行器：cdp/grab/paste 等可替换，云端用假件跑自检。"""

    def __init__(self, cfg, audit, cdp_bridge=None, grabber=None, paster=None, keyer=None, clicker=None):
        self.cfg = cfg
        self.audit = audit
        self.cdp = cdp_bridge if cdp_bridge is not None else CDPBridge()
        self.grabber = grabber or capture.grab_screen
        self._paster = paster
        self._keyer = keyer
        self._clicker = clicker
        self.evidence_dir = os.path.join(cfg["data_dir"], "evidence")
        os.makedirs(self.evidence_dir, exist_ok=True)
        self._last_shot_sha = None  # v0.2 B3: 上一证据帧 sha256，用于 stale 判定

    # -- 惰性真机输入（云端不 import wininput 的调用路径） --
    def _paste(self, text):
        if self._paster:
            return self._paster(text)
        import wininput
        return wininput.paste_text(text)

    def _press(self, key_name):
        if self._keyer:
            return self._keyer(key_name)
        import wininput
        return wininput.press_combo([p.strip() for p in key_name.split("+")])

    def _click_at(self, x, y):
        if self._clicker:
            return self._clicker(x, y)
        import wininput
        return wininput.mouse_click(x, y)

    def _locked(self):
        try:
            return winutil.is_locked()
        except Exception:
            return None

    # -- 主入口 --
    def run(self, script, stop_on_error=True):
        actions = validate_script(script)
        results = []
        all_ok = True
        for i, act in enumerate(actions):
            res = self._run_one(act)
            results.append(res)
            if not res["ok"]:
                all_ok = False
                if stop_on_error:
                    return {"ok": False, "results": results, "stopped_at": i}
        return {"ok": all_ok, "results": results, "stopped_at": None}

    def _run_one(self, act):
        name = act["action"]
        target = act.get("selector") or act.get("text") or act.get("url") or act.get("window") or ""
        try:
            if self.cfg.get("observe_only") and name in MUTATING:
                raise _Fail("OBSERVE_ONLY", "观察模式：拒绝改变状态的动作 %s（验收后改 config 放行）" % name)
            handler = getattr(self, "_do_" + name)
            data, evidence = handler(act)
            self.audit.record(name, str(target), True, "", evidence)
            return {"action": name, "ok": True, "detail": "", "data": data, "evidence": evidence}
        except _Fail as e:
            self.audit.record(name, str(target), False, "%s: %s" % (e.code, e.message))
            return {"action": name, "ok": False, "detail": "%s: %s" % (e.code, e.message),
                    "data": None, "evidence": None}
        except Exception as e:  # 真机异常如实回，不吞
            self.audit.record(name, str(target), False, "ERROR: %r" % e)
            return {"action": name, "ok": False, "detail": "ERROR: %r" % e, "data": None, "evidence": None}

    # -- 各动作 --
    def _do_cdp_connect(self, act):
        info = self.cdp.connect(act["url"])
        return info, None

    def _do_navigate(self, act):
        return self.cdp.navigate(act["url"]), None

    def _do_find_click(self, act):
        sid, hit = self.cdp.find(selector=act.get("selector"), text=act.get("text"))
        if not hit:
            raise _Fail("NOT_FOUND", "主文档与全部 iframe 均未找到目标")
        res = self.cdp.click_found(sid, hit)
        if not res.get("clicked"):
            raise _Fail("CLICK_FAILED", "找到元素但点击失败: %r" % hit)
        return {"hit": hit, "click": res}, None

    def _do_click_at(self, act):
        self._assert_unlocked()
        self._click_at(act["x"], act["y"])
        return {"x": act["x"], "y": act["y"]}, None

    def _do_paste_text(self, act):
        self._assert_unlocked()
        n = self._paste(act["text"])
        return {"pasted_chars": n}, None

    def _do_type_text(self, act):
        self._assert_unlocked()
        return self.cdp.insert_text(act["text"]), None

    def _do_set_files(self, act):
        self._assert_unlocked()
        if not isinstance(act["files"], list) or not act["files"]:
            raise _Fail("BAD_PARAM", "files 必须是非空数组（目标机绝对路径）")
        return self.cdp.set_files(act["selector"], act["files"]), None

    def _do_key(self, act):
        self._assert_unlocked()
        # 优先 CDP（浏览器场景）；未连接 CDP 时走坐标线按键
        try:
            return self.cdp.key(act["key"]), None
        except CDPError:
            self._press(act["key"])
            return {"key": act["key"], "via": "wininput"}, None

    def _do_wait_for(self, act):
        # v0.2 B1: 单次 wait_for 上限 30s——内部 0.3s 短轮询，禁止一条调用长挂楔死桥；
        # 更久的等待由调用方分段重复下发（配合 B4 异步执行也不会堵住控制面）。
        timeout = min(act.get("timeout_ms") or 15000, 30000) / 1000.0
        deadline = time.time() + timeout
        if act.get("pixel_diff"):
            return self._wait_pixel(act, deadline), None
        while time.time() < deadline:
            sid, hit = self.cdp.find(selector=act.get("selector"), text=act.get("text"))
            if hit:
                return {"found": hit, "waited_ms": int((timeout - (deadline - time.time())) * 1000)}, None
            time.sleep(0.3)
        raise _Fail("WAIT_TIMEOUT", "等待 %.1fs 未出现目标" % timeout)

    def _wait_pixel(self, act, deadline):
        spec = act["pixel_diff"]
        rect = spec.get("rect")
        if not rect:
            raise _Fail("BAD_PARAM", "pixel_diff 需要 rect")
        threshold = float(spec.get("threshold", 8))
        base = capture.frame_signature(self.grabber(bbox=rect))
        while time.time() < deadline:
            time.sleep(0.3)
            sig = capture.frame_signature(self.grabber(bbox=rect))
            if capture.signature_diff(base, sig) >= threshold:
                return {"changed": True}
        raise _Fail("WAIT_TIMEOUT", "像素无显著变化")

    def _do_shot(self, act):
        if self._locked() is True:
            raise _Fail("LOCKED", "机器已锁屏，截屏按设计不可行（状态机已如实上报）")
        sc = scope_mod.extract_scope(act)
        words = self.cfg["sensitive_title_words"]
        max_w = act.get("max_width", 960)
        img = None
        if "window" in sc:
            win = winutil.find_window_by_title(sc["window"])
            if not win:
                raise _Fail("WINDOW_NOT_FOUND", "找不到标题含 %r 的窗口" % sc["window"])
            scope_mod.assert_not_sensitive(win["title"], words)
            img = self.grabber(bbox=win["rect"])
        elif "rect" in sc:
            fg = winutil.foreground_window()
            if fg:
                scope_mod.assert_not_sensitive(fg.get("title"), words)
            img = self.grabber(bbox=sc["rect"])
        else:  # selector → CDP 元素区域截图
            sid, hit = self.cdp.find(selector=sc["selector"])
            if not hit:
                raise _Fail("NOT_FOUND", "找不到元素 %s" % sc["selector"])
            # 元素在主视口坐标（find 已 scrollIntoView）；截图需元素宽高——再求值取 rect
            rect = self.cdp.client.evaluate(
                "(function(){var e=document.querySelector(%s); if(!e) return null;"
                " var r=e.getBoundingClientRect(); return {x:r.left,y:r.top,width:r.width,height:r.height};})()"
                % _js_str(sc["selector"]), sid,
                context_id=self.cdp.client.default_context(sid, timeout=1))
            if not rect:
                raise _Fail("NOT_FOUND", "元素 rect 不可得")
            png = self.cdp.screenshot_clip({"x": rect["x"], "y": rect["y"],
                                            "width": rect["width"], "height": rect["height"], "scale": 1})
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(png)).convert("RGB")
        img = capture.downscale(img, max_w)
        fname = time.strftime("%Y%m%d-%H%M%S") + "-shot.jpg"
        path = os.path.join(self.evidence_dir, fname)
        img.save(path, format="JPEG", quality=70)
        # v0.2 B3: 帧新鲜度——与上一帧 sha256 相同标 stale_suspect（同帧复用/抓帧延迟嫌疑），
        # 调用方据此决定是否重取，不再把静态旧帧当新证据。
        with open(path, "rb") as f:
            sha = hashlib.sha256(f.read()).hexdigest()
        stale = (sha == self._last_shot_sha)
        self._last_shot_sha = sha
        return {"file": fname, "w": img.width, "h": img.height,
                "sha256": sha, "stale_suspect": stale}, path

    def _assert_unlocked(self):
        if self._locked() is True:
            raise _Fail("LOCKED", "机器已锁屏，输入类动作拒绝执行")


class _Fail(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _js_str(s):
    import json
    return json.dumps(s)
