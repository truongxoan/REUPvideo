"""Kiểu phụ đề và bộ lọc ffmpeg cho phần ghi chữ / che chữ lên hình.

Đây là NGUỒN DUY NHẤT định nghĩa một "kiểu phụ đề" trong toàn ứng dụng: trang
Cài đặt, hộp thoại chỉnh kiểu chữ, pipeline và trình chỉnh sửa đều đọc cùng
các khóa ở đây, nên chữ xem trước và chữ ghi vào video luôn khớp nhau.

Hai việc, gộp trong đúng một lượt mã hóa lại (:func:`build_filter_complex`):

1. **Che chữ trên hình** — phụ đề cứng của video gốc. Người dùng khoanh vùng
   trong giao diện; tọa độ lưu dạng chuẩn hóa 0..1 nên đổi độ phân giải vẫn
   đúng chỗ.
2. **Ghi phụ đề vào hình** — vẽ phụ đề tiếng Việt đè lên vùng đã che, bằng
   libass, nên chữ cũ bị giấu và chữ mới nằm đúng chỗ của nó.

Chỗ khó nhất là thoát đường dẫn cho bộ lọc ``subtitles`` trên Windows: chuỗi
đi qua bộ đọc filtergraph của ffmpeg RỒI mới tới bộ đọc tùy chọn của bộ lọc,
nên ``C:\\out\\a.srt`` phải thành ``C\\:/out/a.srt``.
"""
from __future__ import annotations

import re

# Độ mờ của vùng che: boxblur luma_radius:luma_power. Bán kính 10 xóa sạch
# chữ mà vẫn nhẹ; vùng che là chữ đục nên không mất chi tiết gì.
MAX_BLUR_RADIUS = 10
BLUR_POWER = 2

#: Kiểu phụ đề đầy đủ — mọi khóa đều có mặt để không nơi nào phải đoán.
DEFAULT_STYLE: dict = {
    "preset": "clean",
    "position": "bottom",       # "bottom" | "middle" | "top"
    "font": "Arial",
    "font_size": 22,
    "margin_v": 40,
    "outline": 2,
    "shadow": 0,
    "bold": True,
    "color": "#FFFFFF",
    "outline_color": "#000000",
    "box": "none",              # "none" (chỉ viền) | "box" (khối nền đặc)
    "box_color": "#000000",
    "box_opacity": 60,          # 0–100, chỉ dùng khi box = "box"
    "line_words": 0,            # 0 = tự xuống dòng theo bề rộng
    "max_lines": 2,
    "all_caps": False,
    "display": "sentence",      # "sentence" | "karaoke"
    "words_per_cue": 3,
    "effect": "pop",            # "pop" | "fade" | "karaoke" | "none"
    "highlight_color": "#FFD54A",
}

#: Bộ kiểu dựng sẵn — (khóa, tên hiển thị, mô tả ngắn, phần ghi đè).
#: Người dùng chọn một bộ rồi tinh chỉnh; mọi khóa không nêu giữ mặc định.
PRESETS: tuple[tuple[str, str, str, dict], ...] = (
    ("clean", "Gọn gàng", "Chữ trắng viền đen, hợp mọi loại video", {
        "font_size": 22, "outline": 2, "shadow": 0, "bold": True,
        "color": "#FFFFFF", "outline_color": "#000000", "box": "none",
        "line_words": 0, "max_lines": 2, "display": "sentence",
    }),
    ("bold_yellow", "Nổi bật", "Chữ vàng viền dày, hợp video giải trí", {
        "font_size": 26, "outline": 3, "shadow": 1, "bold": True,
        "color": "#FFE24A", "outline_color": "#101010", "box": "none",
        "line_words": 0, "max_lines": 2, "display": "sentence",
    }),
    ("box", "Nền mờ", "Khối nền tối sau chữ, dễ đọc trên nền rối", {
        "font_size": 22, "outline": 4, "shadow": 0, "bold": False,
        "color": "#FFFFFF", "box": "box", "box_color": "#000000",
        "box_opacity": 65, "line_words": 0, "max_lines": 2,
        "display": "sentence",
    }),
    ("tiktok", "Video dọc", "Chữ to, ít chữ mỗi hàng, nằm cao hơn mép dưới", {
        "font_size": 30, "outline": 3, "shadow": 0, "bold": True,
        "color": "#FFFFFF", "outline_color": "#000000", "box": "none",
        "line_words": 5, "max_lines": 2, "margin_v": 70,
        "display": "sentence",
    }),
    ("karaoke", "Cụm chữ theo lời", "Từng cụm ngắn sáng lên đúng nhịp đọc", {
        "font_size": 30, "outline": 3, "shadow": 0, "bold": True,
        "color": "#FFFFFF", "outline_color": "#000000", "box": "none",
        "margin_v": 70, "display": "karaoke", "words_per_cue": 3,
        "effect": "karaoke", "highlight_color": "#FFD54A",
    }),
    ("cinema", "Điện ảnh", "Chữ nhỏ, viền mảnh, sát mép dưới", {
        "font_size": 18, "outline": 1, "shadow": 1, "bold": False,
        "color": "#F2F2F2", "outline_color": "#000000", "box": "none",
        "line_words": 0, "max_lines": 2, "margin_v": 24,
        "display": "sentence",
    }),
    ("custom", "Tự chỉnh", "Bạn tự quyết mọi thông số bên dưới", {}),
)

