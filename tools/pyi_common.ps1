# 打包参数的**唯一来源**。
# 本地（tools/build_exe.ps1）与 CI（.github/workflows/build-release.yml）都 dot-source 本文件。
#
# 为什么单独抽出来（这不是洁癖，是真出过事）：
#   历史上 CI 里复制了一份 pyinstaller 命令行。后来本地脚本补上了
#   「--add-data assets/glossary」，CI 那份没跟上 —— 于是发布出来的包
#   「术语表功能凭空消失」。而程序找不到术语表时**不报错**，只在日志里写一行
#   「没有找到术语表，跳过术语校正」，用户只会觉得"这功能没了"，根本查不出来。
#   ⇒ 参数只留这一处，两边都读它，就不可能再漂移。
#
# 改这里之前请先看 README「已知问题与设计取舍」。

$PyiCommonArgs = @(
    '--console',                                                # 不用 --windowed，理由见 README 已知问题第 1 行
    '--paths', 'src',
    '--add-data', 'assets/translate-en_zh.argosmodel;assets',   # 离线模型：目标机器无需联网
    '--add-data', 'assets/glossary;assets/glossary',            # 术语表：缺了会静默失效，必须打
    '--runtime-hook', 'tools/runtime_hook.py',
    '--collect-submodules', 'reportlab',
    '--collect-data', 'reportlab',
    '--collect-submodules', 'ctranslate2',
    '--collect-submodules', 'sentencepiece',
    '--exclude-module', 'argostranslate',
    '--exclude-module', 'torch',
    '--exclude-module', 'spacy',
    '--exclude-module', 'stanza',
    '--exclude-module', 'minisbd',
    '--exclude-module', 'thinc',
    '--exclude-module', 'onnxruntime',
    '--exclude-module', 'weasel',
    '--exclude-module', 'typer'
)
