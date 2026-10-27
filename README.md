# 双语 PDF 翻译器 · Bilingual PDF Translator

把**英文学术 PDF** 变成「**一句英文，紧跟这句中文**」的双语 PDF。
批量处理、自动按论文标题命名、**完全离线可用**（模型随程序打包，无需 API Key、无需联网、无额度限制）。

> **English**: Turn English academic PDFs into bilingual PDFs where **each English
> sentence is immediately followed by its Chinese translation**. Batch processing,
> automatic naming from the paper title, fully offline (Argos model bundled in the
> release). Windows 10/11. No Python or API key needed for end users.

---

## 普通用户：点这里下载（不用装任何东西）

# ⬇️ https://github.com/xiangccookie/bilingual-pdf-translator/releases/latest

点开上面的链接 → 在 **Assets** 里挑一个下载。两种形态功能完全一样，**都自带离线模型和术语表**：

| 下载哪个 | 怎么用 | 适合谁 |
| --- | --- | --- |
| `BilingualPdfTranslator-windows.zip` | 解压后双击里面的 `BilingualPdfTranslator.exe` | **推荐**。启动快（约 1 秒）。解压出来的 `_internal` 文件夹不能删。 |
| `BilingualPdfTranslator-onefile.exe` | 直接双击 | 想要「就一个文件」便于转发时用。首次启动要把约 140 MB 解到临时目录，会慢十几秒。 |

- 不需要安装 Python、不需要 pip、不需要联网
- 首次启动约 10 秒（把离线模型解压到用户目录，只做一次）
- Windows 提示「未知发布者」时：更多信息 → 仍要运行

> 如果你是用绿色 **Code → Download ZIP** 下的源码包（里面没有程序本体）：
> 解压后双击 **启动.bat**，它会自动把程序本体取回来（只需一次）。

---

## 一、给使用者：下载 → 解压 → 双击

1. 到本仓库 **Releases** 页下载 `BilingualPdfTranslator-windows.zip`（约 140 MB）。
2. 解压到任意目录（**不要**只把 exe 单独拖出来，它旁边还有 `_internal` 文件夹）。
3. 双击 `BilingualPdfTranslator.exe`。

**不需要 Python、不需要配置环境变量、不需要联网、不需要 API Key。**
首次运行会自动把随包的离线模型解压到你的用户目录（一次性，约 10 秒）。

界面三步：**添加文章**（可多选 / 整个文件夹）→ **选输出文件夹** → **开始翻译**。

> Windows 会提示「未知发布者」——因为没有购买代码签名证书。点
> 「更多信息 → 仍要运行」。

## 一点五、也可以直接下载源码包（Code → Download ZIP）

如果你点的是绿色的 **Code → Download ZIP**（只有约 200 KB 的源码，没有程序本体），
解压后**双击里面的 启动.bat** 即可：

1. 它会自动检查本机有没有程序包
2. 没有就从本仓库的 Releases 下载（约 140 MB，**只需一次**）
3. 解压到 %LOCALAPPDATA%\\BilingualPdfTranslator\\app 并启动

也就是说：**Code 下载 + 双击 启动.bat = 也能直接用**（首次需要联网下载，之后不再联网）。

> 为什么程序本体不直接放进仓库：GitHub 仓库**单文件上限 100 MB**，
> 而程序包含 Python 运行时与 67.5 MB 离线模型，共约 142 MB。
> 所以它只能放在 Release 附件里，启动.bat 就是替你去把它取回来。

## 二、它能做什么

- **逐句对照**：英文原句后紧跟这句中文。英文句号本身就是分界，中文前不加逗号、只留一个空格；
  英文本身没有句号的小标题才用逗号隔开。
- **自动剔除**页眉页脚、期刊前置信息（`OPEN ACCESS` / `EDITED BY` / DOI / 版权声明）。
- 按字号识别**标题与小标题**；识别**参考文献区并保留英文原文**（学术惯例：书目数据必须可原样检索）。
- **表格与图内文字**：整块**裁成图片保留原貌**，不做逐格翻译（逐格翻译会毁掉表格）。
- **图注**按 `Figure 3 / 图 3` 对照，并在图注前插入英文原图。
- **批量**：一次加入多篇，逐篇跑完；某篇出错只跳过那篇。
- **自动命名**：从首页提取论文标题并译成中文当文件名（也可选英文标题 / 原文件名）。
- 中文按 **CJK 规则断行**，不会因为中文没有空格而整句跳到下一行。