_PRESET_MAP = {key: overrides for key, _label, _hint, overrides in PRESETS}

#: Danh sách (nhãn, khóa) cho ô chọn của giao diện.
PRESET_CHOICES: list[tuple[str, str]] = [
    (label, key) for key, label, _hint, _o in PRESETS
]

# Alignment của libass (theo bàn phím số): 2 = dưới-giữa, 5 = giữa, 8 = trên.
_POSITION_ALIGN = {"bottom": 2, "middle": 5, "top": 8}


def preset_style(key: str) -> dict:
    """Kiểu đầy đủ của một bộ dựng sẵn."""
    return {**DEFAULT_STYLE, "preset": key, **_PRESET_MAP.get(key, {})}


def normalize_style(style: dict | None) -> dict:
    """Điền đủ mọi khóa còn thiếu của một kiểu phụ đề.

    Kiểu lưu trong dự án cũ chỉ có vài khóa; hàm này đắp phần còn lại từ bộ
    dựng sẵn tương ứng (nếu có) rồi tới giá trị mặc định, nên mọi nơi đọc
    kiểu đều thấy một dict hoàn chỉnh và không phải viết ``.get(..., mặc định)``.
    """
    style = dict(style or {})
    base = preset_style(str(style.get("preset", DEFAULT_STYLE["preset"])))
    base.update({k: v for k, v in style.items() if v is not None})
    return base


def blur_filter(width: int, height: int) -> str:
    """Bộ lọc boxblur có bán kính hợp lệ với kích thước vùng che.

    ffmpeg đòi bán kính nhỏ hơn nửa của MẶT PHẲNG đang làm mờ. Ở yuv420p hai
    mặt phẳng màu chỉ bằng một nửa độ phân giải, nên giới hạn thật là
    ``min(w, h) / 4`` — vùng 192x36 chỉ cho phép bán kính dưới 9. Vượt quá
    thì ffmpeg báo "Invalid chroma_param radius value".
    """
    limit = min(width, height) // 4
    radius = max(1, min(MAX_BLUR_RADIUS, limit - 1 if limit > 1 else 1))
    return f"boxblur={radius}:{BLUR_POWER}"


def hex_to_ass_color(hex_color: str, opacity: int = 100) -> str:
    """Đổi ``#RRGGBB`` sang màu ASS ``&HAABBGGRR&`` (thứ tự BGR).

    ``opacity`` tính theo phần trăm: 100 là đục hoàn toàn, 0 là trong suốt.
    Trong ASS thì kênh AA ngược lại — 00 mới là đục.
    """
    h = (hex_color or "").lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        r, g, b = 255, 255, 255
    alpha = max(0, min(255, round((100 - max(0, min(100, opacity))) * 255 / 100)))
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}&"


def safe_font_name(font: str) -> str:
    """Tên phông an toàn: bỏ ký tự phá cấu trúc chuỗi force_style/filtergraph."""
    return re.sub(r"[,'\"\\]", "", str(font or "")) or "Arial"


