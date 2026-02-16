# WeChat BYD MVP（离线本地版）

这个 MVP 只做一件事：
从微信 PC 备份目录中离线提取与 **比亚迪 / BYD / 1211 / 002594** 相关的文本片段，输出 HTML 列表。

## 1. 功能说明

脚本会按以下顺序处理：

1. 优先扫描 `Backup.db`（SQLite）
   - 自动枚举表和字段
   - 优先扫描字段名含 `text/content/msg/body/chat` 或字段类型为 `TEXT/CHAR/CLOB` 的字段
   - 尝试 UTF-8 / UTF-16 / GB18030 解码
   - 命中后记录：关键词、片段（前后各 80 字）、来源（表 + rowid + 字段）

2. 如果 `Backup.db` 0 命中，或无有效文本
   - 再扫描 `BAK_0_TEXT`
   - 先尝试 gzip / zlib / zip / lz4 解压（lz4 需要依赖）
   - 无法直接解压时做 strings-like 提取
   - 命中后记录：关键词、片段、来源（文件 + 解压方式 + 偏移/块编号）

输出：

- `C:\WeChatMvp\output\BYD_hits.html`
- `C:\WeChatMvp\output\debug.json`

## 2. 环境准备（Windows）

1. 安装 Python 3.9+（勾选 Add Python to PATH）
2. 打开 `cmd` 或 `PowerShell`，进入目录：
   ```bat
   cd /d C:\WeChatMvp
   ```
3. 安装依赖：
   ```bat
   pip install -r requirements.txt
   ```

## 3. 一键运行

双击 `run.bat` 即可。

默认读取：

`C:\Users\houyutong\Documents\WeChat Files\wxid_4i112lzyz6wp12\BackupFiles\iphone_49d3f4057bb51cef8bc2591a764e0641\`

默认输出：

`C:\WeChatMvp\output\`

## 4. 命令行运行（可选）

```bat
python wechat_byd_mvp.py
```

自定义目录：

```bat
python wechat_byd_mvp.py --backup-root "你的备份目录" --output-dir "你的输出目录"
```

## 5. debug.json 里有什么

- 扫描步骤
- SQLite 扫描到的表/字段、行数、有效文本数、命中数、错误信息
- BAK_0_TEXT 的文件大小、解压尝试结果、提取块数、命中数、错误信息

> 全程本地离线，不联网、不上传数据。
