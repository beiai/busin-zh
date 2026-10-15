# BUSIN ~Wizardry Alternative~

日版 PlayStation 2 游戏的中文翻译，产品编号 SLPM-62098。

仓库里是译文，以及把译文做成新光盘的程序。原来的光盘不改，也不放进仓库。

## 翻译范围

- 剧本：对白、旁白、说明
- 界面：菜单、建角、商店、战斗和地名
- 术语：人名、地名、职业、种族，全篇用同一个译名
- 文字：游戏字库里没有的字，用思源宋体，思源黑体补上
- 画面：需要改字的图

## 译文放在哪里

| 路径 | 内容 |
|---|---|
| `data/text/` | 剧本。一块一个文件 |
| `data/interface/` | 界面句子 |
| `data/terms.json` | 术语 |
| `data/font/` | 字库 |
| `data/screen/` | 改过的图 |

这些文件由人修改。构建只读取它们，不把结果写回这里。

## 修改剧本

新开一块时，先导出原文，译文留空：

```powershell
python -m busin init-text 块号
```

译好的片段写回这一块，再对照原盘核对。同一句原文出现在别的块里时，把译文抄过去：

```powershell
python -m busin apply-text 块号 片段文件
python -m busin check-text 块号
python -m busin copy-text 来源块 目标块
```

界面、术语、字表和图片直接改上表里的文件，然后重新构建。

## 做出光盘

把日版原盘的路径写进仓库根目录的 `config.toml`，例如 `disc = "games/game.bin"`。相对路径从仓库根目录算。这个文件不提交。没有它时构建会停，读盘测试会跳过。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
python -m busin build
python -m pytest tests -q
```

成品是 `build/image/busin-zh.cue`。用它从头开机。同样的译文，每次构建得到的光盘字节相同。

## 目录

| 目录 | 是什么 |
|---|---|
| `busin/` | 构建程序 |
| `data/` | 译文、字表、改过的图 |
| `tests/` | 测试 |
| `build/` | 构建结果，可以删 |
| `tmp/` | 临时文件，可以删 |
