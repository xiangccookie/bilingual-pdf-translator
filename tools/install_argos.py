# -*- coding: utf-8 -*-
"""
install_argos.py —— 下载并安装「英→中」离线模型（不依赖 argostranslate）

做什么：
  1 从 argos-net.com 下载 translate-en_zh-1_9.argosmodel（67.5 MB，本质是 zip）
  2 解压到程序自己的数据目录 <数据目录>/model/translate-en_zh-1_9
  3 用几句真实论文句子试译，确认能跑

为什么不用 argostranslate 自带的安装器：
  它会连带装 stanza/MiniSBD 分句器（运行时要联网下模型），
  而且数据目录走 settings.data_dir / package_dirs 两套机制，改一个不生效。
  本项目用 local_engine.py 直接调 CTranslate2 + sentencepiece，模型只要「解压到位」即可。

用法：
  python tools/install_argos.py            # 下载并安装 + 试译
  python tools/install_argos.py --where    # 只看模型在哪、装没装
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import local_engine as le   # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--where", action="store_true", help="只显示模型位置与状态")
    a = ap.parse_args()

    print("数据目录：", le.app_data_dir())
    print("选择说明：", le.dir_note())
    print("模型目录：", le.model_dir())
    print("模型就绪：", le.model_ready())
    print("随身模型：", le.bundled_model() or "(没有)")

    if a.where:
        return 0

    if not le.ensure_model(log=print, allow_download=True):
        print("安装失败：请检查网络，或手动把 .argosmodel 放进 assets/ 目录。")
        return 1

    print("\n试译（真实论文句子）：")
    t = le.Translator(log=print)
    for s in ("Gastric cancer (GC) is a major global health challenge.",
              "The tumor microenvironment facilitates immune evasion.",
              "CTHRC1, CST6, and AKR1B1 are independent prognostic factors with HR > 1."):
        print("  EN:", s)
        print("  ZH:", t.translate(s))
    print("\n完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
