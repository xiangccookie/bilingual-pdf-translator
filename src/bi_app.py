# -*- coding: utf-8 -*-
"""
双语 PDF 翻译器（图形界面） —— 选一个英文 PDF，生成「一句英文 + 紧跟中文」的双语 PDF

翻译来源：MyMemory 免费接口（api.mymemory.translated.net，无需 API key）。
注意它**每天有免费额度**，用尽会返回 429 并提示几小时后恢复；程序会把这句话原样保留英文。

界面：输入文件 / 输出文件 / 页码范围 / 每句间隔 / 开始、取消、打开输出目录 / 进度 / 日志。

命令行自检（不弹窗，用于验证打包是否完好）：
  双语PDF翻译器.exe --selftest 输入.pdf 输出.pdf
"""
import argparse
import hashlib
import json
import os
import queue
import re
import shutil
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request

# ---------------------------------------------------------------- 依赖
try:
    import pymupdf
except Exception:                                     # pragma: no cover
    pymupdf = None

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# 打包成「窗口模式」exe 后 sys.stdout 是 None，print() 会抛 AttributeError；
# 自检模式下用它兜底，避免打包版一跑就崩。
def _log_file_path():
    """日志路径：交给 local_engine 选一个**确实能写**的目录

    实测：打包版写 %LOCALAPPDATA% 会被拒（PermissionError），
    所以不能硬编码那个位置，否则日志一条也留不下。
    """
    try:
        import local_engine as _le
        return os.path.join(_le.app_data_dir(), "app.log")
    except Exception:
        return os.path.join(tempfile.gettempdir(), "BilingualPdfTranslator_app.log")


def _safe_log(*args, **kw):
    """打印到控制台（如果有）并**追加到日志文件**。

    窗口模式（--windowed）下没有控制台，出了问题用户看不到任何信息；
    有了这个日志文件，出问题可以直接把 %LOCALAPPDATA%\\BilingualPdfTranslator\\app.log 发过来。
    """
    msg = " ".join(str(a) for a in args)
    try:
        if sys.stdout is not None:
            print(*args, **kw)
    except Exception:
        pass
    try:
        with open(_log_file_path(), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\\n")
    except Exception:
        pass


# ---------------------------------------------------------------- 离线引擎（Argos）
# 为什么要在 import argostranslate 之前塞假模块：
#   argostranslate 的分句模块 sbd.py 顶部硬 import stanza 与 minisbd，
#   而 stanza 又会拉 torch（约 164 MB）—— 只为「把段落切成句子」。
#   我们本来就自己切句（pdf_bilingual.split_sentences），因此：
#     ① 把三种分句器的方法替换成不联网的极简实现；
#     ② 提前放两个空壳模块，避免真去 import stanza / minisbd / torch。
#   实测效果：能正常翻译，且不必打包 torch。
def _stub_sbd_deps():
    import types
    if "stanza" not in sys.modules:
        m = types.ModuleType("stanza")

        class _NoPipeline:
            def __init__(self, *a, **k):
                raise RuntimeError("stanza 已被跳过（本程序自己分句）")

        m.Pipeline = _NoPipeline
        m.download = lambda *a, **k: None
        sys.modules["stanza"] = m
    if "minisbd" not in sys.modules:
        mb = types.ModuleType("minisbd")
        mb.SBDetect = None
        mm = types.ModuleType("minisbd.models")
        mm.cache_dir = ""
        mm.list_models = lambda: []
        mb.models = mm
        sys.modules["minisbd"] = mb
        sys.modules["minisbd.models"] = mm


def _simple_split(self, text):
    """不联网的极简分句（替换 argostranslate 自带的分句器）"""
    t = (text or "").strip()
    if not t:
        return []
    parts = [p for p in re.split(r"(?<=[.!?。！？])\s+", t) if p.strip()]
    return parts or [t]


def _app_root():
    """资源根目录：打成单文件 exe 后是解包临时目录；直接跑源码时是本文件所在目录"""
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def bundled_model_path():
    """随程序一起打包的离线模型文件（首次运行时自动装进用户目录）"""
    for name in ("translate-en_zh.argosmodel", "translate-en_zh-1_9.argosmodel"):
        p = os.path.join(_app_root(), "assets", name)
        if os.path.exists(p):
            return p
    return ""


def _data_dir():
    """程序自己的数据目录（放解压后的离线模型、日志）"""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "BilingualPdfTranslator")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        d = os.path.dirname(os.path.abspath(sys.argv[0]))
    return d


def _prepare_argos_data(log=None):
    """让 argostranslate 用我们自己的数据目录；缺模型时把随程序附带的模型解压进去

    为什么不用 argostranslate 自己的 installer：
      · 它默认的数据目录五花八门（XDG / ~/.local/share / 平台差异），不好预期；
      · 实测在打包环境里调 install_from_path 会静默失败、且不报错。
    自己解压最直白：.argosmodel 就是个 zip，顶层已经是不带扩展名的包目录
    （translate-en_zh-1_9/），直接解到 <数据目录>/packages/ 就是它的规范布局。
    """
    log = log or (lambda s: None)
    _stub_sbd_deps()
    try:
        import pathlib
        from argostranslate import settings
        # ⚠ 关键：argostranslate 真正读的是 settings.package_dirs（一个列表）。
        #   settings.data_dir 只在 import 时用来算默认值，事后改它**没有效果**——
        #   实测改 data_dir 后仍然去 ~/.local/share/argos-translate 找模型，于是
        #   「明明解压好了却说没装」。四个属性一起设，才稳。
        data = pathlib.Path(_data_dir()) / "argos"
        packages = data / "packages"
        settings.data_dir = data
        settings.package_data_dir = packages
        settings.legacy_package_data_dir = packages
        settings.package_dirs = [packages]
    except Exception as e:
        log("离线引擎初始化失败：%s" % e)
        return ""
    packages = str(packages)
    try:
        os.makedirs(packages, exist_ok=True)
    except Exception:
        return ""
    src = bundled_model_path()
    marker = os.path.join(packages, "translate-en_zh-1_9", "metadata.json")
    if src and not os.path.exists(marker):
        log("首次使用：正在解压随程序附带的离线模型（约 82 MB，只做一次）…")
        try:
            import zipfile
            with zipfile.ZipFile(src) as z:
                z.extractall(packages)
            log("离线模型就绪。")
        except Exception as e:
            log("解压离线模型失败：%s" % e)
    return data


def _model_installed():
    """本机是否**真的装好**了 en→zh（只看已装的语言包，不看程序里带没带）"""
    try:
        _prepare_argos_data()
        _stub_sbd_deps()
        from argostranslate import translate as at
        langs = {L.code: L for L in at.get_installed_languages()}
        return ("en" in langs and "zh" in langs
                and langs["en"].get_translation(langs["zh"]) is not None)
    except Exception:
        return False