def escape_subtitles_path(path: str) -> str:
    """Thoát đường dẫn để dùng làm giá trị của bộ lọc ``subtitles=``.

    Trên Windows cần ba phép đổi: dấu ``\\`` thành ``/`` (ffmpeg chấp nhận và
    chúng không còn là ký tự thoát), dấu hai chấm của ổ đĩa được thoát để bộ
    đọc bộ lọc không hiểu nhầm là dấu ngăn tùy chọn, và dấu nháy đơn dùng
    cách nối ``'\\''`` (đóng nháy, nháy đã thoát, mở lại) vì bên trong nháy
    đơn của ffmpeg thì dấu ``\\`` là ký tự thường — viết ``\\'`` sẽ KHÔNG
    thoát được dấu nháy và đường dẫn kiểu ``O'Brien`` làm hỏng cả filtergraph.
    """
    escaped = path.replace("\\", "/")
    escaped = escaped.replace(":", "\\:")
    escaped = escaped.replace("'", r"'\''")
    return escaped


def build_force_style(style: dict | None = None) -> str:
    """Chuỗi ``force_style`` của libass cho phụ đề ghi vào hình.

    Theo đúng vị trí, phông, cỡ chữ, lề dọc, độ dày viền, đổ bóng, in đậm,
    màu chữ / màu viền và khối nền. Màu ASS là ``&HAABBGGRR&`` (thứ tự BGR).
    """
    s = normalize_style(style)
    align = _POSITION_ALIGN.get(str(s["position"]), 2)
    boxed = str(s["box"]) == "box"
    # BorderStyle 3 = khối nền đặc, vẽ bằng chính OutlineColour; lúc đó
    # Outline đóng vai trò khoảng đệm quanh chữ.
    border_style = 3 if boxed else 1
    outline_colour = (hex_to_ass_color(s["box_color"], int(s["box_opacity"]))
                      if boxed else hex_to_ass_color(s["outline_color"]))
    return (
        f"FontName={safe_font_name(s['font'])},"
        f"FontSize={int(s['font_size'])},"
        f"Bold={1 if s['bold'] else 0},"
        f"BorderStyle={border_style},"
        f"Outline={int(s['outline'])},"
        f"Shadow={int(s['shadow'])},"
        f"Alignment={align},"
        f"MarginV={int(s['margin_v'])},"
        f"PrimaryColour={hex_to_ass_color(s['color'])},"
        f"OutlineColour={outline_colour},"
        f"BackColour={hex_to_ass_color('#000000', 40)}"
    )


def _to_pixels(region: dict, video_w: int, video_h: int) -> tuple[int, int, int, int]:
    """Đổi một vùng chuẩn hóa thành số điểm ảnh chẵn, nằm gọn trong khung.

    Chiều rộng và cao chẵn để phép cắt còn hợp lệ với yuv420p.
    """
    x = int(round(float(region["x"]) * video_w))
    y = int(round(float(region["y"]) * video_h))
    w = int(round(float(region["w"]) * video_w))
    h = int(round(float(region["h"]) * video_h))

    x = max(0, min(x, video_w - 2))
    y = max(0, min(y, video_h - 2))
    w = max(2, min(w, video_w - x))
    h = max(2, min(h, video_h - y))
    return x, y, w - (w % 2), h - (h % 2)


