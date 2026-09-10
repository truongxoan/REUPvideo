"""Bộ dịch tự động miễn phí (Free Web Translator Fallback).

Tự động dịch lời thoại gốc (tiếng Trung, Anh, Nhật, Hàn, ...) sang tiếng Việt
mà không cần máy chủ trả phí hay API Key.
"""
from __future__ import annotations

import json
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from autodub.languages import TargetLang
from autodub.text.translate_hint import ensure_terminal_punct
from autodub.utils import setup_logging

logger = setup_logging("autodub.translate_free")


def _translate_single(text: str, sl: str = "auto", tl: str = "vi") -> str:
    if not text or not text.strip():
        return ""
    # Google Translate GTX endpoint (free, no auth required)
    url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl={sl}&tl={tl}&dt=t&q={urllib.parse.quote(text)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    r = requests.get(url, headers=headers, timeout=12)
    if r.status_code == 200:
        data = r.json()
        translated = "".join([part[0] for part in data[0] if part and part[0]])
        return translated.strip()
    raise RuntimeError(f"HTTP {r.status_code} from translation endpoint")


def translate_segments_free(
    segments: list[dict],
    target: TargetLang,
    source_lang: str = "zh-CN",
    reporter=None,
    max_workers: int = 8,
) -> list[dict]:
    """Dịch toàn bộ danh sách segments sang tiếng Việt đa luồng cực nhanh."""
    total = len(segments)
    logger.info(f"Đang dịch tự động {total} câu sang tiếng Việt (Free Engine)...")
    if reporter:
        reporter.emit("translate", "start", detail="Free Engine")

    sl = source_lang.split("-")[0].lower() if source_lang else "auto"
    tl = target.code.split("-")[0].lower() if target and target.code else "vi"
    text_field = target.text_field if target else "text_vi"

    results: list[dict] = [dict(s) for s in segments]
    done_count = 0

    def _worker(idx_seg):
        idx, seg = idx_seg
        orig_text = str(seg.get("text", "")).strip()
        if not orig_text:
            return idx, ""
        for _ in range(3):
            try:
                res = _translate_single(orig_text, sl=sl, tl=tl)
                return idx, ensure_terminal_punct(res)
            except Exception:
                pass
        return idx, ensure_terminal_punct(orig_text)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_worker, (i, s)): i for i, s in enumerate(segments)}
        for fut in as_completed(futures):
            idx, trans_text = fut.result()
            results[idx][text_field] = trans_text
            done_count += 1
            if reporter:
                reporter.emit("translate", "progress", current=done_count, total=total)

    if reporter:
        reporter.emit("translate", "done")
    logger.info(f"Đã dịch xong {total} câu tiếng Việt hoàn tất.")
    return results
