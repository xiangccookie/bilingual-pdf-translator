# -*- coding: utf-8 -*-
"""
local_engine.py —— 本地离线翻译引擎（不依赖 argostranslate）

为什么不用 argostranslate：
  它把「分句 → 翻译」打包成一条链，分句器会在运行时自动选 stanza / MiniSBD / spaCy，
  这几种**都会去网上下模型**（huggingface / github），离线机器上直接崩；
  它的数据目录又由 settings.data_dir / package_dirs 两套机制决定，
  改了 data_dir 也不生效（实测），在打包环境里极难伺候。

所以这里直接调用它内部真正干活的两个库：
  · CTranslate2：跑模型（Argos 的模型本来就是 CTranslate2 格式）
  · sentencepiece：把句子切成子词
流程就是「切子词 → 翻译 → 拼回文字」，二十行，完全可控。

模型来源（按优先级）：
  ① 已经解压到用户目录 → 直接用
  ② 程序里带了 .argosmodel（就是个 zip）→ 解压到用户目录（**无需联网**，首次约 10 秒）
  ③ allow_download=True → 从 argos-net.com 下 67.5 MB
"""
import hashlib
import json
import os
import re
import shutil
import sys
import time
import urllib.request
import zipfile

MODEL_DIRNAME = "translate-en_zh-1_9"
_LAST_DIR_NOTE = ""
MODEL_URL = "https://argos-net.com/v1/translate-en_zh-1_9.argosmodel"
APP_FOLDER = "BilingualPdfTranslator"

# ---------------------------------------------------------------- 路径
def _is_ascii_path(p):
    """路径是否全为 ASCII

    ⚠ 这条不是洁癖：sentencepiece 的 C++ 实现用窄字符 API，
    **打不开含非 ASCII 字符的路径**。实测模型放在中文目录下会报
    `NOT_FOUND: "…\\闲聊\\…\\sentencepiece.model": No such file or directory`，
    而文件明明就在那儿。所以模型必须落在纯英文路径。
    """
    return all(ord(c) < 128 for c in p)


def app_data_dir(log=None):
    """程序自己的数据目录（放模型、缓存、日志）

    依次尝试，取第一个**能写且路径全 ASCII** 的：
      ① %LOCALAPPDATA%\\BilingualPdfTranslator
      ② %TEMP%\\BilingualPdfTranslator
      ③ 程序所在目录\\BilingualPdfTranslator（仅当它是纯英文路径）
    选中原因记在 _LAST_DIR_NOTE 里，`--where` 会打出来，便于排查。
    """
    global _LAST_DIR_NOTE
    cands = []
    base = os.environ.get("LOCALAPPDATA")
    if base:
        cands.append(os.path.join(base, APP_FOLDER))
    # 再加几个「纯英文、通常可写」的位置。
    # 为什么需要：实测某些环境里 %LOCALAPPDATA% 对本程序是拒绝访问的，
    # 而 %TEMP% 可能被重定向到中文路径（sentencepiece 打不开）。
    prof = os.environ.get("USERPROFILE")
    if prof:
        cands.append(os.path.join(prof, APP_FOLDER))
    pub = os.environ.get("PUBLIC")
    if pub:
        cands.append(os.path.join(pub, APP_FOLDER))
    cands.append(os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), APP_FOLDER))
    try:
        import tempfile
        cands.append(os.path.join(tempfile.gettempdir(), APP_FOLDER))
    except Exception:
        pass
    cands.append(os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), APP_FOLDER))

    notes = []
    for d in cands:
        if not _is_ascii_path(d):
            notes.append("%s → 路径含非 ASCII（sentencepiece 打不开）" % d)
            continue
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, ".write_test")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(probe)
            _LAST_DIR_NOTE = "；".join(notes) if notes else "首选目录可用"
            return d
        except Exception as e:
            notes.append("%s → %s" % (d, type(e).__name__))
    _LAST_DIR_NOTE = "；".join(notes)
    try:
        import tempfile
        return os.path.join(tempfile.gettempdir(), APP_FOLDER)
    except Exception:
        return cands[-1]


def dir_note():
    """上一次 app_data_dir() 的选择说明（用于诊断）"""
    return _LAST_DIR_NOTE


def model_dir():
    return os.path.join(app_data_dir(), "model", MODEL_DIRNAME)


def _resource_root():
    """打包后是解包目录（sys._MEIPASS），跑源码时是本文件所在目录"""
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def bundled_model():
    """程序里带的模型文件（.argosmodel，本质是 zip）

    查三个位置：<资源根>/assets、本文件目录/assets、上一级/assets
    （最后一个是给「直接跑源码」准备的——仓库里 assets 在根目录，源码在 src/ 下）
    """
    roots = [_resource_root(),
             os.path.dirname(os.path.abspath(__file__)),
             os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]
    for root in roots:
        for name in ("translate-en_zh.argosmodel", "translate-en_zh-1_9.argosmodel"):
            p = os.path.join(root, "assets", name)
            if os.path.exists(p):
                return p
    return ""


# ---------------------------------------------------------------- 模型安装
def model_ready():
    """模型文件齐不齐（不解压、不联网，纯看文件）"""
    d = model_dir()
    return (os.path.exists(os.path.join(d, "model", "model.bin"))
            and os.path.exists(os.path.join(d, "sentencepiece.model")))


def _extract(src_zip, log):
    """把 .argosmodel 解压到 <app_data>/model/ 下（包内顶层已是 translate-en_zh-1_9/）"""
    target = os.path.join(app_data_dir(), "model")
    os.makedirs(target, exist_ok=True)
    with zipfile.ZipFile(src_zip) as z:
        z.extractall(target)
    log("离线模型解压完成：%s" % model_dir())


