"""Isolated no-network/no-write UTI titles plus the unchanged physical column."""

import hashlib
import io
import json
import logging
import math
import re
import resource
import sys


def column(page, limits):
    width, height = float(page.mediabox.width), float(page.mediabox.height)
    if not 0 < width <= 2000 or not 0 < height <= 2000:
        return None
    spans = []

    def visit(text, cm, tm, font, size):
        if not text.strip():
            return
        if len(spans) >= limits["maximum_page_spans"]:
            raise ValueError("physical span bound")
        if any(not math.isfinite(float(v)) for v in [*cm, *tm, size]):
            raise ValueError("finite transform required")
        if any(abs(v) > 0.0001 for v in (cm[1], cm[2], tm[1], tm[2])):
            return
        x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
        y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
        em = abs(size) * max(abs(tm[0]), abs(tm[3])) * max(abs(cm[0]), abs(cm[3]))
        if 0 <= x <= width and 0 <= y <= height:
            spans.append((x, y, em, text.strip()))

    page.extract_text(visitor_text=visit)
    anchors = [s for s in spans if re.fullmatch(
        r"Portfolio as on [0-9]{1,2}(?:st|nd|rd|th)? [A-Za-z]+,? 20[0-9]{2}", s[3], re.I)]
    if len(anchors) != 1:
        return None
    x, y, em, _ = anchors[0]
    if not 0.25 * width < x < 0.55 * width or not 4 <= em <= 16:
        return None
    boundary = x - 2 * em
    selected = [s for s in spans if s[0] < boundary and s[1] <= y + 0.75]
    if not selected or any("\n" in s[3] or "\r" in s[3] for s in selected):
        return None
    lines = []
    for sx, sy, _, text in sorted(selected, key=lambda s: (-s[1], s[0])):
        if not lines or abs(lines[-1][0] - sy) > 0.75:
            lines.append((sy, []))
        lines[-1][1].append((sx, text))
    text = "\n".join(" ".join(" ".join(t.split()) for _, t in sorted(parts)) for _, parts in lines)
    if len(text) > limits["maximum_page_characters"]:
        raise ValueError("physical column text bound")
    # Titles are original spans, not repaired layout text. Holdings and the
    # adjacent category cannot qualify by their physical start/baseline.
    headers = [s for s in spans if s[3].startswith("UTI ")
               and 0 <= s[0] < 0.1 * width and 0.9 * height <= s[1] <= height
               and 8 <= s[2] <= 30
               and len(s[3]) <= limits["physical_title_maximum_characters"]
               and "\n" not in s[3] and "\r" not in s[3]]
    header = None
    if len(headers) == 1:
        hx, hy, hem, original = headers[0]
        header = {"text": original, "text_hash": hashlib.sha256(original.encode()).hexdigest(),
                  "x": round(hx, 6), "y": round(hy, 6), "font_points": round(hem, 6)}
    return {"text": text,
            "geometry": {"page_width": width, "page_height": height,
                         "anchor_x": round(x, 6), "anchor_y": round(y, 6), "anchor_font_points": round(em, 6),
                         "gutter_em": 2, "boundary_x": round(boundary, 6), "baseline_tolerance_points": 0.75,
                         "retained_spans": len(selected)},
            "header": header}


def main():
    from mfdataindia.enrichment.uti_book_pdf_v2 import policy

    limits = policy()
    resource.setrlimit(resource.RLIMIT_CPU, (limits["worker_cpu_seconds"],) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_AS, (limits["linux_worker_address_space_bytes"],) * 2)
    logging.disable(logging.CRITICAL)
    from pypdf import PdfReader

    raw = sys.stdin.buffer.read(limits["maximum_source_bytes"] + 1)
    if not 0 < len(raw) <= limits["maximum_source_bytes"] or not raw.startswith(b"%PDF-"):
        raise ValueError("bounded original required")
    reader = PdfReader(io.BytesIO(raw), strict=True)
    if reader.is_encrypted or not 0 < len(reader.pages) <= limits["maximum_pages"]:
        raise ValueError("physical document bounds")
    result, streams = [], 0
    for page in reader.pages:
        contents = page.get_contents()
        size = len(contents.get_data()) if contents is not None else 0
        streams += size
        if size > limits["maximum_page_stream_bytes"] or streams > limits["maximum_total_stream_bytes"]:
            raise ValueError("decoded stream bounds")
        result.append(column(page, limits))
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(1)
