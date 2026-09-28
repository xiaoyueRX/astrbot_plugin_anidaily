import os
import re
import ssl
import json
import base64
import urllib.request
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta, date
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# 允许超大分辨率渲染（如 8K/16K），避免高分辨率下触发 DecompressionBombError
Image.MAX_IMAGE_PIXELS = None

def parse_scale(scale_input: str | int | float | None, mode: str = "weekly") -> float:
    """
    解析并映射清晰度档位与缩放倍率。
    mode="weekly": Base Width = 2560px
      - "1k": S = 0.5 (Width: 1280px, 轻量省流)
      - "2k": S = 1.0 (Width: 2560px, 标准 2K)
      - "4k": S = 1.5 (Width: 3840px, 4K UHD)
      - "5k": S = 2.0 (Width: 5120px, 5K 经典)
      - "8k": S = 3.0 (Width: 7680px, 8K UHD 旗舰海报级)
      - "16k": S = 6.0 (Width: 15360px, 16K 发烧级)
    mode="daily": Base Width = 550px
      - "1k": S = 2.0 (Width: 1100px)
      - "2k": S = 4.0 (Width: 2200px, 4K视网膜级)
      - "4k": S = 7.0 (Width: 3850px)
      - "8k": S = 14.0 (Width: 7700px, 8K 级)
      - "16k": S = 28.0 (Width: 15400px, 16K 级)
    亦支持纯数值输入（如 3 或 "3.0" 直接作为缩放系数）。
    设置安全上限，防止超大数值导致内存爆仓。
    """
    weekly_presets = {
        "1k": 0.5,
        "2k": 1.0,
        "4k": 1.5,
        "5k": 2.0,
        "8k": 3.0,
        "16k": 6.0
    }
    daily_presets = {
        "1k": 2.0,
        "2k": 4.0,
        "4k": 7.0,
        "8k": 14.0,
        "16k": 28.0
    }

    preset_map = weekly_presets if mode == "weekly" else daily_presets
    default_val = 3.0 if mode == "weekly" else 14.0

    if scale_input is None:
        return default_val

    s_str = str(scale_input).strip().lower()
    if s_str in preset_map:
        return preset_map[s_str]

    # 尝试纯数字解析
    try:
        val = float(s_str)
        if val <= 0:
            return default_val
        # 安全上限约束：weekly 最大 S=8.0 (20480px), daily 最大 S=32.0 (17600px)
        max_limit = 8.0 if mode == "weekly" else 32.0
        if val > max_limit:
            val = max_limit
        return val
    except ValueError:
        return default_val

def find_cjk_font():
    """探测或加载中文字体，优先插件内置字体"""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    asset_font_dir = os.path.join(current_dir, "assets", "font")
    if os.path.exists(asset_font_dir):
        for f in os.listdir(asset_font_dir):
            if f.lower().endswith(('.ttc', '.ttf', '.otf')):
                return os.path.join(asset_font_dir, f)

    paths = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/lxgw-wenkai/LXGWWenKai-Regular.ttf",
        "/system/fonts/NotoSansCJK-Regular.ttc",
        "C:/Windows/Fonts/msyh.ttc"
    ]
    for p in paths:
        if os.path.exists(p):
            return p
    return None

def fetch_cover_image(item: dict | str, target_size: tuple[int, int]) -> Image.Image | None:
    """下载或解码封面图片并调整大小，支持 dict(含 base64/url) 或直接 url 字符串输入"""
    if not item:
        return None

    # 如果是 dict，优先使用内置 base64 解码，零网络延迟
    if isinstance(item, dict):
        img_b64 = item.get("img_base64", "")
        if img_b64 and len(img_b64) > 100:
            try:
                if "," in img_b64:
                    _, b64_data = img_b64.split(",", 1)
                else:
                    b64_data = img_b64
                raw_bytes = base64.b64decode(b64_data)
                raw_img = Image.open(BytesIO(raw_bytes)).convert("RGB")
                return raw_img.resize(target_size, Image.Resampling.LANCZOS)
            except Exception:
                pass
        url = item.get("img_url", "")
    else:
        url = str(item)

    if not url:
        return None

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Referer": "https://www.bilibili.com/"
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            content = resp.read()
            if len(content) > 500:
                raw_img = Image.open(BytesIO(content)).convert("RGB")
                return raw_img.resize(target_size, Image.Resampling.LANCZOS)
    except Exception:
        pass
    return None

def text_wrap_title(text: str, font, max_width: float, max_lines: int = 2) -> list[str]:
    """带有单词完整性保护与自动省略号截断的标题折行算法"""
    if font.getlength(text) <= max_width:
        return [text]

    tokens = []
    buf = ""
    for c in text:
        if c.isascii() and (c.isalnum() or c in "_-'"):
            buf += c
        else:
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append(c)
    if buf:
        tokens.append(buf)

    lines = []
    cur_line = ""
    for idx, tok in enumerate(tokens):
        test_line = cur_line + tok
        if font.getlength(test_line) <= max_width:
            cur_line = test_line
        else:
            if len(lines) < max_lines - 1:
                if cur_line:
                    lines.append(cur_line)
                    cur_line = tok
                else:
                    for ch in tok:
                        if font.getlength(cur_line + ch) <= max_width:
                            cur_line += ch
                        else:
                            lines.append(cur_line)
                            cur_line = ch
                            break
            else:
                remaining_text = cur_line + "".join(tokens[idx:])
                cand = remaining_text
                while cand and font.getlength(cand + "…") > max_width:
                    cand = cand[:-1]
                lines.append(cand + "…")
                return lines

    if cur_line:
        lines.append(cur_line)

    return lines

def text_wrap(text: str, font, max_width: int | float) -> list[str]:
    """文本自动折行"""
    lines = []
    try:
        if font.getlength(text) <= max_width:
            return [text]
        current_line = ""
        for char in text:
            test_line = current_line + char
            if font.getlength(test_line) <= max_width:
                current_line = test_line
            else:
                if current_line:
                    lines.append(current_line)
                current_line = char
        if current_line:
            lines.append(current_line)
    except Exception:
        lines.append(text)
    return lines

def calculate_anime_episode_status(date_raw: str, current_dt: datetime) -> dict:
    """
    计算该番剧在其播出日期所在周与当前参考日期的集数关系：
    - 若当前日期还未到开播日期周（如 10/5 开播，今天是 9/28）：标明 ⏳ 待开播 (10/5 首播)
    - 若当前周就是首播周：标明 🌟 本周首播 (第 1 话)
    - 若已经开播：动态计算第几周并显示 🔥 本周第 X 话！
    """
    if not date_raw:
        return {"status": "unknown", "text": "🕒 档期待定", "badge_class": "badge-ep-pending", "ep_num": 0}

    m = re.search(r'(\d{1,2})/(\d{1,2})', date_raw)
    if not m:
        return {"status": "unknown", "text": "🕒 档期待定", "badge_class": "badge-ep-pending", "ep_num": 0}

    month = int(m.group(1))
    day = int(m.group(2))
    current_date = current_dt.date()
    start_date = date(current_date.year, month, day)

    cur_monday = current_date - timedelta(days=current_date.weekday())
    start_monday = start_date - timedelta(days=start_date.weekday())

    diff_weeks = (cur_monday - start_monday).days // 7
    clean_date = f"{month}/{day}"

    if diff_weeks < 0:
        return {
            "status": "pending",
            "text": f"⏳ 待开播 ({clean_date} 首播)",
            "badge_class": "badge-ep-pending",
            "ep_num": 0,
            "start_date_str": clean_date
        }
    elif diff_weeks == 0:
        return {
            "status": "premiere",
            "text": "🌟 本周首播 (第 1 话)",
            "badge_class": "badge-ep-premiere",
            "ep_num": 1,
            "start_date_str": clean_date
        }
    else:
        ep_num = diff_weeks + 1
        return {
            "status": "airing",
            "text": f"🔥 本周第 {ep_num} 话",
            "badge_class": "badge-ep-airing",
            "ep_num": ep_num,
            "start_date_str": clean_date
        }

