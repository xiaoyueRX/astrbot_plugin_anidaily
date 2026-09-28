import os
import re
import ssl
import urllib.request
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageDraw, ImageFont, ImageFilter

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

def fetch_cover_image(url: str, target_size: tuple[int, int]) -> Image.Image | None:
    """下载封面图片并调整大小，绕过防盗链并支持并发/缓存"""
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

class AlphaCanvas:
    """
    支持真正的 RGBA Alpha 混合自绘画布
    在 RGB 底图上绘制带半透明 fill / outline 的圆角矩形，
    彻底解决 PIL convert('RGB') 忽略 alpha 通道导致半透明色块变成刺眼实心纯色的问题。
    """
    def __init__(self, base_img: Image.Image):
        self.img = base_img

    def draw_alpha_rounded_rectangle(self, box, radius, fill=None, outline=None, width=1):
        x0, y0, x1, y1 = [int(v) for v in box]
        bw = x1 - x0
        bh = y1 - y0
        if bw <= 0 or bh <= 0:
            return

        # 创建局部微型 RGBA 图层
        layer = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        ldraw = ImageDraw.Draw(layer)
        ldraw.rounded_rectangle([0, 0, bw, bh], radius=radius, fill=fill, outline=outline, width=width)
        
        # 使用图层自身作为 mask，将半透明层贴到底图上
        self.img.paste(layer, (x0, y0), layer)

