# pdf-paper-translate

把一篇**英文 PDF**（学术论文为主）翻译成中文，并**原样保留版面**：图、公式、表格、参考文献
都不动，只把被翻译过的文字块替换成中文，仍放回它原来的位置、原来的行网格上；另可生成一份
**左原文 / 右译文**的对照版 PDF。

以一个可直接复用的 **Command Code Skill** 形式提供（`SKILL.md` + 两个脚本 + 一份深入说明）。

---

## 特点

- **不重排整篇**：以原文页为画布，逐块 redact 掉被翻译的文字（图片、矢量图形一律保留），
  再在**同一个 bbox**、同一条行网格上重排译文——字号与原文西文视觉等大、两端对齐、行距自动
  把块高填满（不硬拉字距）。
- **只翻该翻的**：公式、表格、图内整块、参考文献原样保留；表格只译表题，图内标签按原基线逐行替换。
- **产出两版**：纯译文 PDF（与原文**逐页 1:1**：页数、尺寸、章节/图注/表题/段落位置对齐），
  以及左原文 / 右译文的对照版。
- **纯脚本**：只依赖 `pymupdf` 与 `fontTools`，无外部服务、无网络调用。

---

## 目录结构

```
pdf-paper-translate/
├── SKILL.md                    # 技能说明与完整工作流
├── scripts/
│   ├── pdf_translate.py        # diagnose / extract / render 三个子命令
│   └── make_side_by_side.py    # 生成左原文 / 右译文对照版
└── references/
    └── workflow.md             # 版面字段推导、字体原理、故障排查
```

---

## 依赖

```bash
python3 -m pip install pymupdf fonttools otf2ttf
```

- 需要命令行工具 `pyftsubset`、`otf2ttf` 在 `PATH` 上（随 `fonttools` / `otf2ttf` 一起安装）。
- 字体使用 **Noto Serif CJK SC**（Regular + Bold，OTF），放到源 PDF 同级的 `fonts/` 目录：

  ```
  fonts/NotoSerifCJKsc-Regular.otf
  fonts/NotoSerifCJKsc-Bold.otf
  ```

  脚本会自动把字体**子集化**并 CFF→TTF 后再嵌入（直接嵌完整 OTF 会让体积膨胀约十倍，且 mupdf
  渲染 CFF 子集会用错 GID 导致乱码）。

---

## 使用

下面用 `xx.pdf` 代指你的源文件。

### ① 诊断版面

```bash
python3 scripts/pdf_translate.py diagnose "xx.pdf"
```

逐页输出 CJK 占比与文字块数量，判断是「单语原文」还是「原文 / 译文交替」的双语文件。

### ② 导出待翻译块

```bash
python3 scripts/pdf_translate.py extract "xx.pdf" --compact --out work/xx.compact.json
```

`--compact` 只导出**待翻译块**，按页分组，字段为 `key` / `size` / `bold` / `hint` / `text`，
不含 bbox；公式、表带、图区和参考文献已过滤掉，体积约为完整清单的一半，适合直接读入翻译。
需要坐标或排查分类时，再用完整版：

```bash
python3 scripts/pdf_translate.py extract "xx.pdf" --out work/xx.blocks.json
```

完整清单里每块含 `key`（形如 `p<页>:b<块号>`）、页码、bbox、字号、是否加粗、一个粗分类提示
（`body` / `heading` / `title` / `caption` / `formula` / `table` / `figure` / `reference`）以及
去连字符后的原文，并附上 `fig_regions`、`table_bands` 等建议值。

> 两种 `extract` 输出都只是清单，不能直接交给 `render`；仍需按 `SKILL.md` 编写
> `translations.json`。

### ③ 翻译成 `translations.json`

只翻提示为 `body` / `heading` / `title` / `caption` 的块；公式、表格、图内整块、参考文献不翻。
引用痕迹（`[1]`、`(Author et al., 2020)` 等）**保留原样**即可，`render` 会自动清洗。

### ④ 重建译文 PDF

```bash
python3 scripts/pdf_translate.py render "xx.pdf" work/xx.translations.json \
    --out "xx-zh.pdf" --fonts ./fonts --work ./build --quiet
```

