"""報告產出責任：只負責將 Crew 執行結果寫入 Markdown。"""

from pathlib import Path


def write_markdown_report(content: str, output_path: str) -> Path:
    """寫入 UTF-8 Markdown 報告並回傳實際路徑。"""
    path = Path(output_path)
    path.write_text(content, encoding="utf-8")
    return path