def ensure_local_model(log=None, allow_download=False):
    """确保本机有英→中离线模型；返回 True 表示可用

    顺序：
      ① 已经装过 → 直接用
      ② 程序里带了模型文件（assets/*.argosmodel）→ 自动装到用户目录（**无需联网**）
      ③ allow_download=True 时才去官方索引下载（约 67.5 MB，只需一次）
    """
    log = log or (lambda s: None)
    # 先把数据目录准备好（缺模型就从随身模型解压），再判断
    _prepare_argos_data(log)
    if _model_installed():
        return True
    try:
        _stub_sbd_deps()
        from argostranslate import package as apkg
    except Exception as e:
        log("离线引擎不可用：%s" % e)
        return False
    p = bundled_model_path()
    if p:
        log("首次使用：正在安装随程序附带的离线模型（约 67.5 MB，只做一次）…")
        try:
            apkg.install_from_path(p)
            log("离线模型安装完成。")
            if argos_available():
                return True
        except Exception as e:
            log("安装附带模型失败：%s" % e)
    if allow_download:
        log("正在从官方索引下载英→中模型（约 67.5 MB）…")
        try:
            apkg.update_package_index()
            hit = [x for x in apkg.get_available_packages()
                   if x.from_code == "en" and x.to_code == "zh"]
            if not hit:
                log("官方索引里没有 en→zh 的包。")
                return False
            apkg.install_from_path(hit[0].download())
            log("下载并安装完成。")
            return argos_available()
        except Exception as e:
            log("下载失败：%s" % e)
    log("没有离线模型：可改用「在线免费（MyMemory）」，或点「安装离线模型」联网下载一次。")
    return False


def argos_available():
    """本机是否**真能**离线翻译（存在 en→zh 的翻译路径，或程序里带了模型文件）

    注意：不能只判断「en、zh 两个语言代码在不在」——Argos 会顺带装上
    en→en、zh→zh 这类空转包，删掉 en→zh 主模型后两个代码仍然在。
    实测就是因此把「模型没了」误判成「可用」，自动安装逻辑根本没机会执行。
    """
    try:
        _stub_sbd_deps()
        from argostranslate import translate as at
        langs = {L.code: L for L in at.get_installed_languages()}
        if "en" in langs and "zh" in langs:
            if langs["en"].get_translation(langs["zh"]) is not None:
                return True
    except Exception:
        return False
    return bool(bundled_model_path())


class LocalTranslator:
    """本地离线翻译（Argos Translate）。接口与在线 Translator 保持一致。"""

    def __init__(self, cache_path, log=None, stop=None):
        _stub_sbd_deps()
        _prepare_argos_data(log)
        from argostranslate import sbd
        for name in ("SpacySentencizerSmall", "MiniSBDSentencizer", "StanzaSentencizer"):
            cls = getattr(sbd, name, None)
            if cls is not None and hasattr(cls, "split_sentences"):
                cls.split_sentences = _simple_split
        from argostranslate import translate as at
        from argostranslate import package as apkg
        self.log = log or (lambda s: None)
        self.stop = stop or (lambda: False)
        langs = {L.code: L for L in at.get_installed_languages()}
        if "en" not in langs or "zh" not in langs:
            # 没装过 → 先用程序里附带的模型自动装（无需联网）；装不上才报错
            if not ensure_local_model(self.log):
                raise RuntimeError(
                    "本地还没有英→中模型，且程序里也没带模型文件。\n"
                    "请点「安装离线模型」联网下载一次（约 67.5 MB），"
                    "或改用「在线免费（MyMemory）」。")
            langs = {L.code: L for L in at.get_installed_languages()}
        self._tr = langs["en"].get_translation(langs["zh"])
        if self._tr is None:
            raise RuntimeError("没有 en→zh 的翻译路径")
        self.cache_path = cache_path
        self.cache = {}
        if os.path.exists(cache_path):
            try:
                self.cache = json.load(open(cache_path, encoding="utf-8"))
            except Exception:
                self.cache = {}
        self.ok = self.hit = self.fail = 0
        self.quota = False            # 离线模式没有额度概念，保留字段以兼容

    def one(self, text):
        key = hashlib.sha1(text.encode("utf-8")).hexdigest()
        if key in self.cache:
            self.hit += 1
            return self.cache[key]
        try:
            zh = tidy(self._tr.translate(text))
            if not zh:
                raise ValueError("空译文")
            if not numbers_kept(text, zh):
                # 数字对不上就保留原文，绝不给出可能错误的数字
                self.fail += 1
                return text
            self.cache[key] = zh
            self.ok += 1
            return zh
        except Exception as e:
            self.log("  本地翻译失败（%s），本句保留英文。" % type(e).__name__)
            self.fail += 1
            return text

    def save(self):
        try:
            json.dump(self.cache, open(self.cache_path, "w", encoding="utf-8",
                                       newline="\n"), ensure_ascii=False)
        except Exception:
            pass

try:
    import glossary as _gloss          # 专业术语表（生物医学）
except Exception:
    _gloss = None

try:
    import pdf_bilingual as pb                        # 同目录随包附带的排版模块
except Exception:
    pb = None


# ================================================================ 翻译后端
API = "https://api.mymemory.translated.net/get"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def tidy(zh):
    z = str(zh).replace("\u2581", "")   # 去掉句子片段符 ▁（模型输出残留）
    z = zh
    z = re.sub(r"[（(]\s+", "（", z)
    z = re.sub(r"\s+[）)]", "）", z)
    z = re.sub(r"\|\s*([A-Za-z0-9_.\-]+)\s*\|", r"|\1|", z)
    z = re.sub(r"([\u4e00-\u9fff])\s+([\u4e00-\u9fff])", r"\1\2", z)
    z = z.replace("\u2047", "").replace("\u25c1", "")   # 去掉模型偶发的怪字符 ⁇ / ◁
    z = re.sub(r"\s+([，。；：、）])", r"\1", z)
    return z.strip()


NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


def numbers_kept(en, zh):
    nums = NUM_RE.findall(en)
    return all(n in zh for n in nums) if nums else True


class Translator:
    """MyMemory 逐句翻译：磁盘缓存 + 数字校验 + 退避重试 + 额度用尽即停"""

    def __init__(self, cache_path, sleep=0.3, email="", log=None, stop=None):
        self.cache_path = cache_path
        self.sleep = sleep
        self.email = email
        self.log = log or (lambda s: None)
        self.stop = stop or (lambda: False)
        self.cache = {}
        if os.path.exists(cache_path):
            try:
                self.cache = json.load(open(cache_path, encoding="utf-8"))
            except Exception:
                self.cache = {}
        self.ok = self.hit = self.fail = 0
        self.quota = False

    def _save(self):
        try:
            json.dump(self.cache, open(self.cache_path, "w", encoding="utf-8",
                                       newline="\n"), ensure_ascii=False)
        except Exception:
            pass

    def one(self, text):
        key = hashlib.sha1(text.encode("utf-8")).hexdigest()
        if key in self.cache:
            self.hit += 1
            return self.cache[key]
        # 额度已确认用尽 → 直接保留英文，不再逐句重试（否则一篇要白等十几分钟）
        if self.quota:
            self.fail += 1
            return text
        for attempt in range(3):
            if self.stop():
                return text
            try:
                params = {"q": text, "langpair": "en|zh-CN"}
                if self.email:
                    params["de"] = self.email
                url = API + "?" + urllib.parse.urlencode(params)
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=25) as r:
                    j = json.loads(r.read().decode("utf-8"))
                if j.get("quotaFinished"):
                    self.quota = True
                    self.log("⚠ MyMemory 免费额度已用尽（约 4 小时后恢复），本句保留英文。")
                    return text
                zh = tidy(j["responseData"]["translatedText"])
                if not zh:
                    continue
                if not numbers_kept(text, zh):
                    time.sleep(self.sleep)
                    continue
                self.cache[key] = zh
                self.ok += 1
                time.sleep(self.sleep)
                return zh
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    self.quota = True
                    body = ""
                    try:
                        body = e.read().decode("utf-8", "replace")
                    except Exception:
                        pass
                    m = re.search(r"NEXT AVAILABLE IN\s+(\d+)\s*HOURS?(?:\s+(\d+)\s*MINUTES?)?",
                                  body, re.I)
                    tip = ""
                    if m:
                        tip = "，约 %s 小时后恢复" % m.group(1)
                        if m.group(2):
                            tip = "，约 %s 小时 %s 分后恢复" % (m.group(1), m.group(2))
                    self.log("⚠ MyMemory 返回 429：今日免费额度已用尽%s。之后的句子将保留英文。" % tip)
                    break                       # 别再无谓重试，直接停
                else:
                    self.log("  请求失败 HTTP %s，重试…" % e.code)
                time.sleep(self.sleep * (attempt + 2))
            except Exception as e:
                self.log("  请求失败（%s），重试…" % type(e).__name__)
                time.sleep(self.sleep * (attempt + 2))
        self.fail += 1
        return text


