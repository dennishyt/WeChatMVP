#!/usr/bin/env python3
import argparse
import datetime as dt
import gzip
import html
import io
import json
import os
import re
import sqlite3
import sys
import zipfile
import zlib
from typing import Any, Dict, List, Optional, Tuple

KEYWORDS = ["比亚迪", "BYD", "1211", "002594"]
CONTEXT_CHARS = 80

FIELD_NAME_HINT = re.compile(r"(text|content|msg|body|chat)", re.IGNORECASE)

SEARCH_PATTERNS = [
    ("比亚迪", re.compile(r"比亚迪")),
    ("BYD", re.compile(r"(?<![A-Za-z0-9])BYD(?![A-Za-z0-9])", re.IGNORECASE)),
    ("1211", re.compile(r"(?<!\d)1211(?!\d)")),
    ("002594", re.compile(r"(?<!\d)002594(?!\d)")),
]


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def safe_decode_bytes(data: bytes) -> Optional[str]:
    for enc in ("utf-8", "utf-16", "utf-16le", "utf-16be", "gb18030"):
        try:
            text = data.decode(enc)
            if text and any(ch.isprintable() for ch in text):
                return text
        except UnicodeDecodeError:
            continue
    text = data.decode("utf-8", errors="ignore")
    return text if text.strip() else None


def find_hits_in_text(text: str) -> List[Tuple[str, int, int]]:
    hits = []
    for keyword, pattern in SEARCH_PATTERNS:
        for m in pattern.finditer(text):
            hits.append((keyword, m.start(), m.end()))
    return sorted(hits, key=lambda x: x[1])


def snippet(text: str, start: int, end: int, width: int = CONTEXT_CHARS) -> str:
    s = max(0, start - width)
    e = min(len(text), end + width)
    out = text[s:e].replace("\n", " ").replace("\r", " ")
    return re.sub(r"\s+", " ", out).strip()


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def scan_sqlite(db_path: str, debug: Dict[str, Any]) -> List[Dict[str, str]]:
    hits: List[Dict[str, str]] = []
    section = {
        "path": db_path,
        "tables": [],
        "scan_rows": 0,
        "candidate_fields": 0,
        "decoded_text_cells": 0,
        "errors": [],
    }
    debug["sqlite"] = section

    if not os.path.exists(db_path):
        section["errors"].append("Backup.db not found")
        return hits

    try:
        conn = sqlite3.connect(db_path)
    except Exception as e:
        section["errors"].append(f"open failed: {e}")
        return hits

    conn.row_factory = sqlite3.Row
    try:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        for table_row in tables:
            table = table_row[0]
            table_info = {"table": table, "fields": [], "errors": []}
            section["tables"].append(table_info)
            try:
                cols = conn.execute(f"PRAGMA table_info({quote_ident(table)})").fetchall()
            except Exception as e:
                table_info["errors"].append(f"pragma failed: {e}")
                continue

            candidates = []
            for c in cols:
                name = c[1]
                col_type = (c[2] or "").upper()
                is_candidate = bool(FIELD_NAME_HINT.search(name)) or any(
                    t in col_type for t in ("TEXT", "CHAR", "CLOB")
                )
                if is_candidate:
                    candidates.append(name)
                    table_info["fields"].append({"name": name, "type": col_type, "candidate": True})
                else:
                    table_info["fields"].append({"name": name, "type": col_type, "candidate": False})

            section["candidate_fields"] += len(candidates)
            for col in candidates:
                try:
                    q = f"SELECT rowid AS __rowid__, {quote_ident(col)} AS __val__ FROM {quote_ident(table)} WHERE {quote_ident(col)} IS NOT NULL"
                    rows = conn.execute(q)
                    use_rowid = True
                except Exception:
                    q = f"SELECT {quote_ident(col)} AS __val__ FROM {quote_ident(table)} WHERE {quote_ident(col)} IS NOT NULL"
                    try:
                        rows = conn.execute(q)
                    except Exception as e:
                        table_info["errors"].append(f"scan {col} failed: {e}")
                        continue
                    use_rowid = False

                for i, r in enumerate(rows, 1):
                    section["scan_rows"] += 1
                    raw = r["__val__"]
                    rowid = r["__rowid__"] if use_rowid else i
                    text = None
                    if isinstance(raw, str):
                        text = raw
                    elif isinstance(raw, bytes):
                        text = safe_decode_bytes(raw)
                    elif raw is not None:
                        text = str(raw)

                    if not text:
                        continue
                    section["decoded_text_cells"] += 1
                    for kw, s, e in find_hits_in_text(text):
                        hits.append(
                            {
                                "keyword": kw,
                                "snippet": snippet(text, s, e),
                                "source": f"table={table};rowid={rowid};field={col}",
                            }
                        )
    finally:
        conn.close()

    section["hits"] = len(hits)
    return hits


