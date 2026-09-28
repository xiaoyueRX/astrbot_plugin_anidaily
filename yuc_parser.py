import sys
import os
import re
import json
import ssl
import urllib.request
from datetime import datetime, timezone, timedelta, date
from bs4 import BeautifulSoup

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
WEEKDAY_JP = {"周一": "月", "周二": "火", "周三": "水", "周四": "木", "周五": "金", "周六": "土", "周日": "日"}

def fetch_html(url: str) -> str:
    """抓取页面 HTML：使用标准 urllib，禁用系统环境代理，忽略证书，支持重试"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),  # 禁用环境变量代理，避免代理设置导致请求失败
        urllib.request.HTTPSHandler(context=ctx)
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    }
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(url, headers=headers)
            with opener.open(req, timeout=20) as resp:
                data = resp.read()
                if len(data) > 1000:
                    return data.decode("utf-8", errors="ignore")
        except Exception as e:
            if attempt == 3:
                print(f"[yuc_parser] Fetch failed {url}: {e}", file=sys.stderr)
    return ""

def convert_jst_to_bj(raw_time: str) -> dict:
    """
    将 JST 播出时间转换为北京时间，并标注 30 小时制深夜档特征。
    如 25:00~ -> JST 25:00 -> 北京时间 24:00 (次日 00:00)
    21:30~ -> JST 21:30 -> 北京时间 20:30
    """
    if not raw_time:
        return {"raw": "", "bj_time": "时间未定", "is_late_night": False, "late_tag": ""}
    
    clean_time = raw_time.replace("~", "").strip()
    m = re.match(r"^(\d{1,2}):(\d{2})$", clean_time)
    if not m:
        return {"raw": raw_time, "bj_time": raw_time, "is_late_night": False, "late_tag": ""}
    
    jst_h = int(m.group(1))
    jst_m = m.group(2)
    
    bj_h = jst_h - 1
    is_late_night = (jst_h >= 24 or bj_h >= 24)
    late_tag = ""
    if jst_h >= 24:
        late_tag = f"深夜 {bj_h:02d}:{jst_m}"
    
    bj_time_str = f"{bj_h:02d}:{jst_m}"
    return {
        "raw": raw_time,
        "jst_time": f"{jst_h:02d}:{jst_m}",
        "bj_time": bj_time_str,
        "is_late_night": is_late_night,
        "late_tag": late_tag
    }

def get_target_seasons(ref_dt: datetime | None = None) -> dict:
    """
    动态计算当前目标季度与是否处于跨季平滑过渡期：
    - 换季交替期（Transition Window）：
      - 9/15 ~ 10/15: 夏季 (07) -> 秋季 (10)
      - 12/15 ~ 1/15: 秋季 (10) -> 冬季 (01)
      - 3/15 ~ 4/15: 冬季 (01) -> 春季 (04)
      - 6/15 ~ 7/15: 春季 (04) -> 夏季 (07)
    - 稳定期：
      - 1~3月: 冬季 (01)
      - 4~6月: 春季 (04)
      - 7~9月: 夏季 (07)
      - 10~12月: 秋季 (10)
    """
    if ref_dt is None:
        ref_dt = datetime.now(timezone(timedelta(hours=8)))
        
    y = ref_dt.year
    m = ref_dt.month
    d = ref_dt.day

    if (m == 9 and d >= 15) or (m == 10 and d <= 15):
        return {
            "is_transition": True,
            "prev_season": f"{y}07",
            "next_season": f"{y}10",
            "primary_season": f"{y}10",
            "season_badge_text": f"{y} AUTUMN",
            "season_title": f"{y} 夏秋换季交替 · 番剧更新",
            "sub_title_extra": "7月夏番未完结续播 + 10月秋番新作首播",
            "seasons": [f"{y}07", f"{y}10"]
        }
    elif (m == 12 and d >= 15):
        return {
            "is_transition": True,
            "prev_season": f"{y}10",
            "next_season": f"{y+1}01",
            "primary_season": f"{y+1}01",
            "season_badge_text": f"{y+1} WINTER",
            "season_title": f"{y}-{y+1} 秋冬换季交替 · 番剧更新",
            "sub_title_extra": "10月秋番未完结续播 + 1月冬番新作首播",
            "seasons": [f"{y}10", f"{y+1}01"]
        }
    elif (m == 1 and d <= 15):
        return {
            "is_transition": True,
            "prev_season": f"{y-1}10",
            "next_season": f"{y}01",
            "primary_season": f"{y}01",
            "season_badge_text": f"{y} WINTER",
            "season_title": f"{y-1}-{y} 秋冬换季交替 · 番剧更新",
            "sub_title_extra": "10月秋番未完结续播 + 1月冬番新作首播",
            "seasons": [f"{y-1}10", f"{y}01"]
        }
    elif (m == 3 and d >= 15) or (m == 4 and d <= 15):
        return {
            "is_transition": True,
            "prev_season": f"{y}01",
            "next_season": f"{y}04",
            "primary_season": f"{y}04",
            "season_badge_text": f"{y} SPRING",
            "season_title": f"{y} 冬春换季交替 · 番剧更新",
            "sub_title_extra": "1月冬番未完结续播 + 4月春番新作首播",
            "seasons": [f"{y}01", f"{y}04"]
        }
    elif (m == 6 and d >= 15) or (m == 7 and d <= 15):
        return {
            "is_transition": True,
            "prev_season": f"{y}04",
            "next_season": f"{y}07",
            "primary_season": f"{y}07",
            "season_badge_text": f"{y} SUMMER",
            "season_title": f"{y} 春夏换季交替 · 番剧更新",
            "sub_title_extra": "4月春番未完结续播 + 7月夏番新作首播",
            "seasons": [f"{y}04", f"{y}07"]
        }
    else:
        if m in (1, 2, 3):
            code = f"{y}01"
            en_name = "WINTER"
            cn_name = "冬季"
        elif m in (4, 5, 6):
            code = f"{y}04"
            en_name = "SPRING"
            cn_name = "春季"
        elif m in (7, 8, 9):
            code = f"{y}07"
            en_name = "SUMMER"
            cn_name = "夏季"
        else:
            code = f"{y}10"
            en_name = "AUTUMN"
            cn_name = "秋季"
        return {
            "is_transition": False,
            "prev_season": None,
            "next_season": None,
            "primary_season": code,
            "season_badge_text": f"{y} {en_name}",
            "season_title": f"{y} {cn_name}新番 · 每日更新",
            "sub_title_extra": f"{y}年{cn_name}新番排期",
            "seasons": [code]
        }

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

def parse_single_season(html: str, season_code: str) -> dict:
    """解析单个季度的 yuc.wiki HTML 页面"""
    soup = BeautifulSoup(html, "html.parser")
    title_elem = soup.title.string if soup.title else f"{season_code}新番"
    season_name = title_elem.split("|")[0].strip() if title_elem else f"{season_code}新番"

    schedule = {day: [] for day in WEEKDAYS}
    web_broadcast = []

    for td in soup.find_all("td", class_="date2"):
        txt = td.get_text(strip=True)
        current_section = None
        for day in WEEKDAYS:
            if day in txt:
                current_section = day
                break
        if not current_section and ("网络" in txt or "其他" in txt):
            current_section = "网络放送 & 其他"

        if not current_section:
            continue

        parent_div = td
        while parent_div.parent and parent_div.parent.name != "body":
            parent_div = parent_div.parent

        nxt = parent_div.find_next_sibling("div")
        while nxt and not nxt.find_all(class_=re.compile(r"^div_date.*$")):
            if nxt.find("td", class_="date2"):
                nxt = None
                break
            nxt = nxt.find_next_sibling("div")

        if not nxt:
            continue

        cards = nxt.find_all(class_=re.compile(r"^div_date.*$"))
        for c in cards:
            parent_card = c.parent
            img = c.find("img")
            img_url = ""
            if img:
                img_url = img.get("data-src") or img.get("src") or ""
                if img_url.startswith("//"):
                    img_url = "https:" + img_url

            time_p = c.find("p", class_="imgtext4")
            time_str = time_p.get_text(strip=True) if time_p else ""

            imgep_p = c.find("p", class_="imgep")
            imgep2_p = c.find("p", class_="imgep2")
            
            ep_raw = imgep_p.get_text(strip=True) if imgep_p else ""
            date_raw = imgep2_p.get_text(strip=True) if imgep2_p else ""

            title_td = parent_card.find("td", class_=re.compile(r"^date_title")) if parent_card else None
            title_str = ""
            if title_td:
                t_clone = title_td.__copy__()
                for br in t_clone.find_all("br"):
                    br.replace_with(" ")
                title_str = re.sub(r"\s+", " ", t_clone.get_text(strip=True))

            if not date_raw and parent_card:
                pmfs = parent_card.find("p", class_=re.compile(r"^pmfs"))
                if pmfs:
                    date_raw = pmfs.get_text(strip=True)

            if not title_str:
                continue

            time_info = convert_jst_to_bj(time_str)

            anime_entry = {
                "title": title_str,
                "season_code": season_code,
                "date_raw": date_raw,
                "ep_raw": ep_raw,
                "time_info": time_info,
                "img_url": img_url
            }

            if current_section in schedule:
                schedule[current_section].append(anime_entry)
            else:
                web_broadcast.append(anime_entry)

    return {
        "season_title": season_name,
        "season_code": season_code,
        "schedule": schedule,
        "web_broadcast": web_broadcast
    }

def merge_transition_seasons(prev_data: dict, next_data: dict, ref_dt: datetime) -> dict:
    """
    在换季交替期，将上个季度中【尚未完结、在当前周仍在播出】的番剧合并到新季度日程中。
    """
    cur_date = ref_dt.date()
    cur_monday = cur_date - timedelta(days=cur_date.weekday())
    
    prev_code = prev_data.get("season_code", "202607")
    prev_year = int(prev_code[:4])
    prev_month = int(prev_code[4:])
    q_start = date(prev_year, prev_month, 1)
    q_monday = q_start + timedelta(days=(7 - q_start.weekday()) % 7)
    elapsed_weeks = (cur_monday - q_monday).days // 7
    cur_week_num = elapsed_weeks + 1

    merged_schedule = {day: [] for day in WEEKDAYS}
    merged_web = []

    # 1. 筛选上个季度的续播/最终回番剧
    for day in WEEKDAYS:
        for anime in prev_data.get("schedule", {}).get(day, []):
            ep_raw = anime.get("ep_raw", "")
            m = re.search(r"(\d+)", ep_raw)
            total_ep = int(m.group(1)) if m else 12
            if "长篇" in ep_raw or "年番" in ep_raw:
                total_ep = 52
            elif "半年" in ep_raw or "24" in ep_raw:
                total_ep = 24
            elif "26" in ep_raw:
                total_ep = 26
            elif "13" in ep_raw:
                total_ep = 13

            is_continuing = False
            custom_badge = ""
            badge_class = "badge-ep-continuing"
            
            if total_ep >= 24 or "长篇" in ep_raw or "年番" in ep_raw:
                is_continuing = True
                custom_badge = f"⏳ 跨季续播·第{cur_week_num}话"
            elif total_ep >= cur_week_num:
                is_continuing = True
                if cur_week_num == total_ep:
                    custom_badge = f"🏁 最终回·第{cur_week_num}话"
                    badge_class = "badge-ep-final"
                else:
                    custom_badge = f"⏳ 跨季续播·第{cur_week_num}话"

            if is_continuing:
                anime_copy = dict(anime)
                anime_copy["season_tag"] = "上季跨播"
                anime_copy["status"] = "跨季续播"
                anime_copy["custom_badge_text"] = custom_badge
                anime_copy["badge_class"] = badge_class
                anime_copy["ep_status"] = {
                    "status": "continuing",
                    "text": custom_badge,
                    "badge_class": badge_class,
                    "ep_num": cur_week_num
                }
                merged_schedule[day].append(anime_copy)

    # 2. 加入新季度的新番
    for day in WEEKDAYS:
        for anime in next_data.get("schedule", {}).get(day, []):
            anime_copy = dict(anime)
            anime_copy["season_tag"] = "新季新番"
            anime_copy["status"] = "新番首播"
            # 计算动态状态
            ep_status = calculate_anime_episode_status(anime_copy.get("date_raw", ""), ref_dt)
            anime_copy["ep_status"] = ep_status
            anime_copy["custom_badge_text"] = ep_status["text"]
            anime_copy["badge_class"] = ep_status["badge_class"]
            merged_schedule[day].append(anime_copy)

    # 3. 网络放送
    for anime in next_data.get("web_broadcast", []):
        anime_copy = dict(anime)
        anime_copy["season_tag"] = "新季新番"
        ep_status = calculate_anime_episode_status(anime_copy.get("date_raw", ""), ref_dt)
        anime_copy["ep_status"] = ep_status
        anime_copy["custom_badge_text"] = ep_status["text"]
        anime_copy["badge_class"] = ep_status["badge_class"]
        merged_web.append(anime_copy)

    return {
        "schedule": merged_schedule,
        "web_broadcast": merged_web
    }

def fetch_and_parse(ref_dt: datetime | None = None) -> dict:
    """主入口：全自动感知换季、抓取排期并筛选今日番剧"""
    if ref_dt is None:
        ref_dt = datetime.now(timezone(timedelta(hours=8)))
    
    season_info = get_target_seasons(ref_dt)
    
    if season_info["is_transition"]:
        prev_url = f"https://yuc.wiki/{season_info['prev_season']}/"
        next_url = f"https://yuc.wiki/{season_info['next_season']}/"
        prev_html = fetch_html(prev_url)
        next_html = fetch_html(next_url)
        
        if not next_html and not prev_html:
            return {"error": "无法抓取 yuc.wiki 数据"}
            
        prev_data = parse_single_season(prev_html, season_info["prev_season"]) if prev_html else {"schedule": {}, "web_broadcast": []}
        next_data = parse_single_season(next_html, season_info["next_season"]) if next_html else {"schedule": {}, "web_broadcast": []}
        
        merged = merge_transition_seasons(prev_data, next_data, ref_dt)
        schedule = merged["schedule"]
        web_broadcast = merged["web_broadcast"]
    else:
        url = f"https://yuc.wiki/{season_info['primary_season']}/"
        html = fetch_html(url)
        if not html:
            return {"error": f"无法抓取 yuc.wiki 季度页面: {url}"}
        parsed = parse_single_season(html, season_info["primary_season"])
        schedule = parsed["schedule"]
        web_broadcast = parsed["web_broadcast"]
        for day in WEEKDAYS:
            for anime in schedule.get(day, []):
                anime["season_tag"] = "本季新番"
                ep_status = calculate_anime_episode_status(anime.get("date_raw", ""), ref_dt)
                anime["ep_status"] = ep_status
                anime["custom_badge_text"] = ep_status["text"]
                anime["badge_class"] = ep_status["badge_class"]

    today_idx = ref_dt.weekday()
    today_cn = WEEKDAYS[today_idx]
    today_items = schedule.get(today_cn, [])

    return {
        "date": ref_dt.strftime("%Y-%m-%d"),
        "weekday": today_cn,
        "is_transition": season_info["is_transition"],
        "season_badge_text": season_info["season_badge_text"],
        "season_title": season_info["season_title"],
        "items": today_items,
        "all_schedule": schedule,
        "web_broadcast": web_broadcast
    }

if __name__ == "__main__":
    res = fetch_and_parse()
    print(f"Date: {res.get('date')} {res.get('weekday')}, Items count: {len(res.get('items', []))}")
    for it in res.get("items", []):
        print(f" - {it['title']} | {it.get('time_info', {}).get('bj_time')} | {it.get('custom_badge_text')} | {it.get('date_raw')}")