def build_filter_complex(
    blur_regions: list[dict] | None,
    video_w: int,
    video_h: int,
    srt_path: str | None = None,
    style: dict | None = None,
    anti_copyright: dict | None = None,
    auto_cover_subtitles: bool = False,
    sub_cover_height_pct: float = 0.14,
) -> str | None:
    """Dựng chuỗi ``-filter_complex``, hoặc None khi không cần lọc gì.

    Hỗ trợ:
    1. Tự động che mờ phụ đề gốc ở chân video (auto_cover_subtitles)
    2. Các vùng che mờ thủ công (blur_regions)
    3. Bộ lọc lách bản quyền (anti_copyright: zoom, hflip, color shift)
    4. Ghi đè phụ đề tiếng Việt (libass / subtitles)
    """
    regions = list(blur_regions or [])
    if auto_cover_subtitles and not regions:
        # Tự động tạo vùng che mờ ở chân video (mặc định 14% phía dưới)
        h_pct = max(0.05, min(0.3, sub_cover_height_pct))
        regions.append({
            "x": 0.0,
            "y": round(1.0 - h_pct, 3),
            "w": 1.0,
            "h": round(h_pct, 3),
        })

    ac = anti_copyright or {}
    has_ac = bool(
        ac.get("enabled", False) and (
            ac.get("hflip", False)
            or float(ac.get("zoom", 1.0)) > 1.001
            or ac.get("color_shift", False)
            or ac.get("vignette", False)
            or ac.get("canvas_blur", False)
        )
    )

    if not regions and not srt_path and not has_ac:
        return None

    parts: list[str] = []
    current = "0:v"

    # --- Bước 1: Áp dụng Lách bản quyền video (nếu có) ---
    if has_ac:
        import random
        # 1.1 Khung nền mờ PIP (Canvas Blur) nếu được bật
        if ac.get("canvas_blur", False):
            nxt_canvas = "v_canvas"
            parts.append(
                f"[{current}]split[v_bg_in][v_fg_in];"
                f"[v_bg_in]scale={video_w}:{video_h},boxblur=24:6[v_bg];"
                f"[v_fg_in]scale=trunc({video_w}*0.88/2)*2:-2[v_fg];"
                f"[v_bg][v_fg]overlay=(W-w)/2:(H-h)/2[{nxt_canvas}]"
            )
            current = nxt_canvas

        ac_filters = []
        zoom = float(ac.get("zoom", 1.04))
        if zoom > 1.001:
            # Jitter nhẹ độ zoom (+-0.5%) để mỗi video có fingerprint khác nhau
            z_jitter = zoom * random.uniform(0.995, 1.008)
            ac_filters.append(
                f"crop=w=iw/{z_jitter:.4f}:h=ih/{z_jitter:.4f}:x=(iw-iw/{z_jitter:.4f})/2:y=(ih-ih/{z_jitter:.4f})/2,scale={video_w}:{video_h}"
            )
        if ac.get("hflip", False):
            ac_filters.append("hflip")
        if ac.get("color_shift", False):
            c_val = round(random.uniform(1.015, 1.035), 3)
            b_val = round(random.uniform(0.008, 0.020), 3)
            s_val = round(random.uniform(1.020, 1.045), 3)
            ac_filters.append(f"eq=contrast={c_val}:brightness={b_val}:saturation={s_val}")
        if ac.get("vignette", False):
            ac_filters.append("vignette=PI/5.5")

        if ac_filters:
            nxt = "v_ac"
            parts.append(f"[{current}]{','.join(ac_filters)}[{nxt}]")
            current = nxt

    # --- Bước 2: Xóa/che mờ phụ đề cũ ---
    for i, region in enumerate(regions):
        x, y, w, h = _to_pixels(region, video_w, video_h)
        base, blurred = f"b{i}", f"bl{i}"
        nxt = f"v{i + 1}"

        # Tách luồng để cùng một khung vừa làm nền dán vừa làm nguồn cắt.
        parts.append(f"[{current}]split[{base}][{base}c]")
        parts.append(
            f"[{base}c]crop={w}:{h}:{x}:{y},{blur_filter(w, h)}[{blurred}]"
        )

        overlay = f"overlay={x}:{y}"
        t_start, t_end = region.get("t_start"), region.get("t_end")
        if t_start is not None and t_end is not None:
            overlay += f":enable='between(t,{float(t_start)},{float(t_end)})'"
        parts.append(f"[{base}][{blurred}]{overlay}[{nxt}]")
        current = nxt

    # --- Bước 3: Ghi phụ đề tiếng Việt ---
    if srt_path:
        subs = f"subtitles='{escape_subtitles_path(srt_path)}'"
        from autodub.utils import bundled_font_files, fonts_dir
        if bundled_font_files():
            subs += f":fontsdir='{escape_subtitles_path(fonts_dir())}'"
        if not srt_path.lower().endswith(".ass"):
            subs += f":force_style='{build_force_style(style)}'"
        parts.append(f"[{current}]{subs}[vout]")
    else:
        parts.append(f"[{current}]null[vout]")

    return ";".join(parts)
