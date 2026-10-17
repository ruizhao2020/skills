#!/usr/bin/env python3
"""中文文章的文风体检（AI 味量化）。

只报数据，不改文章。设计原则：**AI 味是文风层的问题，不能用它去动内容层**
（指标口径、数字、限定条件、术语、图注、风险提示）。所以这个脚本只做统计，
让人按命中项定点修改标点与连接词。

用法：
    python3 style_scan.py articles/volume-price-indicator.md
    python3 style_scan.py articles/*.md --verbose

关注三个指标：
1. **破折号密度**：中文 AI 生成文本的典型特征是"——"过多。经验上每千字 < 1 处较自然。
2. **框架词/金句词**：一句话、换句话说、简单来说、这正是…的原因、才是真正的…、本质上是…
   这类词本身不算错，但集中出现会显得像模板。
3. **句长变异系数**（标准差/均值）：过低说明句子长度整齐划一；但**不要为了指标好看强行改节奏**，
   0.45~0.65 区间通常读起来是自然的。
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
from pathlib import Path

TELL_WORDS = [
    "一句话", "换句话说", "简单来说", "总而言之", "综上所述",
    "值得注意", "值得单独说", "更重要的是", "这正是", "才是", "这才",
    "本质上", "真正的", "恰恰", "你会发现", "在当今",
    "赋能", "抓手", "闭环", "打通", "赛道", "底层逻辑", "痛点",
    "至关重要", "不可或缺", "显著提升", "深刻地",
]

EM_DASH = "——"


def prose_lines(text: str) -> list[tuple[int, str]]:
    """只保留散文：剔除代码块、表格、图片、标题与分隔线。返回 (行号, 内容)。"""
    out: list[tuple[int, str]] = []
    in_code = False
    for index, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not stripped:
            continue
        if stripped.startswith(("|", "![", "#", "---", ">")):
            continue
        out.append((index, stripped))
    return out


def scan(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    numbered = prose_lines(text)
    body = "\n".join(content for _, content in numbered)

    em_dashes = body.count(EM_DASH)
    chars = len(re.sub(r"\s", "", body))
    tells: dict[str, list[int]] = {}
    for word in TELL_WORDS:
        lines = [index for index, content in numbered if word in content]
        if lines:
            tells[word] = lines

    sentences = [s for s in re.split(r"(?<=[。！？])", body) if len(s.strip()) > 1]
    lengths = [len(s) for s in sentences]
    mean = statistics.mean(lengths) if lengths else 0
    cv = statistics.pstdev(lengths) / mean if mean else 0

    paragraphs: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        rows = [row.strip() for row in block.strip().splitlines()]
        if not rows or any(row.startswith("```") for row in rows):
            continue
        if all(row.startswith(("|", "![", "#", "---")) for row in rows if row):
            continue
        cleaned = " ".join(row for row in rows if row and not row.startswith(("|", "![", "#", "---", ">", "```")))
        if cleaned:
            paragraphs.append(cleaned)
    paragraph_lengths = [len(re.sub(r"\s", "", p)) for p in paragraphs]

    return {
        "file": str(path),
        "chars": chars,
        "em_dashes": em_dashes,
        "em_dash_per_1000": round(em_dashes / chars * 1000, 2) if chars else 0,
        "tells": tells,
        "tell_total": sum(len(lines) for lines in tells.values()),
        "sentences": len(sentences),
        "sentence_mean": round(mean, 1),
        "sentence_cv": round(cv, 2),
        "paragraphs": len(paragraphs),
        "paragraph_max": max(paragraph_lengths) if paragraph_lengths else 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="中文文章文风体检（只报数据，不改文章）")
    parser.add_argument("paths", nargs="+", help="Markdown 文件")
    parser.add_argument("--verbose", action="store_true", help="列出每个命中词所在行号")
    args = parser.parse_args()

    for raw in args.paths:
        path = Path(raw)
        if not path.exists():
            print(f"跳过（不存在）: {raw}", file=sys.stderr)
            continue
        result = scan(path)
        print(f"\n== {result['file']} ==")
        print(f"  散文 {result['chars']} 字 / {result['sentences']} 句 / {result['paragraphs']} 段（最长段 {result['paragraph_max']} 字）")
        print(f"  破折号: {result['em_dashes']} 处，每千字 {result['em_dash_per_1000']}（<1 较自然）")
        print(f"  框架/金句词: {result['tell_total']} 次" + (f"，涉及 {len(result['tells'])} 个词" if result["tells"] else ""))
        print(f"  句长: 平均 {result['sentence_mean']} 字，变异系数 {result['sentence_cv']}（0.45~0.65 较自然；不要为指标强行改节奏）")
        if args.verbose and result["tells"]:
            for word, lines in result["tells"].items():
                print(f"    {word}: 行 {lines}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
