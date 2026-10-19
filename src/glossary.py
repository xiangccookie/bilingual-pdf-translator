# -*- coding: utf-8 -*-
"""
glossary.py —— 专业术语表（生物医学）

要解决的问题
------------
机器翻译在专业文本上会「认得词、但不认行话」：

    type strains   → 类型菌株        （应为 模式菌株）
    clades         → 花纹            （应为 进化支 / 分支）
    psoriasis      → 多病病人        （应为 银屑病）
    in silico      → 硅酸盐          （应为 计算机模拟）
    GenBank        → 基因银行        （应保留原文）

为什么不用「先替换成占位符、译完再换回来」
------------------------------------------
实测这个模型会把占位符译烂（ZXQ12 → ZQQ12），也会把塞进英文句里的中文吃掉
（"All 分支 of the tree" → "树上的所有 XXX"）。所以占位符方案不可靠。

采用的办法：**译后校正 + 自动标定**
----------------------------------
1. 术语表给出「英文 → 规范中文」；
2. 首次使用时，让引擎**把每条术语裸译一次**，把它的错误译法记下来（自动标定，
   结果缓存在数据目录，术语表变了才重算）；
3. 翻译整句后，若**英文原句里出现了该术语**，就把译文里对应的错误译法替换成规范译法。

为什么要「英文原句里出现了才替换」：错误译法可能是别处的正常用词
（比如 clades 被译成「花纹」，而「花纹」在别的句子里可能是对的），
有了英文侧的触发条件，替换就是定点、安全的。

术语表格式（TSV，可用记事本编辑，Tab 分隔）
-------------------------------------------
    # 英文<Tab>规范中文<Tab>备注（可空）<Tab>已知错误译法（可选，分号分隔）
    type strain	模式菌株	分类学：模式菌株	
    clade	进化支	系统发育	花纹

· <Tab> 必须是真的制表符，不是空格
· 以 # 开头的是注释行
· 英文匹配不区分大小写，并自动兼容复数（末尾加 s / es）
"""
import hashlib
import json
import os
import re

# 默认术语表文件名（随程序或仓库一起分发）
DEFAULT_NAME = "biology.tsv"


def _norm(s):
    return (s or "").strip()