# ================================================================ 流程
FRONT_RE = re.compile(
    r"^(OPEN ACCESS|OPEN$|ARTICLE|EDITED BY|REVIEWED BY|\*CORRESPONDENCE|CITATION|TYPE\s|DOI\s|"
    r"https?://doi\.org|https?://www\.|www\.nature\.com|RECEIVED\s|ACCEPTED\s|PUBLISHED[:\s]|"
    r"Check for updates|©|This is an open access article|Received:|Accepted:|Published:)", re.I)


def norm_footer(t):
    s = re.sub(r"[\s\u00a0]+", " ", t).strip()
    return re.sub(r"[·\-|/\s]*\b\d{1,4}$", "", s).strip()


def trim_pdf(src, dst, start=None, end=None):
    doc = pymupdf.open(src)
    n = len(doc)
    s = max(1, start or 1)
    e = min(n, end or n)
    if s > 1 or e < n:
        doc.select(list(range(s - 1, e)))
    doc.save(dst)
    doc.close()
    return e - s + 1


def prepare_blocks(pdf_path, workdir, log):
    """提取 + 清理 + 裁图（移植自 prepare_one.py 的通用部分）"""
    blocks, _ = pb.extract_blocks(pdf_path, None, progress=False)
    doc = pymupdf.open(pdf_path)
    npages = len(doc)
    H = float(doc[0].rect.height)

    rep = pb.detect_repeated_bands(blocks, 3, verbose=False)

    def assign(rs):
        for i, r in enumerate(rs):
            r["id"] = "B%03d" % (i + 1)
        return rs

    # 页脚（去掉尾部页码后同样的文字，出现在 ≥3 页顶/底带）
    keys = {}
    for b in blocks:
        k = norm_footer(b["text"])
        if k and len(k) <= 140 and (b["y0"] < 80 or b["y1"] > H - 60):
            keys.setdefault(k, set()).add(b["page"])
    footer_keys = {k for k, v in keys.items() if len(v) >= 3}

    recs = assign(pb.classify(blocks, rep, [], pb.MID_X))
    n_skip = 0
    for r in recs:
        t = r["text"].strip()
        if r["action"] == "skip":
            n_skip += 1
            continue
        if norm_footer(t) in footer_keys or (r["page"] == 1 and FRONT_RE.match(t)):
            r["action"], r["kind"] = "skip", "footer"
            n_skip += 1

    # 字号 → 小标题
    font_map = {}
    for pno, page in enumerate(doc, 1):
        items = []
        for blk in page.get_text("dict")["blocks"]:
            for ln in blk.get("lines", []):
                for s in ln["spans"]:
                    if s["text"].strip():
                        items.append((s["bbox"], round(s["size"], 1), s["font"]))
        font_map[pno] = items

    def block_font(r):
        sizes = []
        for bbox, size, font in font_map.get(r["page"], []):
            cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
            if r["x0"] - 2 <= cx <= r["x1"] + 2 and r["y0"] - 2 <= cy <= r["y1"] + 2:
                sizes.append(size)
        sizes.sort()
        return (sizes[len(sizes) // 2] if sizes else None)

    body_size = 9.0
    cnt = {}
    for r in recs:
        sz = block_font(r)
        if sz:
            cnt[sz] = cnt.get(sz, 0) + len(r["text"])
    if cnt:
        body_size = max(cnt, key=cnt.get)

    for r in recs:
        if r["action"] != "translate" or r["kind"] != "body":
            continue
        sz = block_font(r)
        if not sz:
            continue
        if sz >= body_size * 1.45:
            r["kind"] = "title"
        elif sz >= body_size * 1.08:
            r["kind"] = "head"

    pb.mark_references(recs, verbose=False)
    for r in recs:
        if re.match(r"^\d{1,3}\.\s+\S", r["text"].strip()):
            r["action"], r["kind"] = "ref", "reference"

    # 图 / 表：位图裁图 + 短块密集成片（表格、图内文字）整块裁掉
    figdir = os.path.join(workdir, "fig")
    os.makedirs(figdir, exist_ok=True)
    cap_of = {p: sorted([r for r in recs if r["page"] == p and r["kind"] == "figcap"],
                        key=lambda b: b["y0"]) for p in range(1, npages + 1)}

    def key_of(cap):
        m = re.match(r"^((?:FIGURE|Figure|Fig\.?|TABLE|Table)\s*\d+)", cap["text"].strip())
        return m.group(1) if m else cap["text"].strip()[:12]

    cands = []
    dense = {}
    for r in recs:
        if r["action"] == "translate" and r["kind"] == "body" and len(r["text"].strip()) <= 60:
            dense.setdefault(r["page"], []).append(r)
    for pno, frs in sorted(dense.items()):
        if len(frs) < 8:
            continue
        page = doc[pno - 1]
        x0 = min(f["x0"] for f in frs); x1 = max(f["x1"] for f in frs)
        y0 = min(f["y0"] for f in frs); y1 = max(f["y1"] for f in frs)
        if (x1 - x0) * (y1 - y0) > 0.75 * page.rect.width * page.rect.height:
            continue
        if (x1 - x0) < 120 or (y1 - y0) < 60:
            continue
        for r in recs:
            if r["action"] != "translate" or r["page"] != pno:
                continue
            if r["kind"] in ("figcap", "head", "title"):
                continue
            cx, cy = (r["x0"] + r["x1"]) / 2, (r["y0"] + r["y1"]) / 2
            if x0 - 4 <= cx <= x1 + 4 and y0 - 4 <= cy <= y1 + 4:
                r["action"], r["kind"] = "skip", "table-cell"
        pool = cap_of[pno] or cap_of.get(pno + 1, []) or cap_of.get(pno - 1, [])
        if not pool:
            continue
        cap = min(pool, key=lambda c: abs(c["y0"] - (y1 + 6)) + 40 * (c["page"] != pno))
        cands.append((key_of(cap), pno, cap["id"],
                      pymupdf.Rect(max(0, x0 - 4), max(0, y0 - 6),
                                   min(page.rect.width, x1 + 4),
                                   min(page.rect.height, y1 + 6))))

    for pno, page in enumerate(doc, 1):
        caps = cap_of[pno]
        if not caps:
            continue
        for info in sorted(page.get_image_info(), key=lambda x: x["bbox"][1]):
            ix0, iy0, ix1, iy1 = info["bbox"]
            if (ix1 - ix0) < 60 or (iy1 - iy0) < 40:
                continue
            hit = [c for c in caps if c["y0"] >= iy1 - 10 and c["y0"] - iy1 < 120
                   and not (c["x1"] < ix0 or c["x0"] > ix1)]
            if not hit:
                continue
            cap = min(hit, key=lambda c: c["y0"] - iy1)
            cands.append((key_of(cap), pno, cap["id"],
                          pymupdf.Rect(max(0, ix0), max(0, iy0),
                                       min(ix1, page.rect.width), min(iy1, page.rect.height))))

    best = {}
    for k, pno, cid, rect in cands:
        a = rect.width * rect.height
        if k not in best or a > best[k][0]:
            best[k] = (a, pno, cid, rect)
    figs = []
    for k, (a, pno, cid, rect) in best.items():
        pix = doc[pno - 1].get_pixmap(clip=rect, dpi=180)
        safe = re.sub(r"[^\w.\-]+", "_", k)
        path = os.path.join(figdir, safe + "_EN.png")
        pix.save(path)
        figs.append({"key": k + " ", "page": pno, "caption_id": cid,
                     "image": path, "zh_title": k})
    json.dump({"figures": figs}, open(os.path.join(workdir, "my_figures.json"), "w",
                                      encoding="utf-8", newline="\n"),
              ensure_ascii=False, indent=1)
    doc.close()
    n_tr = sum(1 for r in recs if r["action"] == "translate")
    log("  剔除页眉页脚/表格碎片 %d 块，待译 %d 块，图 %d 张" % (n_skip, n_tr, len(figs)))
    return recs, figs


def run_pipeline(src_pdf, out_pdf, start=None, end=None, sleep=0.3, log=print,
                 progress=None, stop=None, backend="mymemory",
                 translator=None, title_zh="", quiet_engine=False):
    """完整流程：trim → 提取清理裁图 → 逐句翻译 → 合成 PDF"""
    tmp = tempfile.mkdtemp(prefix="bi_pdf_")
    try:
        log("① 复制并截取页面 …")
        local = os.path.join(tmp, "paper.pdf")
        shutil.copyfile(src_pdf, local)
        if start or end:
            trimmed = os.path.join(tmp, "trim.pdf")
            pages = trim_pdf(local, trimmed, start, end)
            os.replace(trimmed, local)
            log("  已截取 %d 页" % pages)

        log("② 提取文本、清理、裁图 …")
        recs, figs = prepare_blocks(local, tmp, log)
        need = [r for r in recs if r["action"] == "translate"]

        log("③ 逐句翻译 …")
        cache_p = os.path.join(tmp, "cache_%s.json" % backend)
        # 批量模式下由外面建好的翻译器传进来（本地模型只加载一次，几十篇也不用反复加载）
        own = translator is None
        if own:
            if backend == "local":
                log("  引擎：本地离线（Argos Translate，不联网、无限额）")
                tr = LocalTranslator(cache_p, log=log, stop=stop)
            else:
                log("  引擎：在线免费（MyMemory，每日有限额）")
                tr = Translator(cache_p, sleep=sleep, log=log, stop=stop)
        else:
            tr = translator
        sents_total = 0
        for b in need:
            sents_total += len(pb.split_sentences(b["text"]))
        log("  共 %d 块 / %d 句" % (len(need), sents_total))

        zh, done = {}, 0
        for b in need:
            if stop and stop():
                log("  已取消。")
                return None
            zs = []
            for s in pb.split_sentences(b["text"]):
                zs.append(tr.one(s))
                done += 1
                if progress and sents_total:
                    progress(done, sents_total)
            zh[b["id"]] = zs
        os.makedirs(os.path.join(tmp, "zh"), exist_ok=True)
        json.dump(zh, open(os.path.join(tmp, "zh", "batch_01.json"), "w",
                           encoding="utf-8", newline="\n"),
                  ensure_ascii=False, indent=1)
        log("  本篇：成功 %d 句 / 缓存命中 %d / 失败(保留英文) %d"
            % (tr.ok, tr.hit, tr.fail))
        if own and hasattr(tr, "save"):
            tr.save()
        if tr.quota:
            log("  ⚠ 免费额度已用尽：本次有句子保留英文原文。隔几小时或用别的网络再跑可续译"
                "（已译部分有缓存，不会重复消耗额度）。")

        log("④ 合成双语 PDF …")
        out_name = title_zh or os.path.splitext(os.path.basename(src_pdf))[0]
        COVER = ("<b>本文件由「双语 PDF 翻译器」生成</b>：一句英文后紧跟该句中文；"
                 "英文句号本身就是分界，所以中文前不加逗号，只留一个空格。"
                 "中文为<b>免费机器翻译</b>，专业术语可能有误，重要内容请以英文原文为准。"
                 "图仅保留英文原图；表格与图内文字以图片保留；参考文献保留英文。")
        json.dump(recs, open(os.path.join(tmp, "doc_model.json"), "w",
                             encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
        # ⚠️ 必须把封面文字**作为参数**传给 cmd_build；只写 cfg.json 是不生效的
        #    （那是命令行入口才读的）。踩过：封面标题空白、说明文字丢失。
        pb.cmd_build(tmp, out_pdf, figures={"figures": figs},
                     title_zh=out_name, title_en="",
                     source_line="双语对照（机器翻译）", cover_note=COVER)
        log("✅ 完成：%s（%s）" % (out_pdf,
                                 "{:,} 字节".format(os.path.getsize(out_pdf))))
        return out_pdf
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ================================================================ 图形界面
def run_gui_single():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from tkinter.scrolledtext import ScrolledText

    root = tk.Tk()
    root.title("双语 PDF 翻译器（英文 PDF → 一句英文 + 紧跟中文）")
    root.geometry("760x560")
    root.minsize(700, 520)

    pad = {"padx": 8, "pady": 4}
    frm = ttk.Frame(root)
    frm.pack(fill="x", **pad)
    ttk.Label(frm, text="输入 PDF：").grid(row=0, column=0, sticky="w")
    v_in = tk.StringVar()
    ttk.Entry(frm, textvariable=v_in).grid(row=0, column=1, sticky="we", padx=4)
    frm.columnconfigure(1, weight=1)

    def pick_in():
        p = filedialog.askopenfilename(title="选择英文 PDF",
                                       filetypes=[("PDF 文件", "*.pdf"), ("全部", "*.*")])
        if p:
            v_in.set(p)
            if not v_out.get():
                d, n = os.path.split(p)
                v_out.set(os.path.join(d, os.path.splitext(n)[0] + "_双语.pdf"))

    ttk.Button(frm, text="浏览…", command=pick_in).grid(row=0, column=2)

    ttk.Label(frm, text="输出文件：").grid(row=1, column=0, sticky="w")
    v_out = tk.StringVar()
    ttk.Entry(frm, textvariable=v_out).grid(row=1, column=1, sticky="we", padx=4)

    def pick_out():
        p = filedialog.asksaveasfilename(title="保存为", defaultextension=".pdf",
                                         filetypes=[("PDF 文件", "*.pdf")])
        if p:
            v_out.set(p)

    ttk.Button(frm, text="另存为…", command=pick_out).grid(row=1, column=2)

    opt = ttk.Frame(root)
    opt.pack(fill="x", **pad)
    ttk.Label(opt, text="起始页").pack(side="left")
    v_s = tk.StringVar()
    ttk.Entry(opt, textvariable=v_s, width=6).pack(side="left", padx=(2, 10))
    ttk.Label(opt, text="结束页").pack(side="left")
    v_e = tk.StringVar()
    ttk.Entry(opt, textvariable=v_e, width=6).pack(side="left", padx=(2, 10))
    ttk.Label(opt, text="（留空=全书；先试 2–3 页可少用免费额度）").pack(side="left")
    ttk.Label(opt, text="  每句间隔(s)").pack(side="left")
    v_sleep = tk.StringVar(value="0.3")
    ttk.Entry(opt, textvariable=v_sleep, width=5).pack(side="left", padx=2)

    eng = ttk.Frame(root)
    eng.pack(fill="x", **pad)
    ttk.Label(eng, text="翻译引擎：").pack(side="left")
    v_backend = tk.StringVar(value="local" if argos_available() else "mymemory")
    ttk.Radiobutton(eng, text="本地离线（Argos，不联网、无限额）", value="local",
                    variable=v_backend).pack(side="left", padx=(4, 12))
    ttk.Radiobutton(eng, text="在线免费（MyMemory，每日有限额）", value="mymemory",
                    variable=v_backend).pack(side="left")
    if not argos_available():
        ttk.Label(eng, text="（本地模型未装，选这项会提示安装方法）",
                  foreground="#a04000").pack(side="left", padx=6)

    btns = ttk.Frame(root)
    btns.pack(fill="x", **pad)
    b_start = ttk.Button(btns, text="开始翻译")
    b_start.pack(side="left")
    b_open = ttk.Button(btns, text="打开输出目录", state="disabled")
    b_open.pack(side="left", padx=6)
    b_stop = ttk.Button(btns, text="取消", state="disabled")
    b_stop.pack(side="left")

    bar = ttk.Progressbar(root, mode="determinate", maximum=100)
    bar.pack(fill="x", padx=8, pady=(2, 6))
    logbox = ScrolledText(root, height=18, state="disabled", wrap="word")
    logbox.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    q = queue.Queue()
    state = {"running": False, "stop": False}

    def log(msg):
        q.put(("log", msg))

    def pump():
        try:
            while True:
                kind, val = q.get_nowait()
                if kind == "log":
                    logbox.configure(state="normal")
                    logbox.insert("end", val + "\n")
                    logbox.see("end")
                    logbox.configure(state="disabled")
                elif kind == "prog":
                    done, total = val
                    bar["value"] = 100.0 * done / max(1, total)
                elif kind == "done":
                    state["running"] = False
                    b_start["state"] = "normal"
                    b_stop["state"] = "disabled"
                    b_open["state"] = "normal"
                    if val:
                        messagebox.showinfo("完成", "已生成：\n%s" % val)
                elif kind == "error":
                    state["running"] = False
                    b_start["state"] = "normal"
                    b_stop["state"] = "disabled"
                    messagebox.showerror("出错了", val)
        except queue.Empty:
            pass
        root.after(120, pump)

    def worker(src, out, s, e, sl, backend):
        try:
            run_pipeline(src, out, s, e, sl, log=log,
                         progress=lambda d, t: q.put(("prog", (d, t))),
                         stop=lambda: state["stop"], backend=backend)
            q.put(("done", out))
        except Exception:
            q.put(("error", traceback.format_exc(limit=3)))

    def on_start():
        src = v_in.get().strip()
        out = v_out.get().strip()
        if not os.path.isfile(src):
            messagebox.showwarning("缺少输入", "请先选择要翻译的 PDF 文件。")
            return
        if not out:
            messagebox.showwarning("缺少输出", "请指定输出文件路径。")
            return
        try:
            s = int(v_s.get()) if v_s.get().strip() else None
            e = int(v_e.get()) if v_e.get().strip() else None
            sl = float(v_sleep.get() or 0.3)
        except ValueError:
            messagebox.showwarning("参数有误", "页码要填整数，间隔填小数。")
            return
        state["running"] = True
        state["stop"] = False
        b_start["state"] = "disabled"
        b_stop["state"] = "normal"
        bar["value"] = 0
        log("=== 开始：%s（引擎：%s）" % (os.path.basename(src),
                                        "本地离线 Argos" if v_backend.get() == "local"
                                        else "在线 MyMemory"))
        threading.Thread(target=worker, args=(src, out, s, e, sl, v_backend.get()),
                         daemon=True).start()

    b_start.configure(command=on_start)
    b_stop.configure(command=lambda: (state.update(stop=True), log("正在取消…")))
    b_open.configure(command=lambda: os.startfile(os.path.dirname(v_out.get()) or "."))

    log("两种引擎：")
    log("  · 本地离线（Argos）：完全不联网、没有额度限制，约 0.15 秒/句；需先装一次模型。")
    log("  · 在线免费（MyMemory）：不用装模型，但每天有限额，额度用完那几句保留英文。")
    log("  中文为机器翻译，专业术语可能有误——重要内容请对照英文原文。")
    log("「使用专业术语表」默认打开：会把生物医学术语校正成规范译法"
        "（type strains→模式菌株、psoriasis→银屑病、in silico→计算机模拟 …）。")
    log("它是机器翻译，只覆盖表里已知的术语——重要内容仍请对照英文原文。")
    pump()
    root.mainloop()


# ================================================================ 标题提取 / 命名
_SECTION_LABELS = re.compile(
    r"^(point of view|original research|review article|mini[-\s]?review|review|perspective|"
    r"editorial|brief report|case report|short communication|opinion|commentary|"
    r"research article|clinical trial|study protocol)\s+", re.I)


def extract_title(pdf_path):
    """从 PDF 首页取「论文标题」的英文原文

    做法：首页字号最大的那一块文本就是标题。但**不能把同字号的整页文本都拼起来**——
    实测 JMicrobiolMethods 的期刊名与论文标题同字号，整页拼接会把
    「Journal of Microbiological Methods」也并进标题里。所以只取**紧挨标题那一带**
    （纵向相距 110 pt 以内）的同号块；再把「Point of View」这类栏目名去掉。
    """
    try:
        doc = pymupdf.open(pdf_path)
        page = doc[0]
        blocks = []
        for blk in page.get_text("dict")["blocks"]:
            txts, sizes, ys = [], [], []
            for ln in blk.get("lines", []):
                for s in ln["spans"]:
                    t = s["text"].strip()
                    if t:
                        txts.append(t)
                        sizes.append(s["size"])
                        ys.append((s["bbox"][1] + s["bbox"][3]) / 2)
            if not txts:
                continue
            blocks.append({"text": re.sub(r"\s+", " ", " ".join(txts)).strip(),
                           "size": max(sizes), "y": min(ys)})
        doc.close()
        if not blocks:
            return ""

        biggest = max(b["size"] for b in blocks)
        # 同字号时取文字更长的那块当锚点（避免抓到栏目标签）
        tops = [b for b in blocks if b["size"] >= biggest - 0.6]
        tops.sort(key=lambda b: (-len(b["text"]), b["y"]))
        anchor = tops[0]

        band = [b for b in blocks
                if b["size"] >= biggest - 0.6 and -12 <= (b["y"] - anchor["y"]) <= 110]
        band.sort(key=lambda b: b["y"])
        txt = re.sub(r"\s+", " ", " ".join(b["text"] for b in band)).strip()
        txt = _SECTION_LABELS.sub("", txt).strip(" .,:;-—·\u200b")
        if 15 <= len(txt) <= 350:
            return txt
        t = _SECTION_LABELS.sub("", anchor["text"]).strip(" .,:;-—·\u200b")
        return t if 15 <= len(t) <= 350 else ""
    except Exception:
        return ""


_ILLEGAL = re.compile(r'[\\/:*?"<>|\r\n\t]')


def sanitize_filename(name, maxlen=120):
    """把标题变成合法的 Windows 文件名：去非法字符、压空白、限长

    限长时**尽量在空格处断开**——英文标题硬截会留下「…type 2 diabetes melli」这种半截词。
    """
    s = _ILLEGAL.sub(" ", name or "")
    s = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", s)      # 去零宽字符
    s = re.sub(r"\s+", " ", s).strip(" .-—_·")
    if len(s) > maxlen:
        cut = s[:maxlen]
        if " " in cut[maxlen // 2:]:
            cut = cut[:cut.rfind(" ")]
        s = cut.strip(" .-—_·,;:")
    return s


def unique_path(folder, stem, ext=".pdf"):
    """同名文件已存在时，加（2）（3）……避免覆盖"""
    p = os.path.join(folder, stem + ext)
    if not os.path.exists(p):
        return p
    for i in range(2, 999):
        p = os.path.join(folder, "%s（%d）%s" % (stem, i, ext))
        if not os.path.exists(p):
            return p
    return os.path.join(folder, stem + ext)


def build_translator(backend, cache_path, sleep, log, stop, use_glossary=True):
    """按引擎名造一个翻译器（本地只加载一次模型，批量时复用）

    use_glossary=True 时会在外面套一层「专业术语校正」——
    先让引擎裸译每条术语记下它的错误译法，再在整句译文里定点纠正。
    """
    if backend == "local":
        base = LocalTranslator(cache_path, log=log, stop=stop)
    else:
        base = Translator(cache_path, sleep=sleep, log=log, stop=stop)
    return wrap_with_glossary(base, log=log, enabled=use_glossary)



# ---------------------------------------------------------------- 术语表
_GLOSSARY = None          # 全局术语表（首次用到时载入）
_GLOSSARY_TRIED = False


def get_glossary(log=None, force=False):
    """载入术语表（只载入一次）。找不到就返回 None，程序照常工作。"""
    global _GLOSSARY, _GLOSSARY_TRIED
    if _gloss is None:
        return None
    if _GLOSSARY is not None or (_GLOSSARY_TRIED and not force):
        return _GLOSSARY
    _GLOSSARY_TRIED = True
    try:
        _GLOSSARY = _gloss.load_default(log=log or (lambda s: None))
    except Exception:
        _GLOSSARY = None
    return _GLOSSARY


def wrap_with_glossary(tr, log=None, enabled=True):
    """给翻译器套上术语校正（enabled=False 时原样返回）"""
    if not enabled or tr is None or _gloss is None:
        return tr
    g = get_glossary(log)
    if not g or not len(g):
        return tr
    try:
        # 用引擎裸译每条术语，记录它的错误译法；结果缓存，术语表变了才重算
        cache = ""
        try:
            import local_engine as _le
            cache = os.path.join(_le.app_data_dir(), "glossary_cache.json")
        except Exception:
            pass
        g.calibrate(tr.translate, cache_path=cache, log=log or (lambda s: None))
        return _gloss.GlossaryTranslator(tr, g, log=log)
    except Exception as e:
        (log or (lambda s: None))("术语表启用失败：%s（忽略，继续翻译）" % e)
        return tr


# ================================================================ 批量流程
def run_batch(inputs, out_dir, start=None, end=None, sleep=0.3, log=print,
              progress=None, progress_file=None, stop=None, backend="local",
              naming="zh", use_glossary=True):
    """一次处理多篇：逐篇「取名 → 翻译 → 生成」，一篇失败不影响其余

    naming 决定输出文件叫什么：
      "zh"   = 论文标题的**中文译名**（默认；机器翻译，个别词会错）
      "en"   = 论文标题的**英文原文**（不会译错，但不是中文）
      "orig" = 沿用输入文件的文件名
    """
    os.makedirs(out_dir, exist_ok=True)
    results = []
    cache_p = os.path.join(out_dir, "._cache_%s.json" % backend)
    tr = None
    try:
        tr = build_translator(backend, cache_p, sleep, log, stop, use_glossary)
    except Exception as e:
        log("❌ 引擎初始化失败：%s" % e)
        log("   本地模型未装时，请改用「在线免费（MyMemory）」，或先运行 install_argos.py")
        return results

    for idx, src in enumerate(inputs, 1):
        if stop and stop():
            log("已取消，剩余 %d 篇未处理。" % (len(inputs) - idx + 1))
            break
        log("")
        log("=" * 64)
        log("【第 %d/%d 篇】%s" % (idx, len(inputs), os.path.basename(src)))
        if progress_file:
            progress_file(idx - 1, len(inputs), os.path.basename(src))
        try:
            # ① 取英文标题 → 按所选方式定文件名
            en_title = extract_title(src)
            if en_title:
                log("  英文标题：%s" % en_title[:90])
            else:
                log("  ⚠ 没找到标题")
            if naming == "orig" or not en_title:
                zh_title = os.path.splitext(os.path.basename(src))[0]
            elif naming == "en":
                zh_title = en_title
            else:
                try:
                    zh_title = tr.one(en_title)
                except Exception:
                    zh_title = ""
                if not zh_title or zh_title.strip() == en_title.strip():
                    zh_title = en_title           # 翻译失败就用英文标题当名字
            log("  取名为：%s" % zh_title[:90])
            stem = sanitize_filename(zh_title) or os.path.splitext(os.path.basename(src))[0]
            dst = unique_path(out_dir, stem)
            # ② 翻译 + 合成
            run_pipeline(src, dst, start, end, sleep, log=log, progress=progress,
                         stop=stop, backend=backend, translator=tr,
                         title_zh=stem, quiet_engine=True)
            results.append((src, dst, True, ""))
            log("  ✅ 输出：%s" % dst)
        except Exception as e:
            log("  ❌ 这篇失败：%s" % e)
            results.append((src, "", False, str(e)))
        finally:
            if hasattr(tr, "save"):
                tr.save()
    # 收尾
    ok = [r for r in results if r[2]]
    log("")
    log("=" * 64)
    log("全部结束：成功 %d 篇，失败 %d 篇，输出目录 %s"
        % (len(ok), len(results) - len(ok), out_dir))
    for src, dst, good, err in results:
        log("  %s %s" % ("✅" if good else "❌", os.path.basename(src) if good else src))
    return results


# ================================================================ 图形界面（批量版）
def run_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from tkinter.scrolledtext import ScrolledText

    root = tk.Tk()
    root.title("双语 PDF 翻译器 · 批量（自动按论文标题命名）")
    root.geometry("880x640")
    root.minsize(780, 580)

    pad = {"padx": 8, "pady": 4}
    files = []          # 待处理文件（绝对路径）

    # ---- 输入：多文件列表
    box = ttk.LabelFrame(root, text="① 输入文章（可一次加很多篇，会逐篇跑完）")
    box.pack(fill="both", expand=False, **pad)
    lst = tk.Listbox(box, height=6, selectmode="extended")
    lst.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
    sb = ttk.Scrollbar(box, orient="vertical", command=lst.yview)
    sb.pack(side="left", fill="y", pady=6)
    lst.configure(yscrollcommand=sb.set)
    rb = ttk.Frame(box)
    rb.pack(side="left", fill="y", padx=6, pady=6)

    def refresh():
        lst.delete(0, "end")
        for i, p in enumerate(files, 1):
            lst.insert("end", "%d. %s" % (i, os.path.basename(p)))
        lbl_count.configure(text="共 %d 篇" % len(files))

    def add_files():
        ps = filedialog.askopenfilenames(title="选择英文 PDF（可按住 Ctrl 多选）",
                                         filetypes=[("PDF 文件", "*.pdf"), ("全部", "*.*")])
        for p in ps:
            if p not in files:
                files.append(p)
        refresh()

    def add_folder():
        d = filedialog.askdirectory(title="选择一个文件夹（会加入其中所有 PDF）")
        if not d:
            return
        got = sorted(f for f in os.listdir(d) if f.lower().endswith(".pdf"))
        for f in got:
            p = os.path.join(d, f)
            if p not in files:
                files.append(p)
        refresh()
        log("从文件夹加入 %d 个 PDF：%s" % (len(got), d))

    def remove_sel():
        for i in sorted(lst.curselection(), reverse=True):
            del files[i]
        refresh()

    def clear_all():
        files.clear()
        refresh()

    ttk.Button(rb, text="添加文件…", command=add_files, width=14).pack(fill="x", pady=2)
    ttk.Button(rb, text="添加文件夹…", command=add_folder, width=14).pack(fill="x", pady=2)
    ttk.Button(rb, text="移除选中", command=remove_sel, width=14).pack(fill="x", pady=2)
    ttk.Button(rb, text="清空", command=clear_all, width=14).pack(fill="x", pady=2)
    lbl_count = ttk.Label(rb, text="共 0 篇")
    lbl_count.pack(pady=(6, 0))

    # ---- 输出：一个文件夹
    outbox = ttk.Frame(root)
    outbox.pack(fill="x", **pad)
    ttk.Label(outbox, text="② 输出到文件夹：").pack(side="left")
    v_outdir = tk.StringVar(value=os.path.join(os.path.expanduser("~"), "Desktop"))
    ttk.Entry(outbox, textvariable=v_outdir).pack(side="left", fill="x", expand=True, padx=4)

    def pick_outdir():
        d = filedialog.askdirectory(title="选择输出文件夹")
        if d:
            v_outdir.set(d)

    ttk.Button(outbox, text="选择…", command=pick_outdir).pack(side="left")
    nrow = ttk.Frame(root)
    nrow.pack(fill="x", padx=8)
    ttk.Label(nrow, text="文件名用：").pack(side="left")
    NAMING = {"论文标题的中文译名": "zh", "论文标题的英文原文": "en", "沿用原文件名": "orig"}
    v_naming = tk.StringVar(value="论文标题的中文译名")
    ttk.Combobox(nrow, textvariable=v_naming, values=list(NAMING), width=20,
                 state="readonly").pack(side="left", padx=4)
    ttk.Label(nrow, text="（中文译名由机器翻译，个别词会不准，如 psoriasis 可能被译成磷酸盐；嫌不准就选英文原文）",
              foreground="#555555").pack(side="left")
    ttk.Label(root, text="同名文件不会覆盖，会自动加（2）（3）", foreground="#555555").pack(anchor="w", padx=10)

    # ---- 引擎与选项
    eng = ttk.Frame(root)
    eng.pack(fill="x", **pad)
    ttk.Label(eng, text="③ 翻译引擎：").pack(side="left")
    v_backend = tk.StringVar(value="local" if argos_available() else "mymemory")
    ttk.Radiobutton(eng, text="本地离线（Argos，不联网、无限额）", value="local",
                    variable=v_backend).pack(side="left", padx=(4, 12))
    ttk.Radiobutton(eng, text="在线免费（MyMemory，每日有限额）", value="mymemory",
                    variable=v_backend).pack(side="left")
    def install_model():
        """联网下载离线模型（只在程序没带模型、或模型丢失时需要）"""
        b_model["state"] = "disabled"

        def job():
            try:
                if ensure_local_model(log=log, allow_download=True):
                    log("✅ 离线模型就绪，可以选「本地离线」开始翻译了。")
                    q.put(("model_ok", True))
                else:
                    log("❌ 模型安装失败，请检查网络；也可以先用在线引擎。")
                    q.put(("model_ok", False))
            except Exception:
                q.put(("error", traceback.format_exc(limit=3)))

        threading.Thread(target=job, daemon=True).start()

    b_model = ttk.Button(eng, text="安装离线模型", command=install_model, width=13)
    b_model.pack(side="left", padx=(0, 10))
    def open_glossary():
        """点一下就用记事本打开**你自己的**术语表文件，改完保存即可

        为什么打开「用户副本」而不是安装目录里的原件：
          · 安装目录在升级时会被覆盖，写进去的术语会丢；
          · 安装目录可能根本没写权限。
        所以首次点开时会自动把出场词汇表复制一份到用户数据目录，之后都改这份。
        """
        if _gloss is None:
            log("术语表模块没能载入（安装可能不完整）。")
            return
        try:
            _gloss.ensure_user_glossary()
            p = _gloss.user_glossary_path()
        except Exception as e:
            log("准备术语表失败：%s" % e)
            return
        if not (p and os.path.exists(p)):
            log("没有找到术语表文件，也没能创建：%s" % p)
            return
        log("术语表文件：%s" % p)
        log("格式：英文<Tab>规范中文<Tab>备注<Tab>已知错译（分号分隔），一行一条；"
            "# 开头是注释。改完保存，点「重载术语表」即可生效。")
        try:
            # 用记事本打开：Tab 分隔的文件用 Excel 容易被自动改写，记事本最稳
            import subprocess
            subprocess.Popen(["notepad.exe", p])
        except Exception:
            os.startfile(p)

    def reload_glossary():
        """改完文件后不用重启：重新读一遍术语表并重新标定"""
        global _GLOSSARY, _GLOSSARY_TRIED
        _GLOSSARY = None
        _GLOSSARY_TRIED = False
        if _gloss is None:
            log("术语表模块未载入。")
            return
        g = get_glossary(log=log, force=True)
        if g is None:
            log("没有载入到术语表（文件可能不存在）。")
            return
        log("术语表已重新载入：%d 条（%s）" % (len(g), os.path.basename(_gloss.find_glossary_file())))
        log("下次点「开始翻译」即按新术语表校正。")

    v_gloss = tk.BooleanVar(value=True)
    ttk.Checkbutton(eng, text="使用专业术语表", variable=v_gloss).pack(side="left", padx=(0, 2))
    ttk.Button(eng, text="专业词汇表…", command=open_glossary, width=13).pack(side="left", padx=(0, 4))
    ttk.Button(eng, text="重载术语表", command=reload_glossary, width=11).pack(side="left", padx=(0, 8))
    ttk.Label(eng, text="   起始页").pack(side="left")
    v_s = tk.StringVar()
    ttk.Entry(eng, textvariable=v_s, width=5).pack(side="left", padx=2)
    ttk.Label(eng, text="结束页").pack(side="left")
    v_e = tk.StringVar()
    ttk.Entry(eng, textvariable=v_e, width=5).pack(side="left", padx=2)
    ttk.Label(eng, text="（留空=每篇都翻全书）").pack(side="left")

    btns = ttk.Frame(root)
    btns.pack(fill="x", **pad)
    b_start = ttk.Button(btns, text="开始翻译（全部）")
    b_start.pack(side="left")
    b_open = ttk.Button(btns, text="打开输出文件夹", state="disabled")
    b_open.pack(side="left", padx=6)
    b_stop = ttk.Button(btns, text="取消", state="disabled")
    b_stop.pack(side="left")

    bar = ttk.Progressbar(root, mode="determinate", maximum=100)
    bar.pack(fill="x", padx=8, pady=(2, 2))
    lbl_now = ttk.Label(root, text="等待开始")
    lbl_now.pack(anchor="w", padx=10)
    logbox = ScrolledText(root, height=16, state="disabled", wrap="word")
    logbox.pack(fill="both", expand=True, padx=8, pady=(2, 8))

    q = queue.Queue()
    state = {"stop": False}

    def log(msg):
        q.put(("log", msg))

    def pump():
        try:
            while True:
                kind, val = q.get_nowait()
                if kind == "log":
                    logbox.configure(state="normal")
                    logbox.insert("end", val + "\n")
                    logbox.see("end")
                    logbox.configure(state="disabled")
                elif kind == "prog":
                    done, total = val
                    bar["value"] = 100.0 * done / max(1, total)
                elif kind == "file":
                    i, n, name = val
                    lbl_now.configure(text="第 %d/%d 篇：%s" % (i, n, name))
                    bar["value"] = 100.0 * i / max(1, n)
                elif kind == "done":
                    b_start["state"] = "normal"
                    b_stop["state"] = "disabled"
                    b_open["state"] = "normal"
                    bar["value"] = 100
                    ok = sum(1 for r in (val or []) if r[2])
                    messagebox.showinfo("完成", "成功 %d 篇，失败 %d 篇。\n输出目录：\n%s"
                                        % (ok, len(val or []) - ok, v_outdir.get()))
                elif kind == "model_ok":
                    b_model["state"] = "normal"
                    if val:
                        v_backend.set("local")
                elif kind == "error":
                    b_start["state"] = "normal"
                    b_stop["state"] = "disabled"
                    b_model["state"] = "normal"
                    messagebox.showerror("出错了", val)
        except queue.Empty:
            pass
        root.after(120, pump)

    def worker(todo, outdir, s, e, backend, naming, use_gloss):
        try:
            res = run_batch(todo, outdir, s, e, 0.3, log=log,
                            progress=lambda d, t: q.put(("prog", (d, t))),
                            progress_file=lambda i, n, name: q.put(("file", (i, n, name))),
                            stop=lambda: state["stop"], backend=backend, naming=naming, use_glossary=use_gloss)
            q.put(("done", res))
        except Exception:
            q.put(("error", traceback.format_exc(limit=3)))

    def on_start():
        if not files:
            messagebox.showwarning("没有输入", "请先添加要翻译的 PDF（可多选）。")
            return
        outdir = v_outdir.get().strip()
        if not outdir:
            messagebox.showwarning("没有输出位置", "请选择输出文件夹。")
            return
        try:
            s = int(v_s.get()) if v_s.get().strip() else None
            e = int(v_e.get()) if v_e.get().strip() else None
        except ValueError:
            messagebox.showwarning("参数有误", "页码要填整数。")
            return
        state["stop"] = False
        b_start["state"] = "disabled"
        b_stop["state"] = "normal"
        bar["value"] = 0
        log("=== 开始：共 %d 篇，输出到 %s" % (len(files), outdir))
        log("=== 引擎：%s" % ("本地离线 Argos" if v_backend.get() == "local" else "在线 MyMemory"))
        threading.Thread(target=worker,
                         args=(list(files), outdir, s, e, v_backend.get(),
                               NAMING[v_naming.get()], v_gloss.get()),
                         daemon=True).start()

    b_start.configure(command=on_start)
    b_stop.configure(command=lambda: (state.update(stop=True), log("正在取消（当前这篇跑完后停止）…")))
    b_open.configure(command=lambda: os.startfile(v_outdir.get() or "."))

    log("用法：① 添加文章（可多选、也可整个文件夹）→ ② 选输出文件夹 → ③ 开始翻译。")
    log("文件名会自动用「论文标题的中文译名」；同名不会覆盖，会加（2）（3）。")
    log("本地离线引擎 0.15 秒/句，不联网、无限额；在线引擎每天有免费额度。")
    pump()
    root.mainloop()


# ================================================================ 入口
def _ensure_streams():
    """窗口模式（--windowed）下 sys.stdout / sys.stderr 是 None。

    而排版模块 pdf_bilingual 内部有几十处 print()——往 None 写会抛 AttributeError，
    整个流程就被判成「这篇失败」。启动时先把它们指向一个丢弃器：
    打不出字不要紧，不能崩。
    """
    class _Sink:
        def write(self, *a):
            return 0

        def flush(self):
            pass

        def isatty(self):
            return False

    if sys.stdout is None:
        sys.stdout = _Sink()
    if sys.stderr is None:
        sys.stderr = _Sink()


def main():
    _ensure_streams()
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", nargs=2, metavar=("输入PDF", "输出PDF"),
                    help="命令行自检：不弹窗，跑完整流程（用于验证打包）")
    ap.add_argument("--start", type=int, default=None)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--sleep", type=float, default=0.3)
    ap.add_argument("--backend", default="mymemory", choices=["mymemory", "local"],
                    help="翻译引擎：mymemory=在线免费；local=本地离线 Argos")
    ap.add_argument("--batch-out", default="",
                    help="批量模式：输出文件夹（后面跟着多个 PDF 路径）")
    ap.add_argument("--naming", default="zh", choices=["zh", "en", "orig"],
                    help="文件名来源：zh=标题中译；en=标题英文；orig=原文件名")
    ap.add_argument("--no-glossary", action="store_true",
                    help="不使用专业术语表（默认使用）")
    ap.add_argument("--where", action="store_true",
                    help="诊断用：打印程序路径、模型位置、日志位置后退出")
    ap.add_argument("files", nargs="*", help="批量模式下要翻译的 PDF 列表")
    a = ap.parse_args()
    if pb is None or pymupdf is None:
        msg = "缺少依赖：pymupdf / pdf_bilingual 未找到。"
        _safe_log(msg)
        try:
            import tkinter.messagebox as mb
            import tkinter as tk
            mb.showerror("无法启动", msg)
        except Exception:
            pass
        return 2
    if a.where:
        _safe_log("frozen        = %s" % getattr(sys, "frozen", False))
        _safe_log("sys._MEIPASS  = %s" % getattr(sys, "_MEIPASS", "(未设置)"))
        _safe_log("app_root      = %s" % _app_root())
        _safe_log("bundled_model = %s" % (bundled_model_path() or "(没找到)"))
        _safe_log("data_dir      = %s" % _data_dir())
        _safe_log("log_file      = %s" % _log_file_path())
        _safe_log("model_installed = %s" % _model_installed())
        _safe_log("local_engine 数据目录 = %s" % _local.app_data_dir())
        _safe_log("local_engine 选择说明 = %s" % _local.dir_note())
        _safe_log("local_engine 模型就绪 = %s" % _local.model_ready())
        _safe_log("local_engine 随身模型 = %s" % (_local.bundled_model() or "(没找到)"))
        return 0
    if a.batch_out:
        if not a.files:
            _safe_log("批量模式需要至少一个 PDF")
            return 2
        res = run_batch(a.files, a.batch_out, a.start, a.end, a.sleep,
                        log=_safe_log, backend=a.backend, naming=a.naming,
                        use_glossary=not a.no_glossary)
        ok = sum(1 for r in res if r[2])
        _safe_log("BATCH OK %d/%d" % (ok, len(res)))
        return 0 if ok == len(res) else 1
    if a.selftest:
        src, out = a.selftest
        run_pipeline(src, out, a.start, a.end, a.sleep, log=_safe_log,
                     backend=a.backend)
        _safe_log("SELFTEST OK")
        return 0
    run_gui()
    return 0


# ================================================================ 本地引擎改为 local_engine
# 上面那套基于 argostranslate 的实现留作历史参考（它依赖 stanza/MiniSBD 分句，
# 会在运行时联网下模型，离线机器上必崩；而且它的数据目录有两套机制，改了也不生效）。
# 这里在文件末尾**覆盖**同名函数与类：Python 在调用时解析名字，后定义者生效。
import local_engine as _local

def argos_available():
    """本地离线是否可用（模型已就绪，或程序里带了模型文件）"""
    return _local.model_ready() or bool(_local.bundled_model())


def ensure_local_model(log=None, allow_download=False):
    """确保本地有离线模型：先解压随身模型（无需联网），再考虑下载"""
    return _local.ensure_model(log, allow_download=allow_download)


class LocalTranslator(_local.Translator):
    """本地离线翻译（CTranslate2 + sentencepiece 直接加载 Argos 模型）"""

    def __init__(self, cache_path="", log=None, stop=None):
        super().__init__(cache_path=cache_path, log=log, stop=stop)


if __name__ == "__main__":
    sys.exit(main())