class AlphaCanvas:
    """
    支持真正的 RGBA Alpha 混合自绘画布
    在 RGB 底图上绘制带半透明 fill / outline 的圆角矩形，
    彻底解决 PIL convert('RGB') 忽略 alpha 通道导致半透明色块变成刺眼实心纯色的问题。
    """
    def __init__(self, base_img: Image.Image):
        self.img = base_img

    def draw_alpha_rounded_rectangle(self, box, radius: float | int, fill=None, outline=None, width: float | int = 1):
        x0, y0, x1, y1 = [int(round(v)) for v in box]
        bw = x1 - x0
        bh = y1 - y0
        if bw <= 0 or bh <= 0:
            return

        rad = int(round(radius))
        w = max(1, int(round(width)))
        # 创建局部微型 RGBA 图层
        layer = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        ldraw = ImageDraw.Draw(layer)
        ldraw.rounded_rectangle([0, 0, bw, bh], radius=rad, fill=fill, outline=outline, width=w)
        
        # 使用图层自身作为 mask，将半透明层贴到底图上
        self.img.paste(layer, (x0, y0), layer)

def render(data: dict, output_path: str, scale: str | int | float | None = "8k"):
    """
    使用纯 Pillow 渲染今日番剧日报卡片，支持 1k/2k/4k/8k/16k 或自定义倍率。
    视觉风格与 8K 周历看板高度统一：
    - Dark Mode 深色系画布 (#07070f / #0e0e1c / #121224)
    - 毒刺绿 (#39FF14) 与青蓝 (#00E5FF) 发光点缀
    - 磨砂质感圆角卡片与精细边框
    - 海报封面微弱暗角遮罩 + 细微边框
    - 彩色胶囊集数徽章 (待开播/首播/完结/跨季/连载)
    - 严格居中的 GitHub 专属页脚 (PROJECT BY xiaoyueRX · https://github.com/xiaoyueRX)
    """
    S = parse_scale(scale, mode="daily")
    width = int(round(550 * S))
    padding_x = int(round(24 * S))
    padding_y = int(round(24 * S))
    card_gap = int(round(12 * S))

    font_path = find_cjk_font()
    try:
        if not font_path:
            raise Exception("No CJK font found")
        f_brand_tag = ImageFont.truetype(font_path, max(10, int(round(13 * S))))
        f_brand_sub = ImageFont.truetype(font_path, max(8, int(round(9 * S))))
        f_header_title = ImageFont.truetype(font_path, max(14, int(round(22 * S))))
        f_header_sub = ImageFont.truetype(font_path, max(9, int(round(11 * S))))
        f_header_chip_num = ImageFont.truetype(font_path, max(12, int(round(20 * S))))
        f_header_chip_label = ImageFont.truetype(font_path, max(8, int(round(10 * S))))
        f_card_title = ImageFont.truetype(font_path, max(11, int(round(15 * S))))
        f_card_info = ImageFont.truetype(font_path, max(9, int(round(11 * S))))
        f_card_badge = ImageFont.truetype(font_path, max(8, int(round(10 * S))))
        f_footer = ImageFont.truetype(font_path, max(9, int(round(11 * S))))
        f_footer_bold = ImageFont.truetype(font_path, max(9, int(round(11 * S))))
        f_empty = ImageFont.truetype(font_path, max(10, int(round(14 * S))))
    except Exception:
        f_brand_tag = f_brand_sub = f_header_title = f_header_sub = ImageFont.load_default()
        f_header_chip_num = f_header_chip_label = f_card_title = f_card_info = f_card_badge = f_footer = f_footer_bold = f_empty = ImageFont.load_default()

    items = data.get("items", [])
    date_str = data.get("date", "")
    weekday_str = data.get("weekday", "周一")
    season_badge_text = data.get("season_badge_text", "2026 AUTUMN")
    is_transition = data.get("is_transition", False)

    # 预计算卡片尺寸与总高度
    header_box_h = int(round(74 * S))
    footer_box_h = int(round(44 * S))
    card_h = int(round(92 * S))

    content_h = (len(items) * (card_h + card_gap) - card_gap) if items else int(round(120 * S))
    total_height = int(round(padding_y + header_box_h + 16 * S + content_h + 16 * S + footer_box_h + padding_y))

    # 1. 创建画布并填充暗黑底色 (RGB 模式)
    img = Image.new("RGB", (width, total_height), (7, 7, 15))
    draw = ImageDraw.Draw(img)
    alpha_canvas = AlphaCanvas(img)

    # 背景垂直微弱渐变
    for y in range(0, total_height, 2):
        factor = y / total_height
        r = int(12 - 5 * factor)
        g = int(12 - 5 * factor)
        b = int(24 - 9 * factor)
        draw.line([(0, y), (width, y)], fill=(r, g, b), width=2)

    # 2. 绘制顶部 Header 容器 (#0e0e1c 磨砂圆角)
    header_x0 = padding_x
    header_y0 = padding_y
    header_x1 = width - padding_x
    header_y1 = header_y0 + header_box_h
    
    # 容器背景与边框 (通过 alpha_canvas 混合)
    alpha_canvas.draw_alpha_rounded_rectangle(
        [header_x0, header_y0, header_x1, header_y1],
        radius=14 * S,
        fill=(14, 14, 28, 230),
        outline=(255, 255, 255, 30),
        width=1 * S
    )

    # 左侧霓虹标牌 (Brand Glow Tag)
    tag_w = int(round(64 * S))
    tag_h = int(round(46 * S))
    tag_x0 = header_x0 + int(round(14 * S))
    tag_y0 = header_y0 + (header_box_h - tag_h) // 2
    alpha_canvas.draw_alpha_rounded_rectangle(
        [tag_x0, tag_y0, tag_x0 + tag_w, tag_y0 + tag_h],
        radius=8 * S,
        fill=(0, 229, 255, 25),
        outline=(0, 229, 255, 120),
        width=1 * S
    )
    
    tag_parts = season_badge_text.split()
    tag_year = tag_parts[0] if len(tag_parts) > 0 else "2026"
    tag_sub = " ".join(tag_parts[1:]) if len(tag_parts) > 1 else "AUTUMN"
    # 标牌年份与英文
    draw.text((tag_x0 + tag_w // 2, tag_y0 + int(round(14 * S))), tag_year, font=f_brand_tag, fill=(0, 229, 255), anchor="mm")
    draw.text((tag_x0 + tag_w // 2, tag_y0 + int(round(32 * S))), tag_sub, font=f_brand_sub, fill=(57, 255, 20), anchor="mm")

    # Header 标题与副标题
    title_x = tag_x0 + tag_w + int(round(16 * S))
    title_text = "今日新番 · 每日放送"
    draw.text((title_x, tag_y0 + int(round(12 * S))), title_text, font=f_header_title, fill=(255, 255, 255), anchor="lm")

    # 副标题（绿光呼吸点 + 日期 + 换季感知）
    sub_y = tag_y0 + int(round(34 * S))
    dot_r = int(round(3 * S))
    dot_cx = title_x + int(round(5 * S))
    dot_cy = sub_y
    # 光晕
    alpha_canvas.draw_alpha_rounded_rectangle(
        [dot_cx - dot_r - 3 * S, dot_cy - dot_r - 3 * S, dot_cx + dot_r + 3 * S, dot_cy + dot_r + 3 * S],
        radius=6 * S,
        fill=(57, 255, 20, 60)
    )
    draw.ellipse([dot_cx - dot_r, dot_cy - dot_r, dot_cx + dot_r, dot_cy + dot_r], fill=(57, 255, 20))
    
    sub_text = f"{date_str} {weekday_str} · 实时动态推算"
    if is_transition:
        sub_text += " (夏秋换季交替期)"
    draw.text((dot_cx + int(round(10 * S)), sub_y), sub_text, font=f_header_sub, fill=(163, 255, 143), anchor="lm")

    # 右侧收录统计 Chip
    chip_w = int(round(78 * S))
    chip_h = int(round(44 * S))
    chip_x1 = header_x1 - int(round(14 * S))
    chip_x0 = chip_x1 - chip_w
    chip_y0 = header_y0 + (header_box_h - chip_h) // 2
    alpha_canvas.draw_alpha_rounded_rectangle(
        [chip_x0, chip_y0, chip_x1, chip_y0 + chip_h],
        radius=8 * S,
        fill=(0, 229, 255, 20),
        outline=(0, 229, 255, 80),
        width=1 * S
    )
    # 数字与标签
    draw.text((chip_x0 + chip_w // 2, chip_y0 + int(round(16 * S))), str(len(items)), font=f_header_chip_num, fill=(0, 229, 255), anchor="mm")
    draw.text((chip_x0 + chip_w // 2, chip_y0 + int(round(32 * S))), "部 今日更新", font=f_header_chip_label, fill=(142, 146, 168), anchor="mm")

    # 3. 绘制番剧卡片列表
    cur_y = int(round(header_y1 + 16 * S))
    poster_w = int(round(56 * S))
    poster_h = int(round(74 * S))

    # 并发预下载所有封面图片
    cover_images = {}
    if items:
        with ThreadPoolExecutor(max_workers=6) as executor:
            future_to_idx = {
                executor.submit(fetch_cover_image, item.get("img_url", ""), (poster_w, poster_h)): i
                for i, item in enumerate(items)
            }
            for future in future_to_idx:
                idx = future_to_idx[future]
                try:
                    cover_images[idx] = future.result()
                except Exception:
                    cover_images[idx] = None

    if not items:
        empty_box = [padding_x, cur_y, width - padding_x, cur_y + int(round(110 * S))]
        alpha_canvas.draw_alpha_rounded_rectangle(empty_box, radius=12 * S, fill=(18, 18, 36, 220), outline=(255, 255, 255, 24), width=1 * S)
        draw.text((width // 2, cur_y + int(round(42 * S))), "今天没有番剧更新哦~", font=f_empty, fill=(240, 243, 248), anchor="mm")
        draw.text((width // 2, cur_y + int(round(68 * S))), "主人可以好好休息或补番喵 🐾✨", font=f_card_info, fill=(142, 146, 168), anchor="mm")
        cur_y += int(round(110 * S + 16 * S))
    else:
        for idx, item in enumerate(items):
            card_rect = [padding_x, cur_y, width - padding_x, cur_y + card_h]
            # 磨砂深色圆角卡片底色 (微弱光晕)
            alpha_canvas.draw_alpha_rounded_rectangle(
                card_rect,
                radius=12 * S,
                fill=(18, 18, 36, 230),
                outline=(255, 255, 255, 32),
                width=1 * S
            )

            # 左侧发光条 (根据状态选择荧光绿、青蓝、橙红或紫罗兰)
            badge_class = item.get("badge_class", "")
            if "final" in badge_class:
                accent_color = (255, 45, 85)   # 胭脂红/最终回
            elif "airing" in badge_class:
                accent_color = (255, 94, 58)   # 烈焰橙/正在播出
            elif "premiere" in badge_class:
                accent_color = (255, 230, 0)  # 醒目黄/首播
            elif "continuing" in badge_class:
                accent_color = (217, 70, 239)  # 霓虹紫/跨季续播
            else:
                accent_color = (0, 229, 255)   # 青蓝/待开播或常态

            draw.rounded_rectangle([padding_x, cur_y + int(round(12 * S)), padding_x + int(round(3 * S)), cur_y + card_h - int(round(12 * S))], radius=int(round(2 * S)), fill=accent_color)

            # 封面图片处理
            cover_x = padding_x + int(round(14 * S))
            cover_y = cur_y + (card_h - poster_h) // 2
            cover_rect = [cover_x, cover_y, cover_x + poster_w, cover_y + poster_h]
            cover_img = cover_images.get(idx)
            
            # 封面圆角遮罩
            mask = Image.new("L", (poster_w, poster_h), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.rounded_rectangle([0, 0, poster_w, poster_h], radius=int(round(7 * S)), fill=255)

            if cover_img:
                img.paste(cover_img, (cover_x, cover_y), mask)
            else:
                draw.rounded_rectangle(cover_rect, radius=int(round(7 * S)), fill=(28, 28, 54))
                draw.text((cover_x + poster_w // 2, cover_y + poster_h // 2), "NO IMAGE", font=f_brand_sub, fill=(100, 100, 140), anchor="mm")

            # 封面细微高亮边框
            alpha_canvas.draw_alpha_rounded_rectangle(cover_rect, radius=7 * S, outline=(255, 255, 255, 40), width=1 * S)

            # 信息排版
            text_x = cover_x + poster_w + int(round(14 * S))
            max_text_w = (width - padding_x - int(round(14 * S))) - text_x

            # 标题折行 (最多 2 行)
            raw_title = item.get("title", "未知番剧")
            title_lines = text_wrap(raw_title, f_card_title, max_text_w)
            
            title_top_y = cur_y + int(round(13 * S))
            for line_i, line_txt in enumerate(title_lines[:2]):
                draw.text((text_x, title_top_y + line_i * int(round(18 * S))), line_txt, font=f_card_title, fill=(240, 243, 248))

            # 底部徽章与时间
            badge_y = cur_y + card_h - int(round(26 * S))
            th = int(round(18 * S))
            
            # 1. 播出时间胶囊
            bj_time = item.get("time_info", {}).get("bj_time", "时间未定")
            if bj_time == "24:00":
                time_badge_text = "次日 00:00"
            elif bj_time == "时间未定":
                time_badge_text = "时间未定"
            else:
                time_badge_text = f"放送 {bj_time}"
            tw = draw.textlength(time_badge_text, font=f_card_info)
            tb_rect = [text_x, badge_y, text_x + tw + int(round(12 * S)), badge_y + th]
            alpha_canvas.draw_alpha_rounded_rectangle(
                tb_rect,
                radius=5 * S,
                fill=(0, 229, 255, 28),
                outline=(0, 229, 255, 90),
                width=1 * S
            )
            draw.text((text_x + int(round(6 * S)), badge_y + th // 2), time_badge_text, font=f_card_info, fill=(0, 229, 255), anchor="lm")

            # 2. 状态/集数胶囊
            status_text = item.get("custom_badge_text", "")
            if not status_text:
                status_text = item.get("ep_status", {}).get("text", "连载中")

            # 过滤掉无法被 CJK 字体绘制的 emoji，替换为极简纯净排印符号
            status_text = re.sub(r"[⏳🏁🌟🔥⏰🕒⚡]+", "", status_text).strip()

            sw = draw.textlength(status_text, font=f_card_badge)
            sb_x0 = text_x + tw + int(round(20 * S))
            sb_rect = [sb_x0, badge_y, sb_x0 + sw + int(round(12 * S)), badge_y + th]

            # 徽章背景色与边框 (使用 alpha_canvas 绘制)
            if "final" in badge_class:
                sb_fill = (255, 45, 85, 45)
                sb_border = (255, 45, 85, 140)
                sb_text_color = (255, 90, 120)
            elif "airing" in badge_class:
                sb_fill = (255, 94, 58, 45)
                sb_border = (255, 94, 58, 140)
                sb_text_color = (255, 120, 90)
            elif "premiere" in badge_class:
                sb_fill = (255, 230, 0, 40)
                sb_border = (255, 230, 0, 130)
                sb_text_color = (255, 230, 0)
            elif "continuing" in badge_class:
                sb_fill = (217, 70, 239, 45)
                sb_border = (217, 70, 239, 140)
                sb_text_color = (217, 70, 239)
            else:
                sb_fill = (255, 255, 255, 22)
                sb_border = (255, 255, 255, 60)
                sb_text_color = (203, 213, 225)

            alpha_canvas.draw_alpha_rounded_rectangle(
                sb_rect,
                radius=5 * S,
                fill=sb_fill,
                outline=sb_border,
                width=1 * S
            )
            draw.text((sb_x0 + int(round(6 * S)), badge_y + th // 2), status_text, font=f_card_badge, fill=sb_text_color, anchor="lm")

            # 3. 季度来源小标 (如上季跨播)
            season_tag = item.get("season_tag", "")
            if season_tag and season_tag != "本季新番":
                st_x0 = sb_x0 + sw + int(round(18 * S))
                st_w = draw.textlength(season_tag, font=f_card_badge)
                if st_x0 + st_w + int(round(12 * S)) <= width - padding_x - int(round(8 * S)):
                    st_rect = [st_x0, badge_y, st_x0 + st_w + int(round(12 * S)), badge_y + th]
                    alpha_canvas.draw_alpha_rounded_rectangle(
                        st_rect,
                        radius=5 * S,
                        fill=(57, 255, 20, 30),
                        outline=(57, 255, 20, 90),
                        width=1 * S
                    )
                    draw.text((st_x0 + int(round(6 * S)), badge_y + th // 2), season_tag, font=f_card_badge, fill=(57, 255, 20), anchor="lm")

            cur_y += card_h + card_gap

    # 4. 底部 GitHub 专属页脚 (严格居中)
    footer_y0 = cur_y + int(round(4 * S))
    footer_x0 = padding_x
    footer_x1 = width - padding_x
    footer_y1 = footer_y0 + footer_box_h

    alpha_canvas.draw_alpha_rounded_rectangle(
        [footer_x0, footer_y0, footer_x1, footer_y1],
        radius=12 * S,
        fill=(14, 14, 28, 220),
        outline=(255, 255, 255, 24),
        width=1 * S
    )

    # 绘制居中内容：Octocat 图标 + "PROJECT BY xiaoyueRX · https://github.com/xiaoyueRX"
    octocat_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "img", "octocat.png")
    icon_size = int(round(18 * S))
    icon_gap = int(round(10 * S))

    part1 = "PROJECT BY "
    part2 = "xiaoyueRX"
    part3 = " · "
    part4 = "https://github.com/xiaoyueRX"

    w1 = draw.textlength(part1, font=f_footer)
    w2 = draw.textlength(part2, font=f_footer_bold)
    w3 = draw.textlength(part3, font=f_footer)
    w4 = draw.textlength(part4, font=f_footer)
    total_text_w = w1 + w2 + w3 + w4
    total_footer_w = icon_size + icon_gap + total_text_w

    start_x = int(round((width - total_footer_w) / 2))
    footer_cy = int(round(footer_y0 + footer_box_h / 2))

    # 绘制 Octocat 图标
    if os.path.exists(octocat_path):
        try:
            octo_img = Image.open(octocat_path).convert("RGBA")
            octo_img = octo_img.resize((icon_size, icon_size), Image.Resampling.LANCZOS)
            img.paste(octo_img, (start_x, footer_cy - icon_size // 2), octo_img)
        except Exception:
            pass

    # 绘制文字
    tx = start_x + icon_size + icon_gap
    draw.text((tx, footer_cy), part1, font=f_footer, fill=(148, 163, 184), anchor="lm")
    tx += w1
    draw.text((tx, footer_cy), part2, font=f_footer_bold, fill=(248, 250, 252), anchor="lm")
    tx += w2
    draw.text((tx, footer_cy), part3, font=f_footer, fill=(148, 163, 184), anchor="lm")
    tx += w3
    draw.text((tx, footer_cy), part4, font=f_footer, fill=(0, 229, 255), anchor="lm")

    # 5. 保存输出文件
    final_h = footer_y1 + padding_y
    final_img = img.crop((0, 0, width, final_h))
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    final_img.save(output_path, "PNG", optimize=True)
    return output_path

def render_weekly(data: dict, output_path: str, ref_dt: datetime | None = None, scale: str | int | float | None = "8k"):
    """
    使用纯 Pillow (PIL) 零依赖自绘全景周历看板大图 (Weekly Overview)，默认拉满至 8K 旗舰海报级 (7680px)。
    支持 1k/2k/4k/5k/8k/16k 或自定义倍率。
    像素级复刻 Hermes 黄金【8 列横向紧凑自适应流动看板 (Horizontal Swimlane Flow)】：
    - Header 区域：
      * 左侧带青蓝-荧光绿发光微框的 2026 AUTUMN (或对应季度) 标牌
      * 中央大标题“2026 夏秋换季交替 · 全景周历” (根据季度推算)
      * 绿色状态呼吸灯 + 实时动态推算说明 + 3 组青蓝圆点分隔的元数据说明
      * 右侧 3 组数据统计筹码 (XX部 收录总数 / XX部 TV放送 / X部 网络独播)
    - 主体 7 大星期泳道 + 网络特别放送：
      * 左侧固定竖向指示栏：星期英文缩写徽章、大字中文、英文全拼、更新部数胶囊
      * 若为今天 (如周一)：左侧展示鲜艳绿色的 `✓ TODAY 今日更新` 胶囊，整条泳道带有毒刺绿 (#39FF14) 微光发光外边框
      * 右侧卡片网格流：8 列紧凑流式平铺 (cols_per_row = 8)，包含饱满大封面海报立绘、标题保护折行与省略号、播出时间胶囊与彩色状态徽章
      * 自适应高度：部数少的星期高度自动收拢，部数多的星期平铺展开，彻底消灭垂直大黑洞
    - 底部全宽 Footer：深色半透明圆角底栏，居中展示 GitHub Octocat 图标与 PROJECT BY xiaoyueRX · https://github.com/xiaoyueRX
    """
    if ref_dt is None:
        ref_dt = datetime.now(timezone(timedelta(hours=8)))

    S = parse_scale(scale, mode="weekly")
    width = int(round(2560 * S))  # 默认 8k: S=3.0 -> 7680px 宽屏
    padding_x = int(round(48 * S))
    padding_y = int(round(32 * S))
    lane_gap = int(round(14 * S))
    card_gap = int(round(10 * S))

    font_path = find_cjk_font()
    try:
        if not font_path:
            raise Exception("No CJK font found")
        f_brand_year = ImageFont.truetype(font_path, max(12, int(round(21 * S))))
        f_brand_sub = ImageFont.truetype(font_path, max(8, int(round(11 * S))))
        f_header_title = ImageFont.truetype(font_path, max(16, int(round(30 * S))))
        f_header_status = ImageFont.truetype(font_path, max(9, int(round(12 * S))))
        f_header_meta = ImageFont.truetype(font_path, max(9, int(round(12 * S))))
        f_header_chip_num = ImageFont.truetype(font_path, max(14, int(round(26 * S))))
        f_header_chip_label = ImageFont.truetype(font_path, max(9, int(round(12 * S))))

        f_lane_tag = ImageFont.truetype(font_path, max(8, int(round(11 * S))))
        f_lane_today = ImageFont.truetype(font_path, max(8, int(round(10 * S))))
        f_lane_title = ImageFont.truetype(font_path, max(14, int(round(22 * S))))
        f_lane_sub = ImageFont.truetype(font_path, max(8, int(round(10 * S))))
        f_lane_count_num = ImageFont.truetype(font_path, max(11, int(round(16 * S))))
        f_lane_count_unit = ImageFont.truetype(font_path, max(8, int(round(11 * S))))

        f_card_title = ImageFont.truetype(font_path, max(9, int(round(13 * S))))
        f_card_badge = ImageFont.truetype(font_path, max(8, int(round(10 * S))))
        f_card_time = ImageFont.truetype(font_path, max(8, int(round(10 * S))))

        f_footer = ImageFont.truetype(font_path, max(10, int(round(14 * S))))
        f_footer_bold = ImageFont.truetype(font_path, max(10, int(round(14 * S))))
        f_empty = ImageFont.truetype(font_path, max(10, int(round(14 * S))))
    except Exception:
        f_brand_year = f_brand_sub = f_header_title = f_header_status = f_header_meta = ImageFont.load_default()
        f_header_chip_num = f_header_chip_label = f_lane_tag = f_lane_today = f_lane_title = ImageFont.load_default()
        f_lane_sub = f_lane_count_num = f_lane_count_unit = f_card_title = f_card_badge = f_card_time = f_footer = f_footer_bold = f_empty = ImageFont.load_default()

    schedule = data.get("all_schedule", {})
    if not schedule:
        schedule = data.get("schedule", {})
    web_list = data.get("web_broadcast", [])

    season_badge_text = data.get("season_badge_text", "2026 AUTUMN TRANSITION")
    season_title = data.get("season_title", "2026 夏秋换季交替 · 全景周历")
    sub_title_extra = data.get("sub_title_extra", "7月夏番未完结续播 + 10月秋番新作首播")
    is_transition = data.get("is_transition", False)

    weekday_cn_map = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    today_weekday = weekday_cn_map[ref_dt.weekday()]
    today_full_cn = f"星期{['一','二','三','四','五','六','日'][ref_dt.weekday()]}"
    date_str = ref_dt.strftime("%Y-%m-%d")

    weekdays_meta = [
        ("周一", "MONDAY", "MON", (74, 144, 226)),
        ("周二", "TUESDAY", "TUE", (80, 227, 194)),
        ("周三", "WEDNESDAY", "WED", (245, 166, 35)),
        ("周四", "THURSDAY", "THU", (189, 16, 224)),
        ("周五", "FRIDAY", "FRI", (255, 45, 85)),
        ("周六", "SATURDAY", "SAT", (0, 229, 255)),
        ("周日", "SUNDAY", "SUN", (57, 255, 20))
    ]

    # 左侧固定星期指示栏 (宽 155*S)
    side_w = int(round(155 * S))
    side_gap = int(round(20 * S))
    cols_per_row = 8
    lane_padding_x = int(round(18 * S))
    lane_padding_y = int(round(12 * S))

    lane_inner_w = (width - 2 * padding_x) - 2 * lane_padding_x
    cards_area_w = lane_inner_w - side_w - side_gap
    lane_card_w = int((cards_area_w - (cols_per_row - 1) * card_gap) // cols_per_row)

    card_h = int(round(100 * S))
    poster_w = int(round(66 * S))
    poster_h = int(round(86 * S))

    # 统计数量
    tv_count = sum(len(schedule.get(w[0], [])) for w in weekdays_meta)
    web_count = len(web_list)
    total_count = tv_count + web_count

    # 预计算泳道高度
    lane_render_data = []
    total_lanes_h = 0

    for day_cn, day_en, day_tag, accent_color, items, l_h, is_web_lane in [
        (*w, schedule.get(w[0], []), 0, False) for w in weekdays_meta
    ]:
        num_items = len(items)
        if num_items == 0:
            l_h = card_h + 2 * lane_padding_y
        else:
            num_rows = (num_items + cols_per_row - 1) // cols_per_row
            l_h = num_rows * card_h + (num_rows - 1) * card_gap + 2 * lane_padding_y
        lane_render_data.append((day_cn, day_en, day_tag, accent_color, items, l_h, False))
        total_lanes_h += l_h + lane_gap

    # 网络特别放送泳道
    if web_count > 0:
        num_rows = (web_count + cols_per_row - 1) // cols_per_row
        web_l_h = num_rows * card_h + (num_rows - 1) * card_gap + 2 * lane_padding_y
        lane_render_data.append(("网络放送", "STREAMING SPECIALS", "NET", (255, 0, 127), web_list, web_l_h, True))
        total_lanes_h += web_l_h + lane_gap

    header_box_h = int(round(92 * S))
    footer_box_h = int(round(52 * S))
    total_height = int(round(padding_y + header_box_h + 16 * S + total_lanes_h + 8 * S + footer_box_h + padding_y))

    img = Image.new("RGB", (width, total_height), (7, 7, 15))
    draw = ImageDraw.Draw(img)
    alpha_canvas = AlphaCanvas(img)

    for y in range(0, total_height, 2):
        factor = y / total_height
        r = int(12 - 5 * factor)
        g = int(12 - 5 * factor)
        b = int(24 - 9 * factor)
        draw.line([(0, y), (width, y)], fill=(r, g, b), width=2)

    # 1. 绘制 Header 容器
    hx0 = padding_x
    hy0 = padding_y
    hx1 = width - padding_x
    hy1 = hy0 + header_box_h
    alpha_canvas.draw_alpha_rounded_rectangle(
        [hx0, hy0, hx1, hy1],
        radius=18 * S,
        fill=(14, 14, 28, 230),
        outline=(255, 255, 255, 30),
        width=1 * S
    )

    # 左侧标牌
    tag_w = int(round(90 * S))
    tag_h = int(round(60 * S))
    tag_x0 = hx0 + int(round(20 * S))
    tag_y0 = hy0 + (header_box_h - tag_h) // 2
    alpha_canvas.draw_alpha_rounded_rectangle(
        [tag_x0, tag_y0, tag_x0 + tag_w, tag_y0 + tag_h],
        radius=12 * S,
        fill=(0, 229, 255, 25),
        outline=(0, 229, 255, 120),
        width=1 * S
    )

    tag_parts = season_badge_text.split()
    tag_year = tag_parts[0] if len(tag_parts) > 0 else "2026"
    tag_sub = " ".join(tag_parts[1:]) if len(tag_parts) > 1 else "AUTUMN"
    draw.text((tag_x0 + tag_w // 2, tag_y0 + int(round(20 * S))), tag_year, font=f_brand_year, fill=(0, 229, 255), anchor="mm")
    draw.text((tag_x0 + tag_w // 2, tag_y0 + int(round(44 * S))), tag_sub, font=f_brand_sub, fill=(57, 255, 20), anchor="mm")

    # 中央标题与说明
    t_x = tag_x0 + tag_w + int(round(24 * S))
    draw.text((t_x, tag_y0 + int(round(16 * S))), season_title, font=f_header_title, fill=(255, 255, 255), anchor="lm")

    # 状态灯胶囊与元数据
    sub_y = tag_y0 + int(round(46 * S))
    status_str = f"{date_str} {today_full_cn} · 实时动态更新周历"
    st_w = draw.textlength(status_str, font=f_header_status)
    pill_w = int(round(st_w + 30 * S))
    pill_h = int(round(22 * S))
    pill_x0 = t_x
    pill_y0 = sub_y - pill_h // 2

    alpha_canvas.draw_alpha_rounded_rectangle(
        [pill_x0, pill_y0, pill_x0 + pill_w, pill_y0 + pill_h],
        radius=11 * S,
        fill=(57, 255, 20, 30),
        outline=(57, 255, 20, 90),
        width=1 * S
    )
    dot_cx = pill_x0 + int(round(12 * S))
    dot_cy = sub_y
    dot_r = int(round(4 * S))
    alpha_canvas.draw_alpha_rounded_rectangle(
        [dot_cx - dot_r - 2 * S, dot_cy - dot_r - 2 * S, dot_cx + dot_r + 2 * S, dot_cy + dot_r + 2 * S],
        radius=6 * S,
        fill=(57, 255, 20, 60)
    )
    draw.ellipse([dot_cx - dot_r, dot_cy - dot_r, dot_cx + dot_r, dot_cy + dot_r], fill=(57, 255, 20))
    draw.text((dot_cx + int(round(10 * S)), sub_y), status_str, font=f_header_status, fill=(163, 255, 143), anchor="lm")

    # 右侧跟随的 3 组元数据标签
    meta_x = pill_x0 + pill_w + int(round(16 * S))
    meta_items = [
        sub_title_extra,
        "8列紧凑横向流动看板 (Horizontal Swimlane Flow)",
        "北京时间全量校准"
    ]
    for mi in meta_items:
        draw.ellipse([meta_x, sub_y - int(round(2 * S)), meta_x + int(round(4 * S)), sub_y + int(round(2 * S))], fill=(0, 229, 255))
        meta_x += int(round(10 * S))
        draw.text((meta_x, sub_y), mi, font=f_header_meta, fill=(142, 146, 168), anchor="lm")
        meta_x += int(round(draw.textlength(mi, font=f_header_meta) + 16 * S))

    # 右侧 3 组统计筹码
    chip_h = int(round(56 * S))
    chip_y0 = hy0 + (header_box_h - chip_h) // 2
    chip_y1 = chip_y0 + chip_h

    # 筹码 3 (最右): 网络独播
    c3_w = int(round(120 * S))
    c3_x1 = hx1 - int(round(20 * S))
    c3_x0 = c3_x1 - c3_w
    alpha_canvas.draw_alpha_rounded_rectangle(
        [c3_x0, chip_y0, c3_x1, chip_y1],
        radius=12 * S,
        fill=(255, 255, 255, 10),
        outline=(255, 255, 255, 24),
        width=1 * S
    )
    draw.text((c3_x0 + int(round(20 * S)), chip_y0 + chip_h // 2), str(web_count), font=f_header_chip_num, fill=(255, 255, 255), anchor="lm")
    draw.text((c3_x0 + int(round(48 * S)), chip_y0 + chip_h // 2), "部 网络独播", font=f_header_chip_label, fill=(142, 146, 168), anchor="lm")

    # 分割线 2
    div2_x = c3_x0 - int(round(14 * S))
    draw.line([(div2_x, chip_y0 + int(round(10 * S))), (div2_x, chip_y1 - int(round(10 * S)))], fill=(255, 255, 255, 30), width=max(1, int(round(1 * S))))

    # 筹码 2 (中间): 电视台周更
    c2_w = int(round(130 * S))
    c2_x1 = div2_x - int(round(14 * S))
    c2_x0 = c2_x1 - c2_w
    alpha_canvas.draw_alpha_rounded_rectangle(
        [c2_x0, chip_y0, c2_x1, chip_y1],
        radius=12 * S,
        fill=(255, 255, 255, 10),
        outline=(255, 255, 255, 24),
        width=1 * S
    )
    draw.text((c2_x0 + int(round(18 * S)), chip_y0 + chip_h // 2), str(tv_count), font=f_header_chip_num, fill=(255, 255, 255), anchor="lm")
    draw.text((c2_x0 + int(round(56 * S)), chip_y0 + chip_h // 2), "部 TV放送", font=f_header_chip_label, fill=(142, 146, 168), anchor="lm")

    # 分割线 1
    div1_x = c2_x0 - int(round(14 * S))
    draw.line([(div1_x, chip_y0 + int(round(10 * S))), (div1_x, chip_y1 - int(round(10 * S)))], fill=(255, 255, 255, 30), width=max(1, int(round(1 * S))))

    # 筹码 1 (主筹码): 收录总数
    c1_w = int(round(140 * S))
    c1_x1 = div1_x - int(round(14 * S))
    c1_x0 = c1_x1 - c1_w
    alpha_canvas.draw_alpha_rounded_rectangle(
        [c1_x0, chip_y0, c1_x1, chip_y1],
        radius=12 * S,
        fill=(0, 229, 255, 25),
        outline=(0, 229, 255, 90),
        width=1 * S
    )
    draw.text((c1_x0 + int(round(18 * S)), chip_y0 + chip_h // 2), str(total_count), font=f_header_chip_num, fill=(0, 229, 255), anchor="lm")
    draw.text((c1_x0 + int(round(60 * S)), chip_y0 + chip_h // 2), "部 收录总数", font=f_header_chip_label, fill=(163, 255, 240), anchor="lm")

    all_render_items = []
    for d in lane_render_data:
        all_render_items.extend(d[4])

    cover_cache = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_map = {
            executor.submit(fetch_cover_image, it, (poster_w, poster_h)): (it.get("title"), it.get("img_url", ""))
            for it in all_render_items
        }
        for fut in future_map:
            t, u = future_map[fut]
            try:
                res = fut.result()
                if res:
                    cover_cache[t] = res
            except Exception:
                pass

    # 2. 绘制各个星期泳道
    cur_y = int(round(hy1 + 16 * S))
    for day_cn, day_en, day_tag, accent_color, items, l_h, is_web_lane in lane_render_data:
        is_today = (day_cn == today_weekday)
        lane_x0 = padding_x
        lane_y0 = cur_y
        lane_x1 = width - padding_x
        lane_y1 = cur_y + l_h

        if is_today:
            alpha_canvas.draw_alpha_rounded_rectangle(
                [lane_x0 - 2 * S, lane_y0 - 2 * S, lane_x1 + 2 * S, lane_y1 + 2 * S],
                radius=18 * S,
                fill=(57, 255, 20, 20),
                outline=(57, 255, 20, 140),
                width=2 * S
            )
            lane_fill = (20, 36, 28, 235)
            lane_outline = (57, 255, 20, 220)
            lane_border_w = 2 * S
        elif is_web_lane:
            lane_fill = (22, 10, 26, 210)
            lane_outline = (255, 0, 127, 80)
            lane_border_w = 1 * S
        else:
            lane_fill = (14, 14, 26, 210)
            lane_outline = (255, 255, 255, 24)
            lane_border_w = 1 * S

        alpha_canvas.draw_alpha_rounded_rectangle(
            [lane_x0, lane_y0, lane_x1, lane_y1],
            radius=16 * S,
            fill=lane_fill,
            outline=lane_outline,
            width=lane_border_w
        )

        stripe_color = (57, 255, 20) if is_today else accent_color
        alpha_canvas.draw_alpha_rounded_rectangle(
            [lane_x0, lane_y0 + int(round(4 * S)), lane_x0 + int(round(5 * S)), lane_y1 - int(round(4 * S))],
            radius=2 * S,
            fill=(*stripe_color, 255)
        )

        # 左侧固定星期指示栏 - 水平 + 垂直双向居中
        side_x0 = lane_x0 + lane_padding_x
        side_x1 = side_x0 + side_w
        side_cx = side_x0 + side_w // 2

        tag_bg_color = (57, 255, 20) if is_today else accent_color
        tag_text_color = (0, 0, 0)
        lane_tag_w = int(round(36 * S))
        lane_tag_h = int(round(18 * S))

        count_unit = "部企划" if is_web_lane else "部新作"
        c_num_str = str(len(items))
        c_unit_str = f" {count_unit}"
        c_num_w = draw.textlength(c_num_str, font=f_lane_count_num)
        c_unit_w = draw.textlength(c_unit_str, font=f_lane_count_unit)
        c_chip_w = int(round(c_num_w + c_unit_w + 16 * S))
        c_chip_h = int(round(20 * S))

        content_h = int(round(96 * S))
        side_y_start = lane_y0 + (l_h - content_h) // 2
        side_y_start = max(lane_y0 + lane_padding_y, side_y_start)

        # 1. 顶部 badge / today chip 居中
        badge_y = side_y_start
        if is_today:
            today_chip_w = int(round(100 * S))
            today_chip_h = int(round(18 * S))
            gap = int(round(6 * S))
            total_top_w = lane_tag_w + gap + today_chip_w
            badge_x0 = side_cx - total_top_w // 2
            alpha_canvas.draw_alpha_rounded_rectangle(
                [badge_x0, badge_y, badge_x0 + lane_tag_w, badge_y + lane_tag_h],
                radius=5 * S,
                fill=(*tag_bg_color, 240)
            )
            draw.text((badge_x0 + lane_tag_w // 2, badge_y + lane_tag_h // 2), day_tag, font=f_lane_tag, fill=tag_text_color, anchor="mm")

            today_chip_x = badge_x0 + lane_tag_w + gap
            alpha_canvas.draw_alpha_rounded_rectangle(
                [today_chip_x, badge_y, today_chip_x + today_chip_w, badge_y + today_chip_h],
                radius=9 * S,
                fill=(57, 255, 20, 220),
                outline=(57, 255, 20, 255),
                width=1 * S
            )
            draw.text((today_chip_x + today_chip_w // 2, badge_y + today_chip_h // 2), "✓ TODAY 今日更新", font=f_lane_today, fill=(5, 20, 8), anchor="mm")
        else:
            badge_x0 = side_cx - lane_tag_w // 2
            alpha_canvas.draw_alpha_rounded_rectangle(
                [badge_x0, badge_y, badge_x0 + lane_tag_w, badge_y + lane_tag_h],
                radius=5 * S,
                fill=(*tag_bg_color, 240)
            )
            draw.text((side_cx, badge_y + lane_tag_h // 2), day_tag, font=f_lane_tag, fill=tag_text_color, anchor="mm")

        # 2. 星期大字与英文全拼 (以 side_cx 为轴水平居中)
        title_top = badge_y + lane_tag_h + int(round(8 * S))
        day_title_color = (163, 255, 143) if is_today else (255, 255, 255)
        draw.text((side_cx, title_top), day_cn, font=f_lane_title, fill=day_title_color, anchor="mt")

        sub_top = title_top + int(round(28 * S))
        draw.text((side_cx, sub_top), day_en, font=f_lane_sub, fill=(142, 146, 168), anchor="mt")

        # 3. 收录部数胶囊 (以 side_cx 为轴水平居中，内部图文居中)
        count_chip_y = sub_top + int(round(16 * S))
        count_chip_x0 = side_cx - c_chip_w // 2
        alpha_canvas.draw_alpha_rounded_rectangle(
            [count_chip_x0, count_chip_y, count_chip_x0 + c_chip_w, count_chip_y + c_chip_h],
            radius=7 * S,
            fill=(255, 255, 255, 12),
            outline=(*tag_bg_color, 80),
            width=1 * S
        )
        draw.text((count_chip_x0 + int(round(8 * S)), count_chip_y + c_chip_h // 2), c_num_str, font=f_lane_count_num, fill=tag_bg_color, anchor="lm")
        draw.text((count_chip_x0 + int(round(8 * S)) + c_num_w, count_chip_y + c_chip_h // 2), c_unit_str, font=f_lane_count_unit, fill=(142, 146, 168), anchor="lm")

        # 右侧卡片区域
        cards_x0 = side_x0 + side_w + side_gap
        cards_y0 = lane_y0 + lane_padding_y

        if not items:
            draw.text((cards_x0 + 10 * S, lane_y0 + l_h // 2), "本日暂无新作档期", font=f_empty, fill=(100, 110, 130), anchor="lm")
        else:
            for idx, item in enumerate(items):
                r_idx = idx // cols_per_row
                c_idx = idx % cols_per_row
                cx0 = cards_x0 + c_idx * (lane_card_w + card_gap)
                cy0 = cards_y0 + r_idx * (card_h + card_gap)
                cx1 = cx0 + lane_card_w
                cy1 = cy0 + card_h

                card_fill = (22, 34, 30, 240) if is_today else (18, 18, 36, 220)
                card_outline = (57, 255, 20, 90) if is_today else (255, 255, 255, 28)
                alpha_canvas.draw_alpha_rounded_rectangle(
                    [cx0, cy0, cx1, cy1],
                    radius=12 * S,
                    fill=card_fill,
                    outline=card_outline,
                    width=1 * S
                )

                title = item.get("title", "未命名番剧")
                cov_x0 = cx0 + int(round(8 * S))
                cov_y0 = cy0 + (card_h - poster_h) // 2
                cov_box = [cov_x0, cov_y0, cov_x0 + poster_w, cov_y0 + poster_h]

                c_img = cover_cache.get(title)
                c_mask = Image.new("L", (poster_w, poster_h), 0)
                ImageDraw.Draw(c_mask).rounded_rectangle([0, 0, poster_w, poster_h], radius=int(round(8 * S)), fill=255)
                if c_img:
                    img.paste(c_img, (int(round(cov_x0)), int(round(cov_y0))), c_mask)
                else:
                    draw.rounded_rectangle(cov_box, radius=int(round(8 * S)), fill=(28, 28, 54))
                alpha_canvas.draw_alpha_rounded_rectangle(cov_box, radius=8 * S, outline=(255, 255, 255, 36), width=1 * S)

                # 优雅折行算法处理标题 (右侧顶部)
                tx0 = cov_x0 + poster_w + int(round(10 * S))
                max_tw = cx1 - int(round(8 * S)) - tx0
                title_lines = text_wrap_title(title, f_card_title, max_tw, max_lines=2)
                for l_i, l_txt in enumerate(title_lines[:2]):
                    draw.text((tx0, cy0 + int(round(10 * S)) + l_i * int(round(18 * S))), l_txt, font=f_card_title, fill=(240, 243, 248), anchor="lt")

                # 计算动态集数徽章与播出时间徽章 (右侧底部)
                date_raw = item.get("date_raw", "")
                custom_txt = item.get("custom_badge_text")
                if item.get("season_tag") == "上季跨播" or custom_txt:
                    st_text = custom_txt if custom_txt else "⏳ 跨季续播"
                    st_bg = (189, 16, 224, 45)
                    st_border = (217, 70, 239, 140)
                    st_fg = (235, 120, 255)
                else:
                    ep_info = calculate_anime_episode_status(date_raw, ref_dt)
                    st_text = ep_info["text"]
                    if ep_info["status"] == "premiere":
                        st_bg = (255, 230, 0, 40)
                        st_border = (255, 230, 0, 140)
                        st_fg = (255, 235, 50)
                    elif ep_info["status"] == "airing":
                        st_bg = (255, 75, 43, 40)
                        st_border = (255, 75, 43, 140)
                        st_fg = (255, 110, 80)
                    else:  # pending or unknown
                        st_bg = (255, 255, 255, 18)
                        st_border = (255, 255, 255, 40)
                        st_fg = (203, 213, 225)

                time_info = item.get("time_info", {})
                bj_time = time_info.get("bj_time", "")
                is_late = time_info.get("is_late_night", False)
                if is_web_lane:
                    tb_text = "⚡ 网络企划"
                    tb_bg = (57, 255, 20, 35)
                    tb_border = (57, 255, 20, 110)
                    tb_fg = (163, 255, 143)
                elif bj_time and bj_time != "时间未定":
                    if is_late:
                        tb_text = f"🌙 {bj_time}"
                        tb_bg = (255, 0, 127, 40)
                        tb_border = (255, 0, 127, 110)
                        tb_fg = (255, 120, 180)
                    else:
                        tb_text = f"🕒 {bj_time}"
                        tb_bg = (0, 229, 255, 30)
                        tb_border = (0, 229, 255, 90)
                        tb_fg = (0, 229, 255)
                else:
                    tb_text = "🕒 待定"
                    tb_bg = (0, 229, 255, 25)
                    tb_border = (0, 229, 255, 70)
                    tb_fg = (0, 229, 255)

                badge_h = int(round(16 * S))
                badge_y = cy1 - int(round(10 * S)) - badge_h

                # 1. 状态徽章
                st_w = draw.textlength(st_text, font=f_card_badge)
                alpha_canvas.draw_alpha_rounded_rectangle(
                    [tx0, badge_y, tx0 + st_w + int(round(10 * S)), badge_y + badge_h],
                    radius=5 * S,
                    fill=st_bg,
                    outline=st_border,
                    width=1 * S
                )
                draw.text((tx0 + int(round(5 * S)), badge_y + badge_h // 2), st_text, font=f_card_badge, fill=st_fg, anchor="lm")

                # 2. 播出时间徽章
                tb_x = tx0 + st_w + int(round(16 * S))
                tb_w = draw.textlength(tb_text, font=f_card_time)
                if tb_x + tb_w + int(round(10 * S)) <= cx1 - int(round(6 * S)):
                    alpha_canvas.draw_alpha_rounded_rectangle(
                        [tb_x, badge_y, tb_x + tb_w + int(round(10 * S)), badge_y + badge_h],
                        radius=5 * S,
                        fill=tb_bg,
                        outline=tb_border,
                        width=1 * S
                    )
                    draw.text((tb_x + int(round(5 * S)), badge_y + badge_h // 2), tb_text, font=f_card_time, fill=tb_fg, anchor="lm")

        cur_y += l_h + lane_gap

    # 3. 绘制 Footer 容器与居中署名
    foot_y0 = cur_y
    foot_x0 = padding_x
    foot_x1 = width - padding_x
    foot_y1 = foot_y0 + footer_box_h

    alpha_canvas.draw_alpha_rounded_rectangle(
        [foot_x0, foot_y0, foot_x1, foot_y1],
        radius=14 * S,
        fill=(14, 14, 28, 220),
        outline=(255, 255, 255, 24),
        width=1 * S
    )

    octocat_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "img", "octocat.png")
    icon_size = int(round(20 * S))
    icon_gap = int(round(12 * S))

    part1 = "PROJECT BY "
    part2 = "xiaoyueRX"
    part3 = " · "
    part4 = "https://github.com/xiaoyueRX"

    w1 = draw.textlength(part1, font=f_footer)
    w2 = draw.textlength(part2, font=f_footer_bold)
    w3 = draw.textlength(part3, font=f_footer)
    w4 = draw.textlength(part4, font=f_footer)
    total_text_w = w1 + w2 + w3 + w4
    total_footer_w = icon_size + icon_gap + total_text_w

    start_x = int(round((width - total_footer_w) / 2))
    footer_cy = int(round(foot_y0 + footer_box_h / 2))

    if os.path.exists(octocat_path):
        try:
            octo_img = Image.open(octocat_path).convert("RGBA")
            octo_img = octo_img.resize((icon_size, icon_size), Image.Resampling.LANCZOS)
            img.paste(octo_img, (start_x, footer_cy - icon_size // 2), octo_img)
        except Exception:
            pass

    tx = start_x + icon_size + icon_gap
    draw.text((tx, footer_cy), part1, font=f_footer, fill=(148, 163, 184), anchor="lm")
    tx += w1
    draw.text((tx, footer_cy), part2, font=f_footer_bold, fill=(248, 250, 252), anchor="lm")
    tx += w2
    draw.text((tx, footer_cy), part3, font=f_footer, fill=(148, 163, 184), anchor="lm")
    tx += w3
    draw.text((tx, footer_cy), part4, font=f_footer, fill=(0, 229, 255), anchor="lm")

    final_h = foot_y1 + padding_y
    final_img = img.crop((0, 0, width, final_h))
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    final_img.save(output_path, "PNG", optimize=True)
    return output_path

if __name__ == "__main__":
    from yuc_parser import fetch_and_parse
    test_data = fetch_and_parse()
    out = "/tmp/daily_preview.png"
    render(test_data, out)
    print("Render finished:", out, "Size:", os.path.getsize(out))