def render(data: dict, output_path: str):
    """
    使用纯 Pillow 渲染 4K/Retina 高清（S=4）今日番剧日报卡片。
    视觉风格与 8K 周历看板高度统一：
    - Dark Mode 深色系画布 (#07070f / #0e0e1c / #121224)
    - 毒刺绿 (#39FF14) 与青蓝 (#00E5FF) 发光点缀
    - 磨砂质感圆角卡片与精细边框
    - 海报封面微弱暗角遮罩 + 细微边框
    - 彩色胶囊集数徽章 (待开播/首播/完结/跨季/连载)
    - 严格居中的 GitHub 专属页脚 (PROJECT BY xiaoyueRX · https://github.com/xiaoyueRX)
    """
    S = 4  # 分辨率放大因子 (Base 550px -> 2200px 4K级)
    width = 550 * S
    padding_x = 24 * S
    padding_y = 24 * S
    card_gap = 12 * S

    font_path = find_cjk_font()
    try:
        if not font_path:
            raise Exception("No CJK font found")
        f_brand_tag = ImageFont.truetype(font_path, 13 * S)
        f_brand_sub = ImageFont.truetype(font_path, 9 * S)
        f_header_title = ImageFont.truetype(font_path, 22 * S)
        f_header_sub = ImageFont.truetype(font_path, 11 * S)
        f_header_chip_num = ImageFont.truetype(font_path, 20 * S)
        f_header_chip_label = ImageFont.truetype(font_path, 10 * S)
        f_card_title = ImageFont.truetype(font_path, 15 * S)
        f_card_info = ImageFont.truetype(font_path, 11 * S)
        f_card_badge = ImageFont.truetype(font_path, 10 * S)
        f_footer = ImageFont.truetype(font_path, 11 * S)
        f_footer_bold = ImageFont.truetype(font_path, 11 * S)
        f_empty = ImageFont.truetype(font_path, 14 * S)
    except Exception:
        f_brand_tag = f_brand_sub = f_header_title = f_header_sub = ImageFont.load_default()
        f_header_chip_num = f_header_chip_label = f_card_title = f_card_info = f_card_badge = f_footer = f_footer_bold = f_empty = ImageFont.load_default()

    items = data.get("items", [])
    date_str = data.get("date", "")
    weekday_str = data.get("weekday", "周一")
    season_badge_text = data.get("season_badge_text", "2026 AUTUMN")
    is_transition = data.get("is_transition", False)

    # 预计算卡片尺寸与总高度
    header_box_h = 74 * S
    footer_box_h = 44 * S
    card_h = 92 * S

    content_h = (len(items) * (card_h + card_gap) - card_gap) if items else (120 * S)
    total_height = padding_y + header_box_h + 16 * S + content_h + 16 * S + footer_box_h + padding_y

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
    tag_w = 64 * S
    tag_h = 46 * S
    tag_x0 = header_x0 + 14 * S
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
    draw.text((tag_x0 + tag_w // 2, tag_y0 + 14 * S), tag_year, font=f_brand_tag, fill=(0, 229, 255), anchor="mm")
    draw.text((tag_x0 + tag_w // 2, tag_y0 + 32 * S), tag_sub, font=f_brand_sub, fill=(57, 255, 20), anchor="mm")

    # Header 标题与副标题
    title_x = tag_x0 + tag_w + 16 * S
    title_text = "今日新番 · 每日放送"
    draw.text((title_x, tag_y0 + 12 * S), title_text, font=f_header_title, fill=(255, 255, 255), anchor="lm")

    # 副标题（绿光呼吸点 + 日期 + 换季感知）
    sub_y = tag_y0 + 34 * S
    dot_r = 3 * S
    dot_cx = title_x + 5 * S
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
    draw.text((dot_cx + 10 * S, sub_y), sub_text, font=f_header_sub, fill=(163, 255, 143), anchor="lm")

    # 右侧收录统计 Chip
    chip_w = 78 * S
    chip_h = 44 * S
    chip_x1 = header_x1 - 14 * S
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
    draw.text((chip_x0 + chip_w // 2, chip_y0 + 16 * S), str(len(items)), font=f_header_chip_num, fill=(0, 229, 255), anchor="mm")
    draw.text((chip_x0 + chip_w // 2, chip_y0 + 32 * S), "部 今日更新", font=f_header_chip_label, fill=(142, 146, 168), anchor="mm")

    # 3. 绘制番剧卡片列表
    cur_y = header_y1 + 16 * S
    poster_w = 56 * S
    poster_h = 74 * S

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
        empty_box = [padding_x, cur_y, width - padding_x, cur_y + 110 * S]
        alpha_canvas.draw_alpha_rounded_rectangle(empty_box, radius=12 * S, fill=(18, 18, 36, 220), outline=(255, 255, 255, 24), width=1 * S)
        draw.text((width // 2, cur_y + 42 * S), "今天没有番剧更新哦~", font=f_empty, fill=(240, 243, 248), anchor="mm")
        draw.text((width // 2, cur_y + 68 * S), "主人可以好好休息或补番喵 🐾✨", font=f_card_info, fill=(142, 146, 168), anchor="mm")
        cur_y += 110 * S + 16 * S
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

            draw.rounded_rectangle([padding_x, cur_y + 12 * S, padding_x + 3 * S, cur_y + card_h - 12 * S], radius=2 * S, fill=accent_color)

            # 封面图片处理
            cover_x = padding_x + 14 * S
            cover_y = cur_y + (card_h - poster_h) // 2
            cover_rect = [cover_x, cover_y, cover_x + poster_w, cover_y + poster_h]
            cover_img = cover_images.get(idx)
            
            # 封面圆角遮罩
            mask = Image.new("L", (poster_w, poster_h), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.rounded_rectangle([0, 0, poster_w, poster_h], radius=7 * S, fill=255)

            if cover_img:
                img.paste(cover_img, (cover_x, cover_y), mask)
            else:
                draw.rounded_rectangle(cover_rect, radius=7 * S, fill=(28, 28, 54))
                draw.text((cover_x + poster_w // 2, cover_y + poster_h // 2), "NO IMAGE", font=f_brand_sub, fill=(100, 100, 140), anchor="mm")

            # 封面细微高亮边框
            alpha_canvas.draw_alpha_rounded_rectangle(cover_rect, radius=7 * S, outline=(255, 255, 255, 40), width=1 * S)

            # 信息排版
            text_x = cover_x + poster_w + 14 * S
            max_text_w = (width - padding_x - 14 * S) - text_x

            # 标题折行 (最多 2 行)
            raw_title = item.get("title", "未知番剧")
            title_lines = text_wrap(raw_title, f_card_title, max_text_w)
            
            title_top_y = cur_y + 13 * S
            for line_i, line_txt in enumerate(title_lines[:2]):
                draw.text((text_x, title_top_y + line_i * 18 * S), line_txt, font=f_card_title, fill=(240, 243, 248))

            # 底部徽章与时间
            badge_y = cur_y + card_h - 26 * S
            th = 18 * S
            
            # 1. 播出时间胶囊
            bj_time = item.get("time_info", {}).get("bj_time", "时间未定")
            if bj_time == "24:00":
                time_badge_text = "次日 00:00"
            elif bj_time == "时间未定":
                time_badge_text = "时间未定"
            else:
                time_badge_text = f"放送 {bj_time}"
            tw = draw.textlength(time_badge_text, font=f_card_info)
            tb_rect = [text_x, badge_y, text_x + tw + 12 * S, badge_y + th]
            alpha_canvas.draw_alpha_rounded_rectangle(
                tb_rect,
                radius=5 * S,
                fill=(0, 229, 255, 28),
                outline=(0, 229, 255, 90),
                width=1 * S
            )
            draw.text((text_x + 6 * S, badge_y + th // 2), time_badge_text, font=f_card_info, fill=(0, 229, 255), anchor="lm")

            # 2. 状态/集数胶囊
            status_text = item.get("custom_badge_text", "")
            if not status_text:
                status_text = item.get("ep_status", {}).get("text", "连载中")

            # 过滤掉无法被 CJK 字体绘制的 emoji，替换为极简纯净排印符号
            status_text = re.sub(r"[⏳🏁🌟🔥⏰🕒⚡]+", "", status_text).strip()

            sw = draw.textlength(status_text, font=f_card_badge)
            sb_x0 = text_x + tw + 20 * S
            sb_rect = [sb_x0, badge_y, sb_x0 + sw + 12 * S, badge_y + th]

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
            draw.text((sb_x0 + 6 * S, badge_y + th // 2), status_text, font=f_card_badge, fill=sb_text_color, anchor="lm")

            # 3. 季度来源小标 (如上季跨播)
            season_tag = item.get("season_tag", "")
            if season_tag and season_tag != "本季新番":
                st_x0 = sb_x0 + sw + 18 * S
                st_w = draw.textlength(season_tag, font=f_card_badge)
                if st_x0 + st_w + 12 * S <= width - padding_x - 8 * S:
                    st_rect = [st_x0, badge_y, st_x0 + st_w + 12 * S, badge_y + th]
                    alpha_canvas.draw_alpha_rounded_rectangle(
                        st_rect,
                        radius=5 * S,
                        fill=(57, 255, 20, 30),
                        outline=(57, 255, 20, 90),
                        width=1 * S
                    )
                    draw.text((st_x0 + 6 * S, badge_y + th // 2), season_tag, font=f_card_badge, fill=(57, 255, 20), anchor="lm")

            cur_y += card_h + card_gap

    # 4. 底部 GitHub 专属页脚 (严格居中)
    footer_y0 = cur_y + 4 * S
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
    icon_size = 18 * S
    icon_gap = 10 * S

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

    start_x = int((width - total_footer_w) // 2)
    footer_cy = int(footer_y0 + footer_box_h // 2)

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

def render_weekly(data: dict, output_path: str):
    """
    使用纯 Pillow 渲染 4K 高清（S=4）全景周历看板大图 (Weekly Overview)。
    视觉风格与日报高度统一：
    - Dark Mode 深色系画布 (#07070f / #0e0e1c / #121224)
    - 7大星期泳道纵向分布，每行横向展示番剧卡片
    - 毒刺绿 (#39FF14) 与青蓝 (#00E5FF) 发光点缀
    - 底部居中专属 GitHub 署名
    """
    S = 4
    width = 1200 * S
    padding_x = 28 * S
    padding_y = 28 * S
    lane_gap = 18 * S
    card_gap = 12 * S

    font_path = find_cjk_font()
    try:
        if not font_path:
            raise Exception("No CJK font found")
        f_brand_tag = ImageFont.truetype(font_path, 13 * S)
        f_brand_sub = ImageFont.truetype(font_path, 9 * S)
        f_header_title = ImageFont.truetype(font_path, 24 * S)
        f_header_sub = ImageFont.truetype(font_path, 11 * S)
        f_header_chip_num = ImageFont.truetype(font_path, 20 * S)
        f_header_chip_label = ImageFont.truetype(font_path, 10 * S)
        f_lane_title = ImageFont.truetype(font_path, 16 * S)
        f_lane_sub = ImageFont.truetype(font_path, 10 * S)
        f_card_title = ImageFont.truetype(font_path, 13 * S)
        f_card_info = ImageFont.truetype(font_path, 10 * S)
        f_card_badge = ImageFont.truetype(font_path, 9 * S)
        f_footer = ImageFont.truetype(font_path, 11 * S)
        f_footer_bold = ImageFont.truetype(font_path, 11 * S)
        f_empty = ImageFont.truetype(font_path, 12 * S)
    except Exception:
        f_brand_tag = f_brand_sub = f_header_title = f_header_sub = ImageFont.load_default()
        f_header_chip_num = f_header_chip_label = f_lane_title = f_lane_sub = f_card_title = f_card_info = f_card_badge = f_footer = f_footer_bold = f_empty = ImageFont.load_default()

    schedule = data.get("all_schedule", {})
    if not schedule:
        schedule = data.get("schedule", {})
    
    date_str = data.get("date", "")
    today_weekday = data.get("weekday", "周一")
    season_badge_text = data.get("season_badge_text", "2026 AUTUMN")
    is_transition = data.get("is_transition", False)

    weekdays_order = [
        ("周一", "MONDAY", (74, 144, 226)),
        ("周二", "TUESDAY", (80, 227, 194)),
        ("周三", "WEDNESDAY", (245, 166, 35)),
        ("周四", "THURSDAY", (189, 16, 224)),
        ("周五", "FRIDAY", (255, 45, 85)),
        ("周六", "SATURDAY", (0, 229, 255)),
        ("周日", "SUNDAY", (57, 255, 20))
    ]

    header_box_h = 76 * S
    footer_box_h = 44 * S
    
    # 计算泳道高度与网格布局
    # 每行容纳最多 4 张卡片
    cols_per_row = 4
    lane_card_w = int((width - 2 * padding_x - 130 * S - (cols_per_row - 1) * card_gap) // cols_per_row)
    card_h = 68 * S
    poster_w = 42 * S
    poster_h = 56 * S

    total_anime_count = sum(len(schedule.get(w[0], [])) for w in weekdays_order)

    # 预计算总高度
    lanes_height = 0
    lane_render_data = []
    for day_cn, day_en, accent_color in weekdays_order:
        items = schedule.get(day_cn, [])
        num_items = len(items)
        if num_items == 0:
            num_rows = 1
            l_h = 60 * S
        else:
            num_rows = (num_items + cols_per_row - 1) // cols_per_row
            l_h = max(60 * S, num_rows * (card_h + card_gap) - card_gap + 20 * S)
        lane_render_data.append((day_cn, day_en, accent_color, items, l_h))
        lanes_height += l_h + lane_gap

    total_height = padding_y + header_box_h + 20 * S + lanes_height + 10 * S + footer_box_h + padding_y

    img = Image.new("RGB", (width, total_height), (7, 7, 15))
    draw = ImageDraw.Draw(img)
    alpha_canvas = AlphaCanvas(img)

    for y in range(0, total_height, 2):
        factor = y / total_height
        r = int(12 - 5 * factor)
        g = int(12 - 5 * factor)
        b = int(24 - 9 * factor)
        draw.line([(0, y), (width, y)], fill=(r, g, b), width=2)

    # 绘制 Header
    header_x0 = padding_x
    header_y0 = padding_y
    header_x1 = width - padding_x
    header_y1 = header_y0 + header_box_h

    alpha_canvas.draw_alpha_rounded_rectangle(
        [header_x0, header_y0, header_x1, header_y1],
        radius=14 * S,
        fill=(14, 14, 28, 230),
        outline=(255, 255, 255, 30),
        width=1 * S
    )

    tag_w = 72 * S
    tag_h = 46 * S
    tag_x0 = header_x0 + 16 * S
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
    draw.text((tag_x0 + tag_w // 2, tag_y0 + 14 * S), tag_year, font=f_brand_tag, fill=(0, 229, 255), anchor="mm")
    draw.text((tag_x0 + tag_w // 2, tag_y0 + 32 * S), tag_sub, font=f_brand_sub, fill=(57, 255, 20), anchor="mm")

    title_x = tag_x0 + tag_w + 18 * S
    title_text = "新番放送 · 全景周历看板"
    draw.text((title_x, tag_y0 + 12 * S), title_text, font=f_header_title, fill=(255, 255, 255), anchor="lm")

    sub_y = tag_y0 + 34 * S
    dot_r = 3 * S
    dot_cx = title_x + 5 * S
    dot_cy = sub_y
    alpha_canvas.draw_alpha_rounded_rectangle(
        [dot_cx - dot_r - 3 * S, dot_cy - dot_r - 3 * S, dot_cx + dot_r + 3 * S, dot_cy + dot_r + 3 * S],
        radius=6 * S,
        fill=(57, 255, 20, 60)
    )
    draw.ellipse([dot_cx - dot_r, dot_cy - dot_r, dot_cx + dot_r, dot_cy + dot_r], fill=(57, 255, 20))
    
    sub_text = f"{date_str} {today_weekday} · 全量新番动态周历排期"
    if is_transition:
        sub_text += " (夏秋换季交替期·双季共存)"
    draw.text((dot_cx + 10 * S, sub_y), sub_text, font=f_header_sub, fill=(163, 255, 143), anchor="lm")

    # 右侧收录统计 Chip
    chip_w = 90 * S
    chip_h = 44 * S
    chip_x1 = header_x1 - 16 * S
    chip_x0 = chip_x1 - chip_w
    chip_y0 = header_y0 + (header_box_h - chip_h) // 2
    alpha_canvas.draw_alpha_rounded_rectangle(
        [chip_x0, chip_y0, chip_x1, chip_y0 + chip_h],
        radius=8 * S,
        fill=(0, 229, 255, 20),
        outline=(0, 229, 255, 80),
        width=1 * S
    )
    draw.text((chip_x0 + chip_w // 2, chip_y0 + 16 * S), str(total_anime_count), font=f_header_chip_num, fill=(0, 229, 255), anchor="mm")
    draw.text((chip_x0 + chip_w // 2, chip_y0 + 32 * S), "部 全周收录", font=f_header_chip_label, fill=(142, 146, 168), anchor="mm")

    # 预下载封面 (限量并发避免阻塞)
    all_items = []
    for d in lane_render_data:
        all_items.extend(d[3])
    
    cover_images = {}
    if all_items:
        with ThreadPoolExecutor(max_workers=8) as executor:
            future_to_url = {
                executor.submit(fetch_cover_image, it.get("img_url", ""), (poster_w, poster_h)): it.get("img_url", "")
                for it in all_items if it.get("img_url")
            }
            for future in future_to_url:
                u = future_to_url[future]
                try:
                    cover_images[u] = future.result()
                except Exception:
                    cover_images[u] = None

    # 绘制各个星期泳道
    cur_y = header_y1 + 18 * S
    for day_cn, day_en, accent_color, items, l_h in lane_render_data:
        is_today = (day_cn == today_weekday)
        lane_rect = [padding_x, cur_y, width - padding_x, cur_y + l_h]

        # 泳道底色与边框
        lane_fill = (22, 22, 44, 240) if is_today else (14, 14, 28, 200)
        lane_outline = (57, 255, 20, 150) if is_today else (255, 255, 255, 24)
        lane_width = 2 * S if is_today else 1 * S

        alpha_canvas.draw_alpha_rounded_rectangle(
            lane_rect,
            radius=12 * S,
            fill=lane_fill,
            outline=lane_outline,
            width=lane_width
        )

        # 泳道左侧标识栏
        side_w = 110 * S
        side_rect = [padding_x, cur_y, padding_x + side_w, cur_y + l_h]
        alpha_canvas.draw_alpha_rounded_rectangle(
            side_rect,
            radius=12 * S,
            fill=(*accent_color, 24),
            outline=(*accent_color, 80),
            width=1 * S
        )
        
        # 星期标识
        draw.text((padding_x + side_w // 2, cur_y + 18 * S), day_cn, font=f_lane_title, fill=accent_color, anchor="mm")
        draw.text((padding_x + side_w // 2, cur_y + 32 * S), day_en, font=f_lane_sub, fill=(160, 170, 190), anchor="mm")
        if is_today:
            # TODAY 闪光药丸
            t_chip_w = 64 * S
            t_chip_h = 16 * S
            t_chip_x = padding_x + (side_w - t_chip_w) // 2
            t_chip_y = cur_y + 44 * S
            alpha_canvas.draw_alpha_rounded_rectangle(
                [t_chip_x, t_chip_y, t_chip_x + t_chip_w, t_chip_y + t_chip_h],
                radius=4 * S,
                fill=(57, 255, 20, 60),
                outline=(57, 255, 20, 180),
                width=1 * S
            )
            draw.text((t_chip_x + t_chip_w // 2, t_chip_y + t_chip_h // 2), "TODAY", font=f_brand_sub, fill=(57, 255, 20), anchor="mm")
        else:
            draw.text((padding_x + side_w // 2, cur_y + 48 * S), f"{len(items)} 部", font=f_brand_sub, fill=(120, 130, 150), anchor="mm")

        # 泳道内番剧卡片
        cards_start_x = padding_x + side_w + 14 * S
        cards_start_y = cur_y + 10 * S

        if not items:
            draw.text((cards_start_x + 20 * S, cur_y + l_h // 2), "本日暂无新作档期", font=f_empty, fill=(100, 110, 130), anchor="lm")
        else:
            for idx, item in enumerate(items):
                r_idx = idx // cols_per_row
                c_idx = idx % cols_per_row
                c_x0 = cards_start_x + c_idx * (lane_card_w + card_gap)
                c_y0 = cards_start_y + r_idx * (card_h + card_gap)
                c_x1 = c_x0 + lane_card_w
                c_y1 = c_y0 + card_h

                card_box = [c_x0, c_y0, c_x1, c_y1]
                alpha_canvas.draw_alpha_rounded_rectangle(
                    card_box,
                    radius=8 * S,
                    fill=(18, 18, 36, 220),
                    outline=(255, 255, 255, 30),
                    width=1 * S
                )

                # 封面
                c_img_url = item.get("img_url", "")
                c_img = cover_images.get(c_img_url)
                cov_x0 = c_x0 + 6 * S
                cov_y0 = c_y0 + (card_h - poster_h) // 2
                cov_box = [cov_x0, cov_y0, cov_x0 + poster_w, cov_y0 + poster_h]
                
                c_mask = Image.new("L", (poster_w, poster_h), 0)
                ImageDraw.Draw(c_mask).rounded_rectangle([0, 0, poster_w, poster_h], radius=5 * S, fill=255)

                if c_img:
                    img.paste(c_img, (cov_x0, cov_y0), c_mask)
                else:
                    draw.rounded_rectangle(cov_box, radius=5 * S, fill=(28, 28, 54))
                alpha_canvas.draw_alpha_rounded_rectangle(cov_box, radius=5 * S, outline=(255, 255, 255, 36), width=1 * S)

                # 标题折行 (最多 2 行)
                t_x = cov_x0 + poster_w + 8 * S
                max_tw = c_x1 - 8 * S - t_x
                t_lines = text_wrap(item.get("title", "未命名"), f_card_title, max_tw)
                for l_i, l_txt in enumerate(t_lines[:2]):
                    draw.text((t_x, c_y0 + 8 * S + l_i * 15 * S), l_txt, font=f_card_title, fill=(240, 243, 248))

                # 徽章与时间
                bj_t = item.get("time_info", {}).get("bj_time", "时间未定")
                time_badge = f"{bj_t}" if bj_t != "时间未定" else "未定"
                st_badge = item.get("custom_badge_text", "")
                if not st_badge:
                    st_badge = item.get("ep_status", {}).get("text", "连载中")
                st_badge = re.sub(r"[⏳🏁🌟🔥⏰🕒⚡]+", "", st_badge).strip()

                b_y = c_y1 - 18 * S
                tw = draw.textlength(time_badge, font=f_card_info)
                alpha_canvas.draw_alpha_rounded_rectangle(
                    [t_x, b_y, t_x + tw + 8 * S, b_y + 14 * S],
                    radius=4 * S,
                    fill=(0, 229, 255, 25),
                    outline=(0, 229, 255, 80),
                    width=1 * S
                )
                draw.text((t_x + 4 * S, b_y + 7 * S), time_badge, font=f_card_info, fill=(0, 229, 255), anchor="lm")

                if st_badge:
                    sw = draw.textlength(st_badge, font=f_card_badge)
                    sb_x = t_x + tw + 14 * S
                    if sb_x + sw + 8 * S <= c_x1 - 4 * S:
                        alpha_canvas.draw_alpha_rounded_rectangle(
                            [sb_x, b_y, sb_x + sw + 8 * S, b_y + 14 * S],
                            radius=4 * S,
                            fill=(255, 255, 255, 20),
                            outline=(255, 255, 255, 60),
                            width=1 * S
                        )
                        draw.text((sb_x + 4 * S, b_y + 7 * S), st_badge, font=f_card_badge, fill=(220, 225, 235), anchor="lm")

        cur_y += l_h + lane_gap

    # 绘制居中 GitHub 页脚
    footer_y0 = cur_y
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

    octocat_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "img", "octocat.png")
    icon_size = 18 * S
    icon_gap = 10 * S

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

    start_x = int((width - total_footer_w) // 2)
    footer_cy = int(footer_y0 + footer_box_h // 2)

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

    final_h = footer_y1 + padding_y
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