## 三、它做不到什么（重要）

- **不保证术语准确**。翻译是机器翻译。真实测到的错例：
  `clades` → 「阴道」、`psoriasis`（银屑病）→「磷酸盐」、`taurine`（牛磺酸）→「陶林」、
  `gold standard`（金标准）→「金本位」、`GenBank` →「基因bank/银行」。
  **适合快速通读与筛选，不能替代人工译稿。**
- **不翻译参考文献**；**不处理扫描版 PDF**（没有文字层需要先 OCR）。
- 只支持 **英→中** 一个方向。

程序内置一道保险：**英文里的数字必须在译文里原样出现**，否则该句保留英文原文
——宁可让你看到英文，也不给出可能错误的数字。

## 四、专业术语表（生物医学）

机器翻译认得词、但不认行话。实测引擎的原始输出 vs 术语校正后：

| 英文 | 裸译 | 校正后 |
|---|---|---|
| type strains | 类型菌株 | **模式菌株** |
| clades | 花纹 | **进化支** |
| psoriasis | 多病病人 | **银屑病** |
| in silico | 硅酸盐 | **计算机模拟** |
| hazard ratio | 危险比率 | **风险比** |
| GenBank | 基因银行 | **GenBank**（保留原文） |

整篇实测（同一篇 2 页、同一引擎）：

| 检查项 | 不开术语表 | 开术语表 |
|---|---|---|
| 「磷酸盐」（错） | 3 次 | **0 次** |
| 「银屑病」（对） | 0 次 | **3 次** |
| 「置信间隔」（错） | 1 次 | **0 次** |
| 「置信区间」（对） | 0 次 | **1 次** |

**怎么做到的**：没有用「占位符保护」（实测这个模型会把 `ZXQ12` 译成 `ZQQ12`、
把句中的中文吃掉），而是**译后校正 + 自动标定**——首次运行时让引擎把每条术语裸译一次、
记下它的错误译法，之后在整句译文里定点纠正；替换前先确认英文原句里确实出现了该术语，
所以不会误伤别处正常用词。

**怎么加词**：`assets/glossary/biology.tsv`，Tab 分隔四列
`英文 <Tab> 规范中文 <Tab> 备注 <Tab> 已知错误译法（分号分隔）`，
记事本改完保存、重开程序即可，**不用重新打包**。界面上有 **「专业词汇表…」** 按钮——点一下直接用记事本打开**你自己的那份**（首次会自动复制一份到 %LOCALAPPDATA%\BilingualPdfTranslator\glossary\biology.tsv，升级不丢），改完点「重载术语表」立刻生效。
现有 **130 条**，覆盖分类学/组学/疾病/统计/数据库名。
引擎偶尔会换个说法（	ype strains 时而是「类型菌株」时而是「种类菌株」），
程序会把「没能校正的术语」列在日志里，照着补进第 4 列即可。
细节与边界见 [docs/术语表.md](docs/术语表.md)。

> 术语表只让**术语**对，不会让译文变通顺；也不覆盖表外的词。

## 五、两种翻译引擎

| | 本地离线（默认） | 在线免费（MyMemory） |
|---|---|---|
| 联网 | **完全不需要** | 需要 |
| 费用/额度 | 0 元、无限制 | 免费但**每天有额度**，用尽返回 429 |
| 速度（实测） | 0.05–0.15 秒/句；9 页 194 句整篇约 45 秒 | 约 0.9 秒/句，易被限流 |
| 模型体积 | 下载 67.5 MB，解压后约 82 MB | 无 |

## 六、开发：从源码运行

```bash
git clone https://github.com/xiangccookie/bilingual-pdf-translator.git
cd bilingual-pdf-translator
pip install -r requirements.txt
python src/bi_app.py                 # 图形界面
```

离线模型（首次，或直接用随身模型）：

```bash
python tools/install_argos.py        # 从 argos-net.com 下载 en→zh（约 67.5 MB）
```

## 七、开发：自己打包

```powershell
.\tools\build_exe.ps1                # Windows PowerShell
```

脚本会：① 缺模型就下载 → ② PyInstaller 打**目录版**并压成 zip → ③ 造个 2 页 PDF 跑一遍自检。
产物：`dist\BilingualPdfTranslator-windows.zip`。