def decompress_candidates(data: bytes, debug_section: Dict[str, Any]) -> List[Tuple[str, bytes]]:
    candidates = [("raw", data)]

    for name, fn in (
        ("gzip", gzip.decompress),
        ("zlib", zlib.decompress),
    ):
        try:
            out = fn(data)
            if out and out != data:
                candidates.append((name, out))
                debug_section["decompressions"].append({"method": name, "ok": True, "size": len(out)})
        except Exception as e:
            debug_section["decompressions"].append({"method": name, "ok": False, "error": str(e)})

    # zip container
    if data[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
                for n in zf.namelist():
                    payload = zf.read(n)
                    candidates.append((f"zip:{n}", payload))
            debug_section["decompressions"].append({"method": "zip", "ok": True})
        except Exception as e:
            debug_section["decompressions"].append({"method": "zip", "ok": False, "error": str(e)})

    try:
        import lz4.frame  # type: ignore

        try:
            out = lz4.frame.decompress(data)
            if out and out != data:
                candidates.append(("lz4", out))
                debug_section["decompressions"].append({"method": "lz4", "ok": True, "size": len(out)})
        except Exception as e:
            debug_section["decompressions"].append({"method": "lz4", "ok": False, "error": str(e)})
    except Exception:
        debug_section["decompressions"].append({"method": "lz4", "ok": False, "error": "lz4 lib not installed"})

    return candidates


def strings_like_extract(data: bytes, min_len: int = 6) -> List[Tuple[str, int, str]]:
    out: List[Tuple[str, int, str]] = []

    ascii_re = re.compile(rb"[\x20-\x7e]{%d,}" % min_len)
    for m in ascii_re.finditer(data):
        out.append(("ascii", m.start(), m.group().decode("ascii", errors="ignore")))

    utf16le_re = re.compile(rb"(?:[\x20-\x7e]\x00){%d,}" % min_len)
    for m in utf16le_re.finditer(data):
        out.append(("utf16le-ascii", m.start(), m.group().decode("utf-16le", errors="ignore")))

    utf8_text = data.decode("utf-8", errors="ignore")
    chunk_re = re.compile(r"[\u4e00-\u9fffA-Za-z0-9_\-，。！？,.()（）【】《》:：;；\s]{8,}")
    for i, m in enumerate(chunk_re.finditer(utf8_text), 1):
        out.append(("utf8-chunk", i, re.sub(r"\s+", " ", m.group()).strip()))

    return out


def scan_binary_text(path: str, debug: Dict[str, Any]) -> List[Dict[str, str]]:
    hits: List[Dict[str, str]] = []
    section = {
        "path": path,
        "size": 0,
        "decompressions": [],
        "string_blocks": 0,
        "errors": [],
    }
    debug["bak_text"] = section

    if not os.path.exists(path):
        section["errors"].append("BAK_0_TEXT not found")
        return hits

    try:
        raw = open(path, "rb").read()
    except Exception as e:
        section["errors"].append(f"read failed: {e}")
        return hits

    section["size"] = len(raw)
    payloads = decompress_candidates(raw, section)

    for method, payload in payloads:
        blocks = strings_like_extract(payload)
        section["string_blocks"] += len(blocks)
        for kind, offset, text in blocks:
            if not text:
                continue
            for kw, s, e in find_hits_in_text(text):
                hits.append(
                    {
                        "keyword": kw,
                        "snippet": snippet(text, s, e),
                        "source": f"file={os.path.basename(path)};method={method};block={kind};offset={offset}",
                    }
                )

    section["hits"] = len(hits)
    return hits


def write_html(path: str, hits: List[Dict[str, str]]) -> None:
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    groups: Dict[str, List[Dict[str, str]]] = {}
    for h in hits:
        groups.setdefault(h["keyword"], []).append(h)

    parts = [
        "<!doctype html>",
        "<html><head><meta charset='utf-8'><title>BYD Hits</title>",
        "<style>body{font-family:Arial,\"Microsoft YaHei\",sans-serif;margin:24px;}"
        ".meta{color:#555}.hit{padding:8px 0;border-bottom:1px solid #eee}"
        "code{background:#f6f8fa;padding:2px 4px;border-radius:4px;}</style></head><body>",
        f"<h1>BYD Hits - {html.escape(now)}</h1>",
        f"<p class='meta'>命中总数：<strong>{len(hits)}</strong></p>",
    ]

    for kw in sorted(groups):
        parts.append(f"<h2>关键词：{html.escape(kw)}（{len(groups[kw])}）</h2>")
        for item in groups[kw]:
            parts.append("<div class='hit'>")
            parts.append(f"<div><strong>命中词：</strong>{html.escape(item['keyword'])}</div>")
            parts.append(f"<div><strong>片段：</strong>{html.escape(item['snippet'])}</div>")
            parts.append(f"<div><strong>来源：</strong><code>{html.escape(item['source'])}</code></div>")
            parts.append("</div>")

    parts.append("</body></html>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline BYD hit extractor for WeChat backup")
    parser.add_argument(
        "--backup-root",
        default=r"C:\Users\houyutong\Documents\WeChat Files\wxid_4i112lzyz6wp12\BackupFiles\iphone_49d3f4057bb51cef8bc2591a764e0641",
    )
    parser.add_argument("--output-dir", default=r"C:\WeChatMvp\output")
    args = parser.parse_args()

    db_path = os.path.join(args.backup_root, "Backup.db")
    bak_text = os.path.join(args.backup_root, "BAK_0_TEXT")

    ensure_dir(args.output_dir)

    debug: Dict[str, Any] = {
        "timestamp": dt.datetime.now().isoformat(),
        "keywords": KEYWORDS,
        "backup_root": args.backup_root,
        "steps": [],
    }

    debug["steps"].append("scan Backup.db")
    db_hits = scan_sqlite(db_path, debug)

    all_hits = list(db_hits)
    if len(db_hits) == 0 or debug.get("sqlite", {}).get("decoded_text_cells", 0) == 0:
        debug["steps"].append("scan BAK_0_TEXT")
        all_hits.extend(scan_binary_text(bak_text, debug))
    else:
        debug["steps"].append("skip BAK_0_TEXT (DB already has hits)")

    html_path = os.path.join(args.output_dir, "BYD_hits.html")
    debug_path = os.path.join(args.output_dir, "debug.json")

    write_html(html_path, all_hits)
    with open(debug_path, "w", encoding="utf-8") as f:
        json.dump(debug, f, ensure_ascii=False, indent=2)

    print(f"Done. hits={len(all_hits)}")
    print(f"HTML: {html_path}")
    print(f"DEBUG: {debug_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
