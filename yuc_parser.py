import subprocess
import datetime
import re
import json
from bs4 import BeautifulSoup

def get_current_season_url():
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    now_beijing = now_utc + datetime.timedelta(hours=8)
    year = now_beijing.year
    month = now_beijing.month
    if 1 <= month <= 3:
        season_month = "01"
    elif 4 <= month <= 6:
        season_month = "04"
    elif 7 <= month <= 9:
        season_month = "07"
    else:
        season_month = "10"
    return f"https://yuc.wiki/{year}{season_month}/"

def normalize_title(text):
    if not text: return ""
    text = re.sub(r'[\s\n\r]+', '', text)
    text = re.sub(r'(?:环大陆|港台|大陆|网络|完结|[\d/]+|(?:\d+:\d+)).*$', '', text)
    text = re.sub(r'[^\w\u4e00-\u9fa5]', '', text)
    return text

def parse_yuc_wiki():
    url = get_current_season_url()
    try:
        # 使用 curl 绕过 requests 可能存在的代理/DNS 问题
        # 显式 --noproxy '*'
        result = subprocess.run(
            ["curl", "-k", "-sL", "-m", "30", "--noproxy", "*", "-A", "Mozilla/5.0", url],
            capture_output=True, text=True, check=True, encoding='utf-8', errors='ignore'
        )
        html = result.stdout
        if not html:
            return {"error": "empty html from curl"}
    except Exception as e:
        return {"error": f"fetch failed via curl: {str(e)}"}

    soup = BeautifulSoup(html, 'html.parser')
    
    cover_map = {}
    for div in soup.find_all("div", style=lambda x: x and 'float:left' in x):
        img = div.find("img")
        title_td = div.find("td", class_=re.compile(r"date_title"))
        if img and title_td:
            raw_title = title_td.get_text()
            norm_title = normalize_title(raw_title)
            src = img.get("data-src") or img.get("src")
            if src and not src.endswith("blank.png"):
                if src.startswith("//"): src = "https:" + src
                elif src.startswith("http://"): src = "https://" + src[7:]
                cover_map[norm_title] = src

    for br in soup.find_all("br"):
        br.replace_with(" ")
    clean_text = soup.get_text(separator=' ')
    clean_text = re.sub(r'\s+', ' ', clean_text)

    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    now_beijing = now_utc + datetime.timedelta(hours=8)
    today_idx = now_beijing.weekday()
    today_label = weekdays[today_idx]
    
    start_pattern = f"{today_label} ("
    start_idx = clean_text.find(start_pattern)
    if start_idx == -1:
        return {"error": f"no schedule found for {today_label} at {url}"}

    end_idx = len(clean_text)
    for next_wd in weekdays[today_idx + 1:] + weekdays[:today_idx]:
        next_pattern = f"{next_wd} ("
        idx = clean_text.find(next_pattern, start_idx + len(start_pattern))
        if idx != -1:
            end_idx = idx
            break
    
    section = clean_text[start_idx:end_idx]
    
    for cut_key in ["网络放送 & 其他", "本期 ", "共收录"]:
        ci = section.find(cut_key)
        if ci != -1:
            section = section[:ci]
            break

    entry_pattern = re.compile(
        r'(\d{1,2}:\d{2})~\s*'
        r'(?:\(([^)]*)\)|(P\d+=\d+话|全\d+话\?))\s*'
        r'([^\s(（][^~]{1,100}?)\s*'
        r'(?=\s\d{1,2}:\d{2}~|$)'
    )
    matches = entry_pattern.finditer(section)
    
    items = []
    for match in matches:
        jst_time_str = match.group(1)
        anno = (match.group(2) or match.group(3) or "").strip()
        title_raw = match.group(4).strip()
        
        if not title_raw or title_raw in weekdays:
            continue
        
        display_title = re.sub(r'\s+(?:环大陆|港台|大陆|台湾|网络|完结|首播|独家|[\d/]+|(?:P\d+=\d+话|全\d+话\?)).*$', '', title_raw).strip()
        norm_title = normalize_title(title_raw)
        
        cover = cover_map.get(norm_title, "")
        if not cover:
            for k, v in cover_map.items():
                if norm_title in k or k in norm_title:
                    cover = v
                    break

        is_finished = "完结" in anno
        
        beijing_time_str = "未知"
        if jst_time_str:
            try:
                h, m = map(int, jst_time_str.split(':'))
                total_minutes = h * 60 + m - 60
                if total_minutes < 0: total_minutes += 24 * 60
                beijing_time_str = f"{(total_minutes // 60) % 24:02d}:{total_minutes % 60:02d}"
            except:
                beijing_time_str = jst_time_str

        status_text = "更新中"
        if is_finished:
            status_text = "已完结"
        elif any(x in anno for x in ["长篇", "年番", "泡面"]):
            status_text = "连载中"
        
        items.append({
            "title": display_title,
            "time": beijing_time_str,
            "cover": cover,
            "is_finished": is_finished,
            "status_text": status_text
        })

    return {
        "date": now_beijing.strftime('%Y-%m-%d'),
        "weekday": today_label,
        "items": items
    }

def fetch_and_parse():
    return parse_yuc_wiki()

if __name__ == "__main__":
    print(json.dumps(fetch_and_parse(), ensure_ascii=False))
