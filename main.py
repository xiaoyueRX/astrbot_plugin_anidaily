import os
import asyncio
import logging
from typing import List
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import AstrBotConfig
import astrbot.api.message_components as Comp
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .yuc_parser import fetch_and_parse
from .pic_renderer import render, render_weekly
from .data_handler import DataHandler

logger = logging.getLogger("astrbot")

@register("astrbot_plugin_anidaily", "xiaoyueRX", "基于 yuc.wiki 的 4K 高清番剧每日/周历推送，支持全自动换季感知与动态集数推算", "0.2.0", "https://github.com/xiaoyueRX/astrbot_plugin_anidaily")
class AniDailyPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        os.makedirs(self.data_dir, exist_ok=True)
        self.data_handler = DataHandler(self.data_dir)
        self.scheduler = AsyncIOScheduler()
        self._setup_scheduler()

    def _setup_scheduler(self):
        # 1. 每日番剧推送 (Daily Report)
        enable_daily = self.config.get("enable_daily_push", True)
        daily_cron = self.config.get("daily_cron_expr", "20 8 * * *")
        
        # 兼容旧配置字段 cron_expr
        if "cron_expr" in self.config and "daily_cron_expr" not in self.config:
            daily_cron = self.config.get("cron_expr", "20 8 * * *")

        if enable_daily:
            try:
                parts = daily_cron.split()
                if len(parts) == 5:
                    self.scheduler.add_job(
                        self.daily_push,
                        'cron',
                        minute=parts[0],
                        hour=parts[1],
                        day=parts[2],
                        month=parts[3],
                        day_of_week=parts[4],
                        id='anidaily_daily_job'
                    )
                    logger.info(f"[anidaily] Daily push job registered with cron: {daily_cron}")
            except Exception as e:
                logger.error(f"[anidaily] Daily scheduler setup failed: {e}")

        # 2. 全景周历看板推送 (Weekly Overview)
        enable_weekly = self.config.get("enable_weekly_push", True)
        weekly_cron = self.config.get("weekly_cron_expr", "30 8 * * *")
        if enable_weekly:
            try:
                w_parts = weekly_cron.split()
                if len(w_parts) == 5:
                    self.scheduler.add_job(
                        self.weekly_push,
                        'cron',
                        minute=w_parts[0],
                        hour=w_parts[1],
                        day=w_parts[2],
                        month=w_parts[3],
                        day_of_week=w_parts[4],
                        id='anidaily_weekly_job'
                    )
                    logger.info(f"[anidaily] Weekly push job registered with cron: {weekly_cron}")
            except Exception as e:
                logger.error(f"[anidaily] Weekly scheduler setup failed: {e}")

        if enable_daily or enable_weekly:
            try:
                self.scheduler.start()
            except Exception as e:
                logger.error(f"[anidaily] Scheduler start failed: {e}")

    async def daily_push(self):
        logger.info("[anidaily] Starting daily push...")
        subscribers = self.data_handler.get_subscribers()
        if not subscribers:
            return

        try:
            data = await asyncio.to_thread(fetch_and_parse)
            if "error" in data:
                return
            
            latest_png = os.path.join(self.data_dir, "anidaily_latest.png")
            await asyncio.to_thread(render, data, latest_png)
            
            for origin in subscribers.keys():
                try:
                    await self.context.send_message(
                        origin,
                        MessageChain().image(file=latest_png)
                    )
                except Exception as e:
                    logger.error(f"[anidaily] Daily push to {origin} failed: {e}")
            
            # 自动清理临时图片碎片
            if self.config.get("auto_clean_temp", True):
                self._safe_clean_temp()
        except Exception as e:
            logger.error(f"[anidaily] Daily push process failed: {e}")

    async def weekly_push(self):
        logger.info("[anidaily] Starting weekly push...")
        subscribers = self.data_handler.get_subscribers()
        if not subscribers:
            return

        try:
            data = await asyncio.to_thread(fetch_and_parse)
            if "error" in data:
                return
            
            season_png = os.path.join(self.data_dir, "aniseason_latest.png")
            await asyncio.to_thread(render_weekly, data, season_png)
            
            for origin in subscribers.keys():
                try:
                    await self.context.send_message(
                        origin,
                        MessageChain().image(file=season_png)
                    )
                except Exception as e:
                    logger.error(f"[anidaily] Weekly push to {origin} failed: {e}")
            
            # 自动清理临时图片碎片
            if self.config.get("auto_clean_temp", True):
                self._safe_clean_temp()
        except Exception as e:
            logger.error(f"[anidaily] Weekly push process failed: {e}")

    def _safe_clean_temp(self):
        """安全清理除最新单文件覆盖之外的临时文件碎片，保障磁盘恒定"""
        try:
            allowed_files = {"anidaily_latest.png", "aniseason_latest.png", "subscribers.json"}
            if os.path.exists(self.data_dir):
                for f in os.listdir(self.data_dir):
                    if f not in allowed_files and (f.endswith(".png") or f.endswith(".tmp")):
                        try:
                            os.unlink(os.path.join(self.data_dir, f))
                        except Exception:
                            pass
        except Exception as e:
            logger.warning(f"[anidaily] Safe clean temp failed: {e}")

    @filter.command_group("anidaily")
    def anidaily(self):
        pass

    @anidaily.command("now")
    async def now(self, event: AstrMessageEvent):
        '''立即获取今日番剧卡片'''
        yield event.plain_result("正在抓取今日番剧数据，请稍候...")
        try:
            data = await asyncio.to_thread(fetch_and_parse)
            if "error" in data:
                yield event.plain_result(f"抓取失败: {data['error']}")
                return
            
            latest_png = os.path.join(self.data_dir, "anidaily_latest.png")
            await asyncio.to_thread(render, data, latest_png)
            
            yield event.image_result(latest_png)
            if self.config.get("auto_clean_temp", True):
                self._safe_clean_temp()
        except Exception as e:
            logger.error(f"[anidaily] now command failed: {e}")
            yield event.plain_result(f"生成卡片失败: {e}")

    @anidaily.command("week")
    async def week(self, event: AstrMessageEvent):
        '''立即获取全景周历看板'''
        yield event.plain_result("正在生成全景周历看板，请稍候...")
        try:
            data = await asyncio.to_thread(fetch_and_parse)
            if "error" in data:
                yield event.plain_result(f"抓取失败: {data['error']}")
                return
            
            season_png = os.path.join(self.data_dir, "aniseason_latest.png")
            await asyncio.to_thread(render_weekly, data, season_png)
            
            yield event.image_result(season_png)
            if self.config.get("auto_clean_temp", True):
                self._safe_clean_temp()
        except Exception as e:
            logger.error(f"[anidaily] week command failed: {e}")
            yield event.plain_result(f"生成周历失败: {e}")

    @filter.command("aniseason")
    async def aniseason(self, event: AstrMessageEvent):
        '''立即获取全景周历看板 (/aniseason 快捷指令)'''
        yield event.plain_result("正在生成全景周历看板，请稍候...")
        try:
            data = await asyncio.to_thread(fetch_and_parse)
            if "error" in data:
                yield event.plain_result(f"抓取失败: {data['error']}")
                return
            
            season_png = os.path.join(self.data_dir, "aniseason_latest.png")
            await asyncio.to_thread(render_weekly, data, season_png)
            
            yield event.image_result(season_png)
            if self.config.get("auto_clean_temp", True):
                self._safe_clean_temp()
        except Exception as e:
            logger.error(f"[anidaily] aniseason command failed: {e}")
            yield event.plain_result(f"生成周历失败: {e}")

    @anidaily.command("sub")
    async def sub(self, event: AstrMessageEvent):
        '''订阅番剧推送 (管理员)'''
        if not event.is_admin:
            yield event.plain_result("❌ 权限不足：只有管理员可以操作订阅。")
            return
        origin = event.unified_msg_origin
        if self.data_handler.add_subscriber(origin):
            yield event.plain_result("订阅成功！将为你推送番剧更新通知。")
        else:
            yield event.plain_result("你已经订阅过了。")

    @anidaily.command("unsub")
    async def unsub(self, event: AstrMessageEvent):
        '''取消番剧推送 (管理员)'''
        if not event.is_admin:
            yield event.plain_result("❌ 权限不足：只有管理员可以操作订阅。")
            return
        origin = event.unified_msg_origin
        if self.data_handler.remove_subscriber(origin):
            yield event.plain_result("取消订阅成功。")
        else:
            yield event.plain_result("你还没有订阅。")

    @anidaily.command("list")
    async def list_subs(self, event: AstrMessageEvent):
        '''查看当前订阅列表 (管理员)'''
        if not event.is_admin:
            yield event.plain_result("❌ 权限不足。")
            return
        subs = self.data_handler.get_subscribers()
        if not subs:
            yield event.plain_result("当前没有任何订阅。")
            return
        
        msg = "当前订阅列表：\n"
        for origin, info in subs.items():
            msg += f"- {origin} (订阅于 {info['subscribed_at']})\n"
        yield event.plain_result(msg.strip())