def ensure_model(log=None, allow_download=True):
    """确保本地有可用模型；返回 True 表示可用"""
    log = log or (lambda s: None)
    if model_ready():
        return True
    src = bundled_model()
    if src:
        log("首次使用：正在解压随程序附带的离线模型（约 67.5 MB，只做一次，不需要联网）…")
        try:
            _extract(src, log)
            if model_ready():
                return True
        except Exception as e:
            log("解压失败：%s" % e)
    if allow_download:
        log("正在从 argos-net.com 下载英→中模型（约 67.5 MB，只需一次）…")
        try:
            tmp = os.path.join(app_data_dir(), "download.argosmodel")
            urllib.request.urlretrieve(MODEL_URL, tmp)
            _extract(tmp, log)
            try:
                os.remove(tmp)
            except Exception:
                pass
            if model_ready():
                return True
        except Exception as e:
            log("下载失败：%s" % e)
    log("没有可用的离线模型。可改用在线引擎，或点「安装离线模型」联网下载一次。")
    return False


# ---------------------------------------------------------------- 文本整理
def tidy(zh):
    """收拾机器翻译的排版小毛病（只动空格与标点，不动字）"""
    z = str(zh).replace("\u2581", "")   # 去掉句子片段符 ▁（模型输出残留）
    z = zh
    z = re.sub(r"[（(]\s+", "（", z)
    z = re.sub(r"\s+[）)]", "）", z)
    z = re.sub(r"\|\s*([A-Za-z0-9_.\-]+)\s*\|", r"|\1|", z)
    z = re.sub(r"([\u4e00-\u9fff])\s+([\u4e00-\u9fff])", r"\1\2", z)
    z = re.sub(r"\s+([，。；：、）])", r"\1", z)
    return z.strip()


NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


def numbers_kept(en, zh):
    """英文里的数字是否都还在译文里（防止 0.907 变成 0.9 或丢掉）"""
    nums = NUM_RE.findall(en)
    return all(n in zh for n in nums) if nums else True


# ---------------------------------------------------------------- 翻译器
def _load_model_files(d):
    """加载模型，返回 (ctranslate2.Translator, SentencePieceProcessor)

    ⚠ 关键技巧：**先 chdir 进模型目录，再用相对名加载**。
    原因：sentencepiece 的 C++ 实现用窄字符 API，打不开含中文的**绝对**路径
    （实测报 NOT_FOUND，而文件就在那儿）；ctranslate2 也同理。
    换成相对名（"model" / "sentencepiece.model"）后，路径里就没有非 ASCII 字符了，
    中文目录也能正常工作。加载完立刻切回原目录，避免影响别处。
    """
    import ctranslate2
    import sentencepiece as spm

    old = os.getcwd()
    try:
        os.chdir(d)
        tr = ctranslate2.Translator("model", device="cpu")
        sp = spm.SentencePieceProcessor(model_file="sentencepiece.model")
        return tr, sp
    finally:
        try:
            os.chdir(old)
        except Exception:
            pass


class Translator:
    """接口与 bi_app/在线翻译器一致：.translate(text) / .one(text)"""

    def __init__(self, cache_path="", log=None, stop=None):
        self.log = log or (lambda s: None)
        self.stop = stop or (lambda: False)
        self.ok = self.hit = self.fail = 0
        self.quota = False              # 离线没有额度概念，保留字段以兼容
        self.cache_path = cache_path
        self.cache = {}
        if cache_path and os.path.exists(cache_path):
            try:
                self.cache = json.load(open(cache_path, encoding="utf-8"))
            except Exception:
                self.cache = {}
        if not ensure_model(self.log, allow_download=False):
            raise RuntimeError(
                "本地没有可用的离线模型。\n"
                "请点界面上的「安装离线模型」（联网下载一次约 67.5 MB），"
                "或改用「在线免费（MyMemory）」。")
        d = model_dir()
        t0 = time.time()
        try:
            self._tr, self._sp = _load_model_files(d)
        except ImportError as e:
            raise RuntimeError("缺少 CTranslate2 / sentencepiece：%s" % e)
        self.log("离线引擎就绪（加载 %.2f 秒）" % (time.time() - t0))

    def translate(self, text):
        tokens = self._sp.encode(text, out_type=str)
        if not tokens:
            return ""
        out = self._tr.translate_batch([tokens], max_batch_size=1)[0].hypotheses[0]
        return self._sp.decode(out)

    def one(self, text):
        key = hashlib.sha1(text.encode("utf-8")).hexdigest()
        if key in self.cache:
            self.hit += 1
            return self.cache[key]
        try:
            zh = tidy(self.translate(text))
            if zh and numbers_kept(text, zh):
                self.cache[key] = zh
                self.ok += 1
                return zh
        except Exception as e:
            self.log("  本地翻译失败（%s），本句保留英文。" % type(e).__name__)
        self.fail += 1
        return text

    def save(self):
        if not self.cache_path:
            return
        try:
            json.dump(self.cache, open(self.cache_path, "w", encoding="utf-8",
                                       newline="\n"), ensure_ascii=False)
        except Exception:
            pass


if __name__ == "__main__":
    print("模型目录：", model_dir())
    print("模型就绪：", model_ready())
    t = Translator(log=print)
    for s in ("The tumor microenvironment facilitates immune evasion.",
              "Gastric cancer is a common malignancy worldwide."):
        t0 = time.time()
        print("[%.2f 秒] %s" % (time.time() - t0, t.translate(s)))