> 推 `v*` 标签时，GitHub Actions 会自动完成同样的事并把 zip 挂到 Release（见 `.github/workflows/`）。

## 八、命令行

```powershell
# 批量：输出文件夹 + 多篇 PDF
BilingualPdfTranslator.exe --batch-out D:\输出 --backend local --naming zh 论文1.pdf 论文2.pdf

# --naming : zh=标题中译（默认） en=标题英文 orig=原文件名
# --backend: local=本地离线（推荐） mymemory=在线免费

# 单篇
BilingualPdfTranslator.exe --selftest 输入.pdf 输出.pdf --backend local

# 诊断（出问题时请把这段的输出发出来）
BilingualPdfTranslator.exe --where
```

退出码 `0` = 全部成功。运行日志写在 `--where` 打印的 `log_file` 里。

## 九、项目结构

```
├─ src/
│  ├─ bi_app.py          界面 + 批量流程 + 标题提取 + 命名
│  ├─ pdf_bilingual.py   排版引擎（块分类、图注处理、双语排版）
│  ├─ local_engine.py    离线引擎（CTranslate2 + sentencepiece 直接跑 Argos 模型）
│  ├─ glossary.py        专业术语表（译后校正 + 自动标定）
│  └─ mt_translate.py    纯脚本版翻译器（可脚本化批量）
├─ tools/
│  ├─ install_argos.py   下载/安装离线模型
│  ├─ build_exe.ps1      本地打包（目录版 + zip）
│  └─ runtime_hook.py    PyInstaller 运行时钩子（隐藏控制台、兜住 stdout）
├─ assets/               离线模型（**不进 git**，构建时下载）+ glossary/ 术语表（进 git）
├─ docs/使用说明.md      给使用者的详细说明
├─ docs/术语表.md        术语表怎么用、怎么加词、边界在哪
└─ .github/workflows/    CI：语法检查 + 打标签自动出 zip
```

## 十、已知问题与设计取舍（都是踩出来的）

| 现象 | 原因 / 处理 |
|---|---|
| `--windowed` 打包后一启动就退出，连日志都没有 | 无控制台时 `sys.stdout` 是 `None`，第三方库在 import 阶段写它就崩。**改用 `--console` + 运行时钩子隐藏窗口**，并给 stdout 兜底 |
| 单文件版报 `Could not create temporary directory!` | 单文件每次运行都要把约 140 MB 解包到**临时目录**。临时目录不可写时直接失败——实测把它放在某些受限目录下必现。**日常用目录版**（不依赖临时目录）；单文件版只当转发用，碰到这个报错就换目录版 |
| 中文目录下报 `NOT_FOUND: …sentencepiece.model`（文件明明在） | sentencepiece 的 C++ 实现用窄字符 API，打不开非 ASCII 路径。**先 chdir 进模型目录再用相对名加载** |
| 装了 argostranslate 却一翻译就崩，报 huggingface 超时 | 它默认用 stanza 分句，首次运行要联网下模型。**本项目自己分句**，并直接用 CTranslate2 + sentencepiece，不依赖 argostranslate |
| 打包体积超过 1 GB | torch/spacy/stanza 只被上面的分句器用到。全部 `--exclude-module` 掉，省约 164 MB |
| 表格被拆成几百个单元格 | 检出「短块密集成片」的区域 → 剔文字 + **整块裁成图片** |
| 同一张图被插两次 | 按 `caption_id` 精确匹配图注（原来按文字前缀，`Table 1 provides…` 也会命中 `Table 1`） |
| 封面标题取成期刊名 | 首页可能有多个最大字号块，只取**论文标题那一带**的块，不整页拼接 |
| 作者行出现重复 | 逐句去重：某句中译与英文实质相同就只印英文（避免 `Xinping Sun，Xinping Sun`） |
| `Start-Process -Wait` 调本程序会卡住 | 实测现象，`tools/build_exe.ps1` 里改成直接调用 `&` |

## 十一、许可

MIT（见 `LICENSE`）。第三方组件：
PyMuPDF（AGPL-3.0）、ReportLab（BSD-3）、CTranslate2（MIT）、sentencepiece（Apache-2.0）、
Argos en→zh 模型（来自 OPUS 语料，CC-BY-4.0，构建时下载）。
中文字体使用系统自带的微软雅黑，**不随程序分发**。
