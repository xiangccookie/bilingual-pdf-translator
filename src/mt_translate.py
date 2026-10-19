# -*- coding: utf-8 -*-
"""
mt_translate.py —— 用**免费翻译接口**逐句翻译，产出与本工具链接口一致的译文

用法：
  python mt_translate.py <工作目录> [--limit N] [--sleep 0.4] [--email 你的邮箱]

它做什么：
  1 读 doc_model.json（PDF 工程）或 blocks.json（MD 工程），取出 action=translate 的块
  2 每块用 **pdf_bilingual.split_sentences()** 切句（与现成的中英对照工具同一套断句口径）
  3 逐句送去翻译，落盘成 zh\batch_NN.json —— 与人工/大模型译文格式完全相同，
     因此 build_all.py / md_render.py 可以原样复用
  4 带**磁盘缓存**（mt_cache.json）：同一句话第二次不再请求，省额度也省时间
  5 带**数字校验**：英文里的数字必须在译文里原样出现，否则重试一次；
     仍不达标就保留英文原句（宁可不译，也不能给出错误数字）

为什么用它、以及它的代价（实测）：
  · 本机实测：Google 的免费端点（translate.googleapis.com）**连不通**（超时）；
    LibreTranslate 几个公共实例也全部超时/域名解析失败；
    **只有 MyMemory 可用**（api.mymemory.translated.net），约 0.9 秒/句，无需 API key。
  · 逐句机器翻译**没有上下文**：缩写前后不一致、术语不统一、跨句指代丢失；
    质量明显低于逐句人工/大模型翻译。适合"先读个大概"，不适合对外交付。
  · MyMemory 免费额度有限（响应里有 quotaFinished 字段，额度用尽会置 true），
    整篇翻译可能中途用完，脚本会如实报告并停下。
"""
import argparse
import glob
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))   # 本目录里就有 pdf_bilingual.py / argos_backend.py
sys.path.insert(0, HERE)
from pdf_bilingual import split_sentences          # noqa: E402

API = "https://api.mymemory.translated.net/get"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


# ---------------------------------------------------------------- 后处理
def tidy(zh):
    """收拾机器翻译的排版小毛病（只动空格，不动字）"""
    z = zh
    z = re.sub(r"[（(]\s+", lambda m: "（", z)          # （ GC ） → （GC）
    z = re.sub(r"\s+[）)]", "）", z)
    z = re.sub(r"\|\s*([A-Za-z0-9_.\-]+)\s*\|", r"|\1|", z)   # | logFC | → |logFC|
    z = re.sub(r"([\u4e00-\u9fff])\s+([\u4e00-\u9fff])", r"\1\2", z)  # 中文字之间的空格
    z = re.sub(r"\s+([，。；：、）])", r"\1", z)
    z = re.sub(r"([（])\s+", r"\1", z)
    return z.strip()


NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


def numbers_kept(en, zh):
    """英文里的数字是否都在译文里出现（防止机器翻译把 0.907 变成 0.9 或丢掉）"""
    nums = NUM_RE.findall(en)
    if not nums:
        return True
    missing = [n for n in nums if n not in zh]
    return not missing


# ---------------------------------------------------------------- 网络
def request(text, pair="en|zh-CN", email="", timeout=25):
    params = {"q": text, "langpair": pair}
    if email:
        params["de"] = email
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def translate_one(text, email="", sleep=0.4, retries=3, cache=None, stats=None):
    key = hashlib.sha1(text.encode("utf-8")).hexdigest()
    if cache is not None and key in cache:
        if stats is not None:
            stats["cache"] += 1
        return cache[key]
    last = None
    for attempt in range(retries):
        try:
            j = request(text, email=email)
            if j.get("quotaFinished"):
                if stats is not None:
                    stats["quota"] += 1
                last = "quota"
                break
            zh = tidy(j["responseData"]["translatedText"])
            if not zh:
                last = "empty"
                continue
            if not numbers_kept(text, zh):
                last = "numbers"
                time.sleep(sleep)
                continue
            time.sleep(sleep)
            if cache is not None:
                cache[key] = zh
            if stats is not None:
                stats["ok"] += 1
            return zh
        except urllib.error.HTTPError as e:
            last = "HTTP %s" % e.code
            time.sleep(sleep * (attempt + 2))
        except Exception as e:
            last = type(e).__name__
            time.sleep(sleep * (attempt + 2))
    if stats is not None:
        stats["fail"] += 1
    # 兜底：保留英文原句（宁可看到英文，也不给出可能错误的数字）
    return text


