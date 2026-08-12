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
from .pic_renderer import render
from .data_handler import DataHandler

logger = logging.getLogger("astrbot")

@register("astrbot_plugin_anidaily", "xiaoyueRX", "基于 yuc.wiki 的每日番剧推送卡片，订阅制定时推送", "0.1.3", "https://github.com/xiaoyueRX/astrbot_plugin_anidaily")
class AniDailyPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        self.data_handler = DataHandler(self.data_dir)
        self.scheduler = AsyncIOScheduler()
        self._setup_scheduler()

    def _setup_scheduler(self):
        cron_expr = self.config.get("cron_expr", "20 8 * * *")
        try:
            # 简单解析 "minute hour day month day_of_week"
            parts = cron_expr.split()
            if len(parts) == 5:
                self.scheduler.add_job(
                    self.daily_push,
                    'cron',
                    minute=parts[0],
                    hour=parts[1],
                    day=parts[2],
                    month=parts[3],
                    day_of_week=parts[4]
                )
                self.scheduler.start()
        except Exception as e:
            logger.error(f"[anidaily] Scheduler setup failed: {e}")

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
                    logger.error(f"[anidaily] Push to {origin} failed: {e}")
        except Exception as e:
            logger.error(f"[anidaily] Daily push process failed: {e}")

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
        except Exception as e:
            logger.error(f"[anidaily] now command failed: {e}")
            yield event.plain_result(f"生成卡片失败: {e}")

    @anidaily.command("sub")
    async def sub(self, event: AstrMessageEvent):
        '''订阅每日番剧推送 (管理员)'''
        if not event.is_admin:
            yield event.plain_result("❌ 权限不足：只有管理员可以操作订阅。")
            return
        origin = event.unified_msg_origin
        if self.data_handler.add_subscriber(origin):
            yield event.plain_result("订阅成功！每天 08:20 将为你推送今日番剧。")
        else:
            yield event.plain_result("你已经订阅过了。")

    @anidaily.command("unsub")
    async def unsub(self, event: AstrMessageEvent):
        '''取消每日番剧推送 (管理员)'''
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