结束会打印验收报告。`--quiet` 会隐藏逐页计数、字体日志、`diagnose` 明细和约 430 行逐块填充率
表，只保留 `!!` 问题行和 `问题页数`；日常渲染都建议加上，排查版面时再去掉。报告里的
`et_al_pages: "auto"`（默认）会把保留表格/图内残留的 `et al.` 原位改成 `等人`，并自动跳过
参考文献页。**所有 `!! 缺少译文`、`!! 溢出` 都要处理掉。**

### ⑤ 生成对照版

```bash
python3 scripts/make_side_by_side.py "xx.pdf" "xx-zh.pdf" --out "xx-dual.pdf"
```

每页尺寸 `(2W+G)×H`，中缝 `x = W + G/2` 处一条细灰分隔线（`--gutter` 设置 `G`，默认 24pt）。

---

## `translations.json` 结构

```json
{
  "params": { "body_ratio": 0.98, "l0": 1.28, "lmax": 1.72, "indent_em": 2.0,
              "head_size": 9.6, "title_size": 16.5, "hmin": 10.5, "alpha": 0.35 },
  "layout": {
    "keep_ids": [], "no_indent": [], "widen": {}, "affil_ids": [],
    "table_bands": {}, "fig_regions": {}, "references_pages": [],
    "references_heading_ids": [], "title_ids": [], "et_al_pages": "auto"
  },
  "blocks":        { "p0:b0": "xx", "p0:b1": "xx" },
  "figure_labels": { "xx": "xx" },
  "affiliations":  { "xx": "xx" }
}
```

字段含义（多数有默认值，按需填）：

- `blocks` / `figure_labels` / `affiliations`：逐块译文 / 图内标签译文 / 作者单位译文。
- `keep_ids`：原样保留、不 redact 的块（作者行、水印、被拆散的宽公式块）。
- `no_indent`：跨块或跨页的续行段（不首行缩进）。
- `widen`：右边无内容的单行短块，放宽其右边界。
- `affil_ids`：作者单位块（按行居中替换）。
- `fig_regions` / `table_bands`：图区与表带（按 1-based 页码），决定哪些块当作图内标签、哪些当作表格保留。
- `references_pages`：原样保留的参考文献页。
- `et_al_pages`：保留表格/图内残留 `et al.` 原位改成 `等人` 的页码；默认 `"auto"` 表示除
  `references_pages` 外的所有页，也可给 1-based 页号列表，`null` / `[]` 关闭。
- `title_ids` / `references_heading_ids`：按标题渲染的块。
- `params`：字号、行距等排版参数，一般用默认值。

---

## 省 token 的工作流

翻译本身不贵，反复读大文件和反复渲染才贵。建议固定成「读一次 / 一次翻译 / 一次渲染 / 一次验收」：

1. **读一次**：`extract --compact`，只读当前要翻的页组，不要反复读完整 `blocks.json`。
2. **一次翻译**：按页组写成最终 key/value，最后合并一次；小修用定点编辑，不要整文件重写。
3. **一次渲染**：始终 `render --quiet`，根据这一份报告改完所有 `!!` 再重渲。
4. **一次验收**：最后抽 3–5 页渲染 PNG；不要每改一页就看一遍图。

版面问题优先用 `layout` 配置（`keep_ids` / `table_bands` / `fig_regions` / `et_al_pages`）解决，
少做「看一页 → 改脚本 → 全篇重渲」的补丁循环。

---

## 安装为 Skill

```bash
# 仅当前项目
cp -r pdf-paper-translate .commandcode/skills/
# 或：所有项目通用
cp -r pdf-paper-translate ~/.commandcode/skills/
```

安装后可用 `/pdf-paper-translate` 调用。默认目标是中文；若要翻译成其他语言，需同时调整脚本里的
`LANG_PUNCT`（标点子集）与 `clean_citations()` 的替换词，详见 `references/workflow.md`。

---

## 已知边界

- 版面按**两栏 Letter**（612×792 pt，栏位 x≈54–298 / 315–559）等比缩放；单栏或特殊版式会退化，
  需要微调。
