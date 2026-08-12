import os
import requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from io import BytesIO

def find_cjk_font():
    # 1. 优先使用 bundle 的字体
    current_dir = os.path.dirname(os.path.abspath(__file__))
    asset_font_dir = os.path.join(current_dir, "assets", "font")
    if os.path.exists(asset_font_dir):
        for f in os.listdir(asset_font_dir):
            if f.lower().endswith(('.ttc', '.ttf', '.otf')):
                return os.path.join(asset_font_dir, f)

    # 2. 探测系统路径
    paths = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/lxgw-wenkai/LXGWWenKai-Regular.ttf",
        "/system/fonts/NotoSansCJK-Regular.ttc",
        "C:/Windows/Fonts/msyh.ttc"
    ]
    for p in paths:
        if os.path.exists(p):
            return p
    return None

def text_wrap(text, font, max_width):
    lines = []
    try:
        if font.getlength(text) <= max_width:
            lines.append(text)
        else:
            words = list(text)
            current_line = ""
            for word in words:
                test_line = current_line + word
                if font.getlength(test_line) <= max_width:
                    current_line = test_line
                else:
                    lines.append(current_line)
                    current_line = word
            lines.append(current_line)
    except Exception:
        # 降级处理，简单按长度截断
        lines.append(text)
    return lines

def render(data, output_path):
    # 配置
    width = 550
    padding = 24
    card_margin = 10
    bg_color = (15, 15, 26)  # #0f0f1a
    card_color = (26, 26, 46)  # #1a1a2e
    card_border_color = (42, 42, 74)  # #2a2a4a
    text_color_main = (224, 224, 255)  # #e0e0ff
    text_color_sub = (136, 136, 170)  # #8888aa
    colors = [(255, 107, 107), (255, 169, 77), (255, 212, 59), (105, 219, 124), (116, 192, 252), (177, 151, 252)]
    
    font_path = find_cjk_font()
    
    try:
        if not font_path:
            raise Exception("No CJK font found")
        title_font = ImageFont.truetype(font_path, 24)
        item_title_font = ImageFont.truetype(font_path, 15)
        info_font = ImageFont.truetype(font_path, 12)
        badge_font = ImageFont.truetype(font_path, 11)
        footer_font = ImageFont.truetype(font_path, 10)
    except:
        # 降级使用 PIL 默认字体
        title_font = item_title_font = info_font = badge_font = footer_font = ImageFont.load_default()

    items = data.get("items", [])
    
    # 预估高度
    header_height = 80
    footer_height = 50
    item_height = 100 # 大致
    total_height = header_height + footer_height + max(100, len(items) * (item_height + card_margin)) + padding * 2
    
    # 创建画布
    img = Image.new('RGB', (width, total_height), bg_color)
    draw = ImageDraw.Draw(img)
    
    # 渐变背景 (模拟 HTML 的 linear-gradient)
    for y in range(total_height):
        r = int(26 - (26 - 15) * (y / total_height))
        g = int(26 - (26 - 15) * (y / total_height))
        b = int(46 - (46 - 26) * (y / total_height))
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    # Header
    draw.text((width//2, padding + 20), "今日番剧", font=title_font, fill=text_color_main, anchor="mm")
    draw.text((width//2, padding + 50), f"{data.get('date')} {data.get('weekday')}", font=info_font, fill=text_color_sub, anchor="mm")
    
    curr_y = header_height + padding
    
    if not items:
        draw.text((width//2, curr_y + 40), "今天没有番剧更新哦~\n主人好好休息喵", font=item_title_font, fill=text_color_sub, anchor="mm", align="center")
        curr_y += 100
    else:
        for i, item in enumerate(items):
            c = colors[i % len(colors)]
            
            # 卡片容器
            card_rect = [padding, curr_y, width - padding, curr_y + 95]
            # 绘制圆角矩形背景和边框
            draw.rounded_rectangle(card_rect, radius=12, fill=card_color, outline=card_border_color, width=1)
            # 左侧彩色条
            draw.rectangle([padding, curr_y + 5, padding + 3, curr_y + 90], fill=c)
            
            # 封面
            cover_url = item.get("cover")
            cover_img = None
            if cover_url:
                try:
                    # 修复：不继承系统代理，防止 SSL 错误
                    resp = requests.get(cover_url, timeout=5, trust_env=False)
                    cover_img = Image.open(BytesIO(resp.content)).convert("RGB")
                    cover_img = cover_img.resize((56, 72), Image.Resampling.LANCZOS)
                    # 圆角处理
                    mask = Image.new('L', (56, 72), 0)
                    mask_draw = ImageDraw.Draw(mask)
                    mask_draw.rounded_rectangle([0, 0, 56, 72], radius=8, fill=255)
                    
                    target_x, target_y = padding + 14, curr_y + 9
                    img.paste(cover_img, (target_x, target_y), mask)
                    # 封面边框
                    draw.rounded_rectangle([target_x, target_y, target_x + 56, target_y + 72], radius=8, outline=(*c, 64), width=2)
                except:
                    cover_img = None
            
            if not cover_img:
                # 占位图
                placeholder_x, placeholder_y = padding + 14, curr_y + 9
                draw.rounded_rectangle([placeholder_x, placeholder_y, placeholder_x + 56, placeholder_y + 72], radius=8, fill=(42, 42, 74))
            
            # 文字内容
            text_x = padding + 14 + 56 + 14
            title = item.get("title", "未知标题")
            
            # 标题折行处理 (最多两行)
            wrapped_title = text_wrap(title, item_title_font, width - text_x - padding - 10)
            title_y = curr_y + 16
            for line_idx, line in enumerate(wrapped_title[:2]):
                draw.text((text_x, title_y + line_idx * 20), line, font=item_title_font, fill=text_color_main)
            
            # 时间和状态
            info_y = curr_y + 64
            time_str = f"Time: {item.get('time')}"
            draw.text((text_x, info_y), time_str, font=info_font, fill=text_color_sub)
            
            status_text = item.get("status_text", "更新中")
            badge_color = (45, 164, 78) if item.get("is_finished") else (31, 111, 235)
            
            # Badge
            status_w = draw.textlength(status_text, font=badge_font)
            badge_h = 16
            badge_x = text_x + 85
            badge_y = info_y - 2
            badge_rect = [badge_x, badge_y, badge_x + status_w + 12, badge_y + badge_h]
            # 先画背景
            draw.rounded_rectangle(badge_rect, radius=4, fill=badge_color)
            # 再画文字（白色）
            draw.text((badge_x + (status_w + 12) / 2, badge_y + badge_h / 2 + 1), status_text, font=badge_font, fill=(255, 255, 255), anchor="mm")
            
            curr_y += 95 + card_margin

    # Footer
    draw.text((width//2, curr_y + 20), "AniDaily @ xiaoyueRX - 番剧监控", font=footer_font, fill=(85, 85, 119), anchor="mm")
    
    # 裁剪高度
    final_img = img.crop((0, 0, width, curr_y + footer_height))
    final_img.save(output_path)
    return output_path

if __name__ == "__main__":
    from yuc_parser import fetch_and_parse
    data = fetch_and_parse()
    render(data, "render_test.png")