# ---------------------------------------------------------------- 主流程
def load(wd):
    p = os.path.join(wd, "doc_model.json")
    if os.path.exists(p):
        return [b for b in json.load(open(p, encoding="utf-8"))
                if b["action"] == "translate"]
    p = os.path.join(wd, "blocks.json")
    d = json.load(open(p, encoding="utf-8"))
    return [b for b in d["blocks"] if b["action"] == "translate"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir")
    ap.add_argument("--limit", type=int, default=0, help="只翻前 N 块（试跑用）")
    ap.add_argument("--sleep", type=float, default=0.4, help="每句之间等待秒数")
    ap.add_argument("--email", default="", help="MyMemory 可选邮箱，提高每日额度")
    ap.add_argument("--out-tag", default="", help="缓存文件后缀，便于区分不同工程")
    ap.add_argument("--out-dir", default="", help="译文输出目录（默认 <工作目录>/zh_mt，绝不覆盖 zh/）")
    ap.add_argument("--backend", default="mymemory", choices=["mymemory", "local"],
                    help="mymemory=在线免费接口；local=本地离线 Argos（不联网、无限额）")
    a = ap.parse_args()

    # ---- 选引擎 -------------------------------------------------------
    do_translate = None
    if a.backend == "local":
        sys.path.insert(0, HERE)
        import local_engine                    # 本地引擎：CTranslate2 + sentencepiece
        _local = local_engine.Translator(log=print)

        def do_translate(s):
            """本地离线翻译：命中缓存直接用；数字对不上就保留英文。"""
            key = hashlib.sha1(s.encode("utf-8")).hexdigest()
            if key in cache:
                stats["cache"] += 1
                return cache[key]
            try:
                zh = tidy(_local.translate(s))
            except Exception:
                zh = ""
            if zh and numbers_kept(s, zh):
                cache[key] = zh
                stats["ok"] += 1
                return zh
            stats["fail"] += 1
            return s
    else:
        def do_translate(s):
            return translate_one(s, email=a.email, sleep=a.sleep,
                                 cache=cache, stats=stats)

    # ⚠️ 安全默认：写到 zh_mt/，不要碰 zh/。
    #    实测教训：第一版默认写 zh/，试跑时把某个工程已有的第 1 批译文覆盖掉了。
    out_dir = a.out_dir or os.path.join(a.workdir, "zh_mt")
    if os.path.abspath(out_dir) == os.path.abspath(os.path.join(a.workdir, "zh")) \
            and "--force" not in sys.argv:
        sys.exit("拒绝写入 zh/（那是人工/大模型译文的目录）。"
                 "要覆盖请显式加 --force，或改用 --out-dir。")
    os.makedirs(out_dir, exist_ok=True)

    blocks = load(a.workdir)
    if a.limit:
        blocks = blocks[:a.limit]
    # 缓存按引擎分开：本地与在线是两套译文，混用会让「本地模式」直接复用在线结果
    cache_p = os.path.join(a.workdir, "mt_cache_%s%s.json" % (a.backend, a.out_tag))
    cache = json.load(open(cache_p, encoding="utf-8")) if os.path.exists(cache_p) else {}

    stats = {"ok": 0, "cache": 0, "fail": 0, "quota": 0}
    t0 = time.time()
    # 按批次写（每 1200 词一批，与 build_all 的期望一致；这里简单按块数 25 一批）
    bs = 25
    total_sent = 0
    for bi in range(0, len(blocks), bs):
        chunk = blocks[bi:bi + bs]
        out = {}
        for b in chunk:
            sents = split_sentences(b["text"])
            if not sents:
                continue
            out[b["id"]] = [do_translate(s) for s in sents]
            total_sent += len(sents)
            if stats["quota"]:
                break
        if out:
            dst = os.path.join(out_dir, "batch_%02d.json" % (bi // bs + 1))
            json.dump(out, open(dst, "w", encoding="utf-8", newline="\n"),
                      ensure_ascii=False, indent=1)
            json.dump(cache, open(cache_p, "w", encoding="utf-8", newline="\n"),
                      ensure_ascii=False)
            done = min(bi + bs, len(blocks))
            print("  批次 %d：块 %d–%d，累计 %d 句，用时 %.0f 秒"
                  % (bi // bs + 1, bi + 1, done, total_sent, time.time() - t0))
        if stats["quota"]:
            print("⚠ 免费额度已用尽（quotaFinished），已停止。用完额度后隔天再跑，或换邮箱。")
            break

    json.dump(cache, open(cache_p, "w", encoding="utf-8", newline="\n"),
              ensure_ascii=False)
    print("\n完成：翻译 %d 句 / 缓存命中 %d 句 / 失败 %d 句 / 额度用尽 %d 次，用时 %.1f 分钟"
          % (stats["ok"], stats["cache"], stats["fail"], stats["quota"],
             (time.time() - t0) / 60))
    print("译文已写入 %s\\batch_*.json（与 zh/ 同格式）" % out_dir)


if __name__ == "__main__":
    main()
