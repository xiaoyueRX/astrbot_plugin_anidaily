# AstrBot 番剧每日推送插件 (astrbot_plugin_anidaily)

基于 [yuc.wiki](https://yuc.wiki/) 数据的番剧每日更新提醒插件，提供精美的深色卡片渲染。

## 功能

- **每日推送**：定时推送今日更新的番剧列表（默认 08:20）。
- **美观卡片**：使用 PIL 渲染的深色风格卡片，包含封面、放送时间和状态。
- **订阅管理**：支持按会话订阅或取消推送。
- **手动触发**：随时查看今日番剧。

## 安装

1. 在 AstrBot 的 `plugins` 目录下：
   ```bash
   git clone https://github.com/xiaoyueRX/astrbot_plugin_anidaily
   ```
2. 安装依赖：
   ```bash
   pip install -r requirements.txt
   ```
3. 重启 AstrBot。

## 命令说明

| 命令 | 说明 |
| --- | --- |
| `/anidaily` 或 `/anidaily now` | 立即获取今日番剧卡片 |
| `/anidaily sub` | 订阅每日推送 |
| `/anidaily unsub` | 取消订阅 |
| `/anidaily list` | 查看订阅列表 |

## 配置项

可在 WebUI 插件配置中修改：
- `cron_expr`: 推送时间，默认 `20 8 * * *`。
- `enable_cover`: 是否显示封面，默认 `true`。
- `max_items`: 最大显示条目数，默认 `20`。

## 免责声明

数据来源 [yuc.wiki](https://yuc.wiki/)。本插件仅供学习交流使用。

## 字体与依赖说明

本插件已内置 **文泉驿微米黑 (WenQuanYi Micro Hei)** 字体文件（位于 `assets/font/`），该字体遵循 **Apache 2.0 / SIL Open Font License**。

- **字体优先级**：插件会优先使用 `assets/font/` 目录下的字体文件，如果未找到或加载失败，则会尝试搜索系统自带的 CJK 字体（如 Noto Sans CJK, LXGW WenKai 等）。
- **兜底方案**：如果系统和插件目录均无可用中文字体，插件将降级使用 PIL 默认字体进行渲染。此时中文可能会显示为方块，建议用户根据日志提示安装相应字体以获得最佳显示效果。

## 开源协议

MIT