- 图区、表带、保留块、续行等仍需**逐篇校准**：`extract` 会给出建议，但需人工或模型复核——这是
  「保留版面翻译」的固有代价。
- 字体子集按实际绘制字符生成，出现缺字会直接报错并列出缺失码位。

> Skill 同时是「指令 + 可执行代码」，拉取与使用前请先审阅脚本内容。

---

## Prompt

```
用 Python + PyMuPDF（pymupdf）跑主流程、fontTools 的 pyftsubset 做字体子集化、otf2ttf 把 CFF 转成 TTF、译文用任意一款衬线 CJK 字体（如 Noto Serif CJK SC），不需要额外 skill 或 MCP，纯脚本即可；拿到双语或带译文的 PDF 后先诊断（逐页统计 CJK 占比判断是否「原文页 / 译文页」交替、对比每页 text block 数量与 bbox 是否重合，块数相等就按 (round(y0/k), x0) 排序做顺序配对，k 取小整数；个别页块数不等就改用单调 DP 对齐，代价 = |Δy0| + α|Δ高度|、跳过惩罚取一个略大于行高的常数），然后不要重排整篇，而是以原文页为画布，只把被翻译过的文字块 redact 掉（images 和 graphics 设 NONE，只删文字），再在同一个 bbox、同一条行网格上用 insert_htmlbox 重排译文——正文字号 S 取与原文西文视觉等大的值、两端对齐、行距先按 L0 倍试排再用返回的 spare/scale 反推行数把块高填满（上限 Lmax 倍，填不满就留白，不硬拉字距）、首行缩进 I 个字符、原文首行是 Bold 的就把译文到第一个句号包进 <b>、标题不能用 insert_htmlbox（紧矩形下会静默失败返回 -1）而要 insert_textbox 配合原字号加粗（居中标题 = 字号 ≥ Hmin 且原文不以数字开头，以数字开头的是章节号左对齐），图、公式、参考文献和原本就没翻译的原文一律原样保留；引用清洗按顺序做：先 NFC 规范化 → 「作者 et al. (年份)」型替换成「该文献」→ 删残留的「X et al.,」→ 括号内含 et al 或四位年份的整组删成一个空格（缩写、(§x.y)、(A) 这类保留不动）→ 清掉分页切断的悬挂括号和句首残留的年份碎片，句首括号引用后面紧跟动词的仍换成「该文献」以保语法；图内标签不整块重排而是逐行替换，按原文每一行的原基线和水平居中等宽重画译文，字号取 min(原字号×s, 行宽/字数)；表格如果原译文已经错位或溢出，就保留原表格只翻译表题（窄块宽 < Wc 且字号 < Hmin 直接跳过，表题正则例外）；字体必须先用 pyftsubset 子集化再用 otf2ttf 转 TTF（直接嵌完整 OTF 会让文档体积膨胀一个量级，doc.subset_fonts() 对 CFF/CID 字体无效，且 mupdf 渲染 CFF 子集会用错 GID 导致乱码，转 TTF 后才正常）。

最终输出三个文件：/content/xxx-zh.pdf 是纯译文版，N 页、W×H、页面结构与原文逐页一一对应（章节标题、图注、表题、段落起止位置都对齐，图表格保持原样）；/content/xxx-dual.pdf 是左原文右译文的对照版，N 页、页面尺寸 (2W+G)×H，每页左半放原文页、右半放对应译文页、中缝 x = W + G/2 处一条细灰线分隔；以及原始文件；还有另外留下 rebuild_zh.py（重建译文版）和 make_side_by_side.py（拼对照版）两个可复跑脚本。验收时抽若干页渲染成 PNG 亲眼检查标题字号、图内标签位置、表格是否仍是原样、有无缺字方块，并扫一遍确认译文块里不再出现「et al」。

可以试一下https://github.com/bilibili/Index-Translate项目以及https://github.com/wshuyi/translate-pdf-skill这个skill来加速翻译，尽可能的确保格式正确

文件是：/xxxxx.pdf
```