class Glossary:
    """术语表：英文 → 规范中文，附带「引擎错误译法」的自动标定结果"""

    def __init__(self, entries=None, source=""):
        # entries: list of dict(en, zh, note, wrongs[list])
        self.entries = list(entries or [])
        self.source = source
        self._by_en = {}
        self._rebuild()

    # ------------------------------------------------------------ 载入
    @classmethod
    def load(cls, path):
        """从 TSV 载入。文件不存在则返回空表（程序照常工作，只是不做校正）"""
        entries = []
        if not path or not os.path.exists(path):
            return cls([], "")
        with open(path, encoding="utf-8-sig") as f:
            for raw in f:
                line = raw.rstrip("\n").rstrip("\r")
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 2:
                    continue
                en, zh = _norm(parts[0]), _norm(parts[1])
                note = _norm(parts[2]) if len(parts) > 2 else ""
                wrongs = []
                if len(parts) > 3 and _norm(parts[3]):
                    wrongs = [w.strip() for w in _norm(parts[3]).split(";") if w.strip()]
                if en and zh:
                    entries.append({"en": en, "zh": zh, "note": note, "wrongs": wrongs})
        return cls(entries, path)

    def _rebuild(self):
        """预编译：按英文长度倒序（先匹配长词，避免 "type strain" 抢了 "type strains"）"""
        self._by_en = {}
        for e in self.entries:
            self._by_en.setdefault(e["en"].lower(), e)
        self._ordered = sorted(self.entries, key=lambda x: -len(x["en"]))
        self._patterns = []
        for e in self._ordered:
            pat = r"(?<![A-Za-z0-9])" + re.escape(e["en"]).replace(r"\ ", r"[\s\-]+") + r"(?:e?s)?(?![A-Za-z0-9])"
            self._patterns.append((re.compile(pat, re.I), e))

    def __len__(self):
        return len(self.entries)

    # ------------------------------------------------------------ 标定
    def calibrate(self, engine_translate, cache_path="", log=None):
        """让引擎把每条术语裸译一次，记录它的错误译法

        engine_translate: 一次只翻一小段文本的函数
        cache_path:       标定结果缓存（术语表内容变了会自动失效）
        """
        log = log or (lambda s: None)
        if not self.entries:
            return 0
        sig = hashlib.sha1(repr([(e["en"], e["zh"]) for e in self.entries]).encode("utf-8")).hexdigest()[:16]
        cache = {}
        if cache_path and os.path.exists(cache_path):
            try:
                cache = json.load(open(cache_path, encoding="utf-8"))
            except Exception:
                cache = {}
        if cache.get("sig") == sig:
            for e in self.entries:
                key = e["en"].lower()
                got = cache.get("map", {}).get(key)
                if got:
                    for w in got:
                        if w and w not in e["wrongs"] and w != e["zh"]:
                            e["wrongs"].append(w)
            log("术语表：已用缓存标定（%d 条）" % len(self.entries))
            self._rebuild()
            return len(self.entries)

        log("术语表：首次使用，正在标定 %d 条术语（约 %d 秒）…"
            % (len(self.entries), max(1, len(self.entries) // 8)))
        mapping = {}
        n = 0
        for e in self.entries:
            try:
                out = (engine_translate(e["en"]) or "").strip()
            except Exception:
                out = ""
            out = out.strip("。.,，、 ")
            if out and out != e["zh"]:
                mapping[e["en"].lower()] = [out]
                if out not in e["wrongs"]:
                    e["wrongs"].append(out)
                n += 1
        if cache_path:
            try:
                os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                json.dump({"sig": sig, "map": mapping},
                          open(cache_path, "w", encoding="utf-8", newline="\n"),
                          ensure_ascii=False, indent=1)
            except Exception:
                pass
        log("术语表：标定完成，%d 条术语的机器译法已记录（可用于校正）" % n)
        self._rebuild()
        return len(self.entries)

    # ------------------------------------------------------------ 应用
    def terms_in(self, en_text):
        """英文句子中出现的术语（长的优先）"""
        hit = []
        for pat, e in self._patterns:
            if pat.search(en_text):
                hit.append(e)
        return hit

    def apply(self, en_text, zh_text):
        """用术语表校正一句译文：只在与英文原句对得上的地方替换"""
        if not self._patterns or not zh_text:
            return zh_text
        out = zh_text
        for e in self.terms_in(en_text):
            canon = e["zh"]
            cands = [w for w in e["wrongs"] if w and w != canon]
            # 长词优先替换，避免短串先被换掉导致长串匹配不上
            for w in sorted(cands, key=len, reverse=True):
                if w in out:
                    out = out.replace(w, canon)
        return out

    def report(self, en_text, zh_before, zh_after):
        """返回本句实际改动（用于日志/测试）"""
        return [(e["en"], [w for w in e["wrongs"] if w in zh_before], e["zh"])
                for e in self.terms_in(en_text)]


class GlossaryTranslator:
    """把任意翻译器包一层「术语校正」

    用法（不改引擎内部，风险最小）：
        base = local_engine.Translator(...)      # 或在线翻译器
        tr   = GlossaryTranslator(base, glossary)
        tr.one("The type strains were sequenced.")   → 用规范术语输出
    """

    def __init__(self, inner, glossary, log=None):
        self.inner = inner
        self.glossary = glossary
        self.log = log or (lambda s: None)

    def __getattr__(self, name):          # ok / hit / fail / quota / save 等全部透传
        return getattr(self.inner, name)

    def translate(self, text):
        return self.inner.translate(text)

    def one(self, text):
        zh = self.inner.one(text)
        if not self.glossary or not zh:
            return zh
        try:
            fixed = self.glossary.apply(text, zh)
        except Exception:
            return zh
        if fixed != zh:
            # 校正后仍要保证数字没被动过
            try:
                if not numbers_kept(text, fixed):
                    return zh
            except Exception:
                pass
        return fixed


NUM_RE_FALLBACK = re.compile(r"\d+(?:[.,]\d+)?")


def numbers_kept(en, zh):
    """英文里的数字是否都还在（防止校正把数字弄丢）"""
    nums = NUM_RE_FALLBACK.findall(en)
    return all(n in zh for n in nums) if nums else True


def load_default(log=None, cache_dir="", allow_download=False):
    """载入默认术语表；找不到就返回 None（程序照常工作）"""
    log = log or (lambda s: None)
    path = find_glossary_file()
    if not path:
        log("没有找到术语表，跳过术语校正。")
        return None
    g = Glossary.load(path)
    log("术语表：%s（%d 条）" % (os.path.basename(path), len(g)))
    return g


def find_glossary_file(explicit="", roots=None):
    """找术语表文件：显式指定 > 程序资源目录/assets/glossary > 源码目录同级"""
    if explicit and os.path.exists(explicit):
        return explicit
    roots = roots or []
    import sys
    roots.insert(0, getattr(sys, "_MEIPASS", ""))
    roots.append(os.path.dirname(os.path.abspath(__file__)))
    roots.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for r in roots:
        if not r:
            continue
        p = os.path.join(r, "assets", "glossary", DEFAULT_NAME)
        if os.path.exists(p):
            return p
    return ""


if __name__ == "__main__":
    # 自测：不用引擎，只验证「英文命中 → 译文替换」
    import tempfile
    tsv = os.path.join(tempfile.gettempdir(), "g_test.tsv")
    with open(tsv, "w", encoding="utf-8") as f:
        f.write("type strain\t模式菌株\t分类学\t类型菌株\n")
        f.write("clade\t进化支\t系统发育\t花纹\n")
    g = Glossary.load(tsv)
    print("载入条目:", len(g))
    for en, zh in [("The type strains were sequenced.", "类型菌株被测序。"),
                   ("All clades were supported.", "所有花纹都得到支持。"),
                   ("Nothing to fix here.", "这里无需修改。")]:
        print("EN :", en)
        print("原译:", zh)
        print("校正:", g.apply(en, zh))
        print()
