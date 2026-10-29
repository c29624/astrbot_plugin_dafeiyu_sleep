"""大肥鱼·随机睡觉插件 v0.2（实施顺序第 2 步）

第 1 步（v0.1/v0.1.1，已完成并实测）：
- 守门拦截器 priority=sys.maxsize-10 + fail-open（异常=放行+ERROR）
- 睡眠窗口全渠道静默：普通消息截停入缓冲（按会话隔离、上限截断、落盘、启动补时）
- / 指令豁免（v0.1.1：框架 waking_check 会剥掉 message_str 的唤醒前缀，改读消息链第一个 Plain）
- 晨间冲刷：消息本体走消息链拼接入历史（LivingMemory 反思引擎可读）；
  事实行走 on_llm_request 的 extra_user_content_parts(mark_as_temp) 不入历史保前缀缓存

第 2 步（本版，设计文档第三节/第十节）：
- 随机作息生成器：每日抽签入睡时刻（以基准为中心近正态波动，硬边界内），
  起床由入睡+时长推导：最终起床 = clamp(入睡 + 抽样时长(含周末加成) + 睡眠债 + 赖床加成, 最早起床, 硬顶)
- 赖床掷骰：入睡晚于阈值→基础概率、每再晚30分钟递增、封顶100%；失眠夜必赖床；早睡10%偶发
- 失眠夜：10% 概率睡到失眠上限（默认 02:30），标记持久化（"没睡醒"注入在第 5 步）
- 持久化：当天抽好的入睡/起床/赖床/失眠全落盘——重启不重抽、跨日滚动新作息
- 配置校验（数值类）：单字段非法回落默认；跨字段矛盾（归一化空间判）双方回落；全部记 WARNING
- /重抛作息 /强制失眠 激活；/强制入睡 HH:MM 按公式重导起床；/睡觉状态 显示今日抽签
- random_schedule=false 时退回 v0.1 的固定作息模式

后续步骤：③叫醒频道 ④临时清醒+回睡+告别语 ⑤午睡补偿+没睡醒 ⑥内置指引+配置页打磨
"""

import asyncio
import json
import random
import sys
import time
from datetime import datetime, time as dtime, timedelta

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register

try:
    from astrbot.api.star import StarTools
except ImportError:  # 旧版无 StarTools
    StarTools = None

try:
    from astrbot.core.agent.message import TextPart
except ImportError:  # 版本变动时的兜底（fail-open：注入失败不拦消息）
    TextPart = None

try:
    from astrbot.core.message.components import Plain
except ImportError:
    Plain = None

PLUGIN_NAME = "astrbot_plugin_dafeiyu_sleep"
STATE_FILE = "sleep_state.json"

PHASE_AWAKE = "awake"
PHASE_SLEEPING = "sleeping"

# ---- 默认值常量表（自洽：回落机制的最坏情况=零配置运行）----
DEFAULTS = {
    "enabled": True,
    "random_schedule": True,
    "bedtime_base": "23:00",
    "bedtime_earliest": "22:15",
    "bedtime_latest": "01:10",
    "insomnia_chance": 10.0,        # %
    "insomnia_latest": "02:30",
    "sleep_hours_base": 7.5,
    "sleep_hours_sigma": 1.0,
    "weekend_extra_min": 30.0,
    "weekend_extra_max": 90.0,
    "wake_earliest": "06:40",
    "wake_hardcap": "09:30",
    "oversleep_threshold": "00:30",
    "oversleep_base": 50.0,         # %
    "oversleep_per_30": 15.0,       # % / 每30分钟
    "oversleep_bonus_min": 10.0,
    "oversleep_bonus_max": 40.0,
    "earlybed_oversleep_chance": 10.0,  # %
    "debt_ratio": 50.0,             # % 被叫醒N分钟→顺延N×比例
    "buffer_max_per_session": 50,
    "buffer_max_total": 500,
    "buffer_text_truncate": 500,
    "day_fact_hours": 6.0,
    "fixed_bedtime": "23:00",
    "fixed_waketime": "07:00",
}


def _parse_hhmm(value):
    """'HH:MM' → 当日分钟数；非法返回 None"""
    try:
        s = str(value).strip()
        hh, mm = s.split(":")
        h, m = int(hh), int(mm)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h * 60 + m
    except Exception:
        pass
    return None


def _rel_minutes(t_min, base_min):
    """跨午夜归一化：把时刻换算成"以基准入睡点为锚的相对分钟数"（±12h）。
    基准 23:00 时：22:15=-45、01:10=+130、02:30=+210。"""
    return (t_min - base_min + 720) % 1440 - 720


def _fmt_dt(ts):
    return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")


def _fmt_hm(ts):
    return datetime.fromtimestamp(ts).strftime("%H:%M")


@register(PLUGIN_NAME, "29624", "大肥鱼的真实作息：到点真睡、早上真醒、记得夜里谁说了什么", "0.2.0")
class DafeiyuSleepPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self._apply_config(config or {})

        # 运行时状态
        self.phase = PHASE_AWAKE
        self._sleep_window = None  # (bed_ts, wake_ts) 本次睡眠的起止（自然夜/固定窗/午睡/强制）
        self._buffers = {}  # umo -> [entry dict]
        self._truncated = set()
        self._morning_pending = {}  # umo -> {sleep_time, wake_time, msg_count}（运行时）
        self._wake_at = None
        self._night = None  # 今日/今夜抽签记录（dict，见 _roll_tonight）
        self._last_night = None  # 上一夜完成记录（失眠标记留给第5步）
        self._debt_minutes = 0.0  # 睡眠债（第4步临时清醒累计，第2步先建字段）
        self._lock = asyncio.Lock()
        self._tick_task = None
        self._data_dir = None

    # ---------- 配置解析与校验（数值类：回落默认+WARNING；跨字段矛盾：双方回落） ----------

    def _warn(self, key, why):
        logger.warning(f"[睡觉] 配置项 {key} {why}，已回落默认值 {DEFAULTS.get(key)}")

    def _apply_config(self, cfg):
        get = lambda k: cfg.get(k, DEFAULTS[k])

        self.enabled = bool(get("enabled"))
        self.schedule_random = bool(get("random_schedule"))

        # 时间项（HH:MM → 分钟数，非法回落）
        tkeys = [
            "bedtime_base",
            "bedtime_earliest",
            "bedtime_latest",
            "insomnia_latest",
            "wake_earliest",
            "wake_hardcap",
            "oversleep_threshold",
            "fixed_bedtime",
            "fixed_waketime",
        ]
        tv = {}
        for k in tkeys:
            m = _parse_hhmm(get(k))
            if m is None:
                self._warn(k, "不是合法的 HH:MM")
                m = _parse_hhmm(DEFAULTS[k])
            tv[k] = m

        # 数值项（范围校验，非法回落）
        def num(key, lo, hi, cast=float):
            try:
                v = cast(get(key))
                if not (lo <= v <= hi):
                    raise ValueError
            except Exception:
                self._warn(key, "非法或超出范围")
                v = DEFAULTS[key]
            return v

        self.c_insomnia_chance = num("insomnia_chance", 0, 100)
        self.c_hours_base = num("sleep_hours_base", 4, 12)
        self.c_hours_sigma = num("sleep_hours_sigma", 0.1, 4)
        self.c_wk_min = num("weekend_extra_min", 0, 300)
        self.c_wk_max = num("weekend_extra_max", 0, 300)
        self.c_os_base = num("oversleep_base", 0, 100)
        self.c_os_per30 = num("oversleep_per_30", 0, 100)
        self.c_os_bonus_min = num("oversleep_bonus_min", 0, 180)
        self.c_os_bonus_max = num("oversleep_bonus_max", 0, 180)
        self.c_early_chance = num("earlybed_oversleep_chance", 0, 100)
        self.c_debt_ratio = num("debt_ratio", 0, 100)
        self.day_fact_hours = num("day_fact_hours", 0, 48)

        buf_per = int(num("buffer_max_per_session", 1, 10000, int))
        buf_total = int(num("buffer_max_total", 1, 100000, int))
        if buf_per > buf_total:  # 跨字段矛盾：双方回落
            logger.warning(
                f"[睡觉] 配置矛盾：单会话上限({buf_per}) > 总量上限({buf_total})，双方回落默认"
            )
            buf_per, buf_total = DEFAULTS["buffer_max_per_session"], DEFAULTS["buffer_max_total"]
        self.buf_per_session, self.buf_total = buf_per, buf_total
        self.text_truncate = int(num("buffer_text_truncate", 20, 20000, int))

        # 入睡族跨字段校验（归一化空间：以基准为锚）
        base = tv["bedtime_base"]
        earliest = _rel_minutes(tv["bedtime_earliest"], base)
        latest = _rel_minutes(tv["bedtime_latest"], base)
        ins_late = _rel_minutes(tv["insomnia_latest"], base)
        if earliest >= latest:
            logger.warning(
                f"[睡觉] 配置矛盾：最早入睡({tv['bedtime_earliest']//60:02d}:{tv['bedtime_earliest']%60:02d})"
                f" ≥ 最晚入睡({tv['bedtime_latest']//60:02d}:{tv['bedtime_latest']%60:02d})（归一化后），双方回落默认"
            )
            earliest = _rel_minutes(_parse_hhmm(DEFAULTS["bedtime_earliest"]), base)
            latest = _rel_minutes(_parse_hhmm(DEFAULTS["bedtime_latest"]), base)
        if not (earliest <= 0 <= latest):  # 基准必须落在边界内
            logger.warning("[睡觉] 配置矛盾：基准入睡点不在最早/最晚边界内，基准回落默认")
            base = _parse_hhmm(DEFAULTS["bedtime_base"])
            earliest = _rel_minutes(_parse_hhmm(DEFAULTS["bedtime_earliest"]), base)
            latest = _rel_minutes(_parse_hhmm(DEFAULTS["bedtime_latest"]), base)
            ins_late = _rel_minutes(_parse_hhmm(DEFAULTS["insomnia_latest"]), base)
        if ins_late <= latest:  # 失眠上限必须比常规最晚更晚
            logger.warning("[睡觉] 配置矛盾：失眠夜最晚未晚于常规最晚入睡，回落默认")
            ins_late = _rel_minutes(_parse_hhmm(DEFAULTS["insomnia_latest"]), base)

        # 起床族跨字段校验（同为次日清晨时间轴，可直接比较）
        if tv["wake_earliest"] >= tv["wake_hardcap"]:
            logger.warning("[睡觉] 配置矛盾：最早起床 ≥ 最晚起床硬顶，双方回落默认")
            tv["wake_earliest"] = _parse_hhmm(DEFAULTS["wake_earliest"])
            tv["wake_hardcap"] = _parse_hhmm(DEFAULTS["wake_hardcap"])
        if self.c_wk_min > self.c_wk_max:
            logger.warning("[睡觉] 配置矛盾：周末加成下限 > 上限，双方回落默认")
            self.c_wk_min, self.c_wk_max = DEFAULTS["weekend_extra_min"], DEFAULTS["weekend_extra_max"]
        if self.c_os_bonus_min > self.c_os_bonus_max:
            logger.warning("[睡觉] 配置矛盾：赖床加成下限 > 上限，双方回落默认")
            self.c_os_bonus_min, self.c_os_bonus_max = DEFAULTS["oversleep_bonus_min"], DEFAULTS["oversleep_bonus_max"]

        # 汇总生效值
        self.c_base = base
        self.c_earliest, self.c_latest, self.c_ins_late = earliest, latest, ins_late
        self.c_bed_sigma = max(15.0, (latest - earliest) / 4.0)  # 近正态离散度由边界宽度推导
        self.c_thr = _rel_minutes(tv["oversleep_threshold"], base)
        self.c_wake_lo = dtime(tv["wake_earliest"] // 60, tv["wake_earliest"] % 60)
        self.c_wake_hi = dtime(tv["wake_hardcap"] // 60, tv["wake_hardcap"] % 60)
        self.bed_min = tv["fixed_bedtime"]
        self.wake_min = tv["fixed_waketime"]

        # 提示词模板（文案类：自由优先，不做数值式校验）
        self.tpl_morning_fact = str(
            cfg.get(
                "morning_fact_template",
                "[系统信息-昨晚作息] 你昨晚 {sleep_time} 入睡、{wake_time} 醒来，"
                "睡眠期间收到 {msg_count} 条消息（附后）。",
            )
        )
        self.tpl_day_fact = str(
            cfg.get("day_fact_template", "[系统信息] 昨晚作息：{sleep_time} 睡，{wake_time} 醒。")
        )
        self.tpl_body_header = str(
            cfg.get("body_header_template", "（以下是睡眠期间收到的 {msg_count} 条消息）")
        )
        # 唤醒前缀：跟随平台配置（waking_check 支持多个），固定含 "/"
        prefixes = ["/"]
        try:
            cfg_pfx = self.context.get_config().get("wake_prefix", [])
            if isinstance(cfg_pfx, str):
                cfg_pfx = [cfg_pfx]
            prefixes.extend(str(p) for p in cfg_pfx if str(p))
        except Exception:
            pass
        self.cmd_prefixes = tuple(dict.fromkeys(prefixes))

    # ---------- 生命周期 ----------

    async def initialize(self):
        try:
            if StarTools is not None:
                self._data_dir = StarTools.get_data_dir(PLUGIN_NAME)
            else:
                from pathlib import Path

                self._data_dir = Path("data/plugin_data") / PLUGIN_NAME
                self._data_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            logger.warning("[睡觉] 数据目录创建失败，缓冲不落盘（仅内存）")

        self._load_state()
        self._startup_recover()
        self._tick_task = asyncio.create_task(self._tick_loop())
        line = f"[睡觉] v0.2 已启动：状态={self.phase}"
        if self.schedule_random:
            n = self._night
            if n:
                line += (
                    f" 今日抽签：{_fmt_hm(n['bed_ts'])} 睡 → {_fmt_hm(n['wake_ts'])} 醒"
                    f"（{'失眠夜 ' if n['insomnia'] else ''}{'含赖床 ' if n['oversleep'] else ''}"
                    f"时长{n['dur_min']:.0f}min{'+周末' if n['weekend_extra'] > 0 else ''}）"
                )
        else:
            line += (
                f" 固定作息 {self.bed_min // 60:02d}:{self.bed_min % 60:02d} → "
                f"{self.wake_min // 60:02d}:{self.wake_min % 60:02d}"
            )
        line += f" 缓冲={sum(len(v) for v in self._buffers.values())}条"
        logger.info(line)

    async def terminate(self):
        if self._tick_task:
            self._tick_task.cancel()
        self._save_state()

    # ---------- 持久化 ----------

    def _state_path(self):
        if self._data_dir is None:
            return None
        return self._data_dir / STATE_FILE

    def _load_state(self):
        path = self._state_path()
        if path is None or not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.phase = data.get("phase", PHASE_AWAKE)
            self._buffers = data.get("buffers", {})
            self._truncated = set(data.get("truncated", []))
            self._sleep_window = tuple(data["sleep_window"]) if data.get("sleep_window") else None
            self._wake_at = data.get("wake_at")
            self._night = data.get("night")
            self._last_night = data.get("last_night")
            self._debt_minutes = float(data.get("debt_minutes", 0))
        except Exception:
            logger.warning("[睡觉] 状态文件损坏，按全新状态启动（缓冲可能丢失）")

    def _save_state(self):
        path = self._state_path()
        if path is None:
            return
        try:
            data = {
                "phase": self.phase,
                "buffers": self._buffers,
                "truncated": list(self._truncated),
                "sleep_window": list(self._sleep_window) if self._sleep_window else None,
                "wake_at": self._wake_at,
                "night": self._night,
                "last_night": self._last_night,
                "debt_minutes": self._debt_minutes,
                "saved_at": time.time(),
            }
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception:
            logger.warning("[睡觉] 状态落盘失败（不影响运行）")

    def _startup_recover(self):
        """启动补时：按盘上状态+当前时刻恢复，不重抽、不丢缓冲"""
        now = time.time()
        now_dt = datetime.now()
        if self.phase == PHASE_SLEEPING:
            wake_ts = self._sleep_window[1] if self._sleep_window else 0
            if now >= wake_ts:
                self._set_morning_pending()
                self._finish_night(actual_ts=now)
                self.phase = PHASE_AWAKE
                logger.info("[睡觉] 启动补时：已过起床时刻，直接转清醒（按原定作息，不重抽）")
            else:
                logger.info("[睡觉] 启动补时：仍在睡眠窗口内，恢复睡眠态继续缓冲")
        else:
            # 孤儿缓冲补救：醒来后没等到任何会话首条消息就重启 → 依据缓冲重建晨间待注入
            if self._buffers and not self._morning_pending:
                self._set_morning_pending()
                if self._sleep_window:
                    logger.info("[睡觉] 启动补时：发现未冲刷的夜间缓冲，重建晨间待注入")
        if self.schedule_random:
            self._ensure_tonight(now_dt)

    # ---------- 随机作息生成器（设计文档第三节） ----------

    def _anchor_base_dt(self, anchor_date):
        """某夜的基准入睡时刻（anchor 日期的基准点，如 23:00）"""
        return datetime.combine(anchor_date, dtime(self.c_base // 60, self.c_base % 60))

    def _derive_wake(self, anchor_dt, bed_dt, insomnia):
        """最终起床 = clamp(入睡 + 抽样时长 + 周末加成 + 睡眠债 + 赖床加成, 最早起床, 硬顶)"""
        dur = random.gauss(self.c_hours_base * 60, self.c_hours_sigma * 60)
        dur = max(self.c_hours_base * 60 - 2 * self.c_hours_sigma * 60,
                  min(self.c_hours_base * 60 + 2 * self.c_hours_sigma * 60, dur))
        # 周末判定按起床日（周五深夜睡周六醒→按周六）
        wake_day = (anchor_dt + timedelta(days=1)).date()
        weekend = wake_day.weekday() >= 5
        wk_extra = random.uniform(self.c_wk_min, self.c_wk_max) if weekend else 0.0

        # 赖床掷骰
        offset = (bed_dt - anchor_dt).total_seconds() / 60.0  # 相对基准的偏移分钟
        if insomnia:
            hit = True  # 失眠必赖床
        elif offset > self.c_thr:
            p = min(100.0, self.c_os_base + self.c_os_per30 * int((offset - self.c_thr) // 30))
            hit = random.random() * 100 < p
        else:
            hit = random.random() * 100 < self.c_early_chance  # 早睡10%偶发
        bonus = random.uniform(self.c_os_bonus_min, self.c_os_bonus_max) if hit else 0.0

        debt_extra = self._debt_minutes * self.c_debt_ratio / 100.0
        return {
            "dur_min": round(dur, 1),
            "weekend_extra": round(wk_extra, 1),
            "oversleep": hit,
            "bonus_min": round(bonus, 1),
            "debt_extra": round(debt_extra, 1),
        }

    def _roll_tonight(self, now_dt=None, force_bed_rel=None, force_insomnia=None, source="auto"):
        """抽今晚的作息。force_* 仅供调试指令拨表，走同一套公式。"""
        now_dt = now_dt or datetime.now()
        anchor_date = now_dt.date()
        base_dt = self._anchor_base_dt(anchor_date)

        if force_insomnia is not None:
            insomnia = bool(force_insomnia)
        else:
            insomnia = random.random() * 100 < self.c_insomnia_chance
        if force_bed_rel is not None:
            off = float(force_bed_rel)
        elif insomnia:
            off = random.uniform(self.c_latest, self.c_ins_late)  # 失眠：常规最晚~失眠上限
        else:
            off = random.gauss(0, self.c_bed_sigma)
            off = max(self.c_earliest, min(self.c_latest, off))

        bed_dt = base_dt + timedelta(minutes=off)
        rec = self._derive_wake(base_dt, bed_dt, insomnia)
        # 最终起床 = clamp(入睡 + 时长 + 周末 + 赖床 + 债, 最早起床, 硬顶)
        lo = datetime.combine(anchor_date + timedelta(days=1), self.c_wake_lo)
        hi = datetime.combine(anchor_date + timedelta(days=1), self.c_wake_hi)
        wake_dt = bed_dt + timedelta(
            minutes=rec["dur_min"] + rec["weekend_extra"] + rec["bonus_min"] + rec["debt_extra"]
        )
        rec.update(
            anchor=anchor_date.isoformat(),
            bed_ts=bed_dt.timestamp(),
            wake_ts=min(max(wake_dt, lo), hi).timestamp(),
            insomnia=insomnia,
            source=source,
        )
        self._night = rec
        self._save_state()  # 抽签即落盘：重启不重抽
        return rec

    def _ensure_tonight(self, now_dt):
        """盘上没有今晚作息或已跨日 → 滚动抽新一天；当天已有 → 不重抽"""
        today = now_dt.date().isoformat()
        if self._night is None or self._night.get("anchor") != today:
            stale = self._night
            rec = self._roll_tonight(now_dt)
            if stale:
                logger.info(
                    f"[睡觉] 跨日滚动：新一天抽签 {_fmt_hm(rec['bed_ts'])} 睡 → {_fmt_hm(rec['wake_ts'])} 醒"
                )
            return rec
        return self._night

    async def _apply_debt(self, minutes):
        """第4步临时清醒结算入口：睡眠债顺延当前起床（硬顶内）。本步先建通路，债恒为0。"""
        self._debt_minutes += float(minutes)
        if self.phase == PHASE_SLEEPING and self._sleep_window:
            bed_ts, wake_ts = self._sleep_window
            bed_dt = datetime.fromtimestamp(bed_ts)
            extra = timedelta(minutes=minutes * self.c_debt_ratio / 100.0)
            hi_dt = bed_dt + timedelta(hours=12)  # 兜底：顺延不越过入睡+12h
            new_wake = min(datetime.fromtimestamp(wake_ts) + extra, hi_dt).timestamp()
            self._sleep_window = (bed_ts, new_wake)
            if self._night and self._night.get("anchor") == datetime.now().date().isoformat():
                self._night["wake_ts"] = new_wake
        self._save_state()

    # ---------- 窗口计算（固定模式用） ----------

    def _span_minutes(self):
        span = (self.wake_min - self.bed_min) % 1440
        return span if span > 0 else 480

    def _natural_window(self, now=None):
        now = now or datetime.now()
        bed = now.replace(hour=self.bed_min // 60, minute=self.bed_min % 60, second=0, microsecond=0)
        wake = bed + timedelta(minutes=self._span_minutes())
        if bed <= now < wake:
            return bed, wake
        if now >= wake:
            bed += timedelta(days=1)
            wake = bed + timedelta(minutes=self._span_minutes())
        return bed, wake

    def _next_wake_after(self, now_dt):
        wake = now_dt.replace(
            hour=self.wake_min // 60, minute=self.wake_min % 60, second=0, microsecond=0
        )
        if wake <= now_dt:
            wake += timedelta(days=1)
        return wake

    # ---------- 定时循环（30s 轮询） ----------

    async def _tick_loop(self):
        while True:
            await asyncio.sleep(30)
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("[睡觉] 定时循环异常（忽略继续）", exc_info=True)

    async def _tick(self):
        if not self.enabled:
            return
        now_dt = datetime.now()
        now = now_dt.timestamp()
        async with self._lock:
            if self.phase == PHASE_SLEEPING:
                wake_ts = self._sleep_window[1] if self._sleep_window else 0
                if wake_ts and now >= wake_ts:
                    await self._wake()
            else:
                if self.schedule_random:
                    self._ensure_tonight(now_dt)
                    n = self._night
                    if n and n["bed_ts"] <= now:
                        await self._enter_sleep(
                            n["bed_ts"], n["wake_ts"], note=f"（今日抽签 {_fmt_hm(n['bed_ts'])}）"
                        )
                else:
                    bed, wake = self._natural_window(now_dt)
                    if bed.timestamp() <= now < wake.timestamp():
                        await self._enter_sleep(bed.timestamp(), wake.timestamp())

    async def _enter_sleep(self, bed_ts, wake_ts, note=""):
        self.phase = PHASE_SLEEPING
        self._sleep_window = (bed_ts, wake_ts)
        self._morning_pending = {}
        self._save_state()
        logger.info(
            f"[睡觉] 入睡{note}：{_fmt_dt(bed_ts)} → {_fmt_hm(wake_ts)}，守门挂起，开始缓冲"
        )

    def _set_morning_pending(self, actual_wake_ts=None):
        """为所有有夜间缓冲的会话登记晨间事实（各自下一次请求时注入）"""
        if not self._sleep_window:
            return
        bed_ts, wake_ts = self._sleep_window
        wake_disp = actual_wake_ts or wake_ts
        for umo, buf in self._buffers.items():
            if buf:
                self._morning_pending[umo] = {
                    "sleep_time": _fmt_hm(bed_ts),
                    "wake_time": _fmt_hm(wake_disp),
                    "msg_count": len(buf),
                }

    def _finish_night(self, actual_ts=None):
        """自然醒/启动补时：归档今夜记录，抽明晚新作息"""
        self._last_night = dict(self._night) if self._night else None
        if self._last_night and actual_ts:
            self._last_night["wake_actual_ts"] = actual_ts
        self._debt_minutes = 0.0  # 债随夜结算清零
        self._roll_tonight(datetime.fromtimestamp(actual_ts) if actual_ts else datetime.now())

    async def _wake(self):
        actual = time.time()
        self._set_morning_pending(actual)
        self.phase = PHASE_AWAKE
        self._wake_at = actual
        self._finish_night(actual)
        total = sum(len(v) for v in self._buffers.values())
        self._save_state()
        n = self._night or {}
        logger.info(
            f"[睡觉] 起床。夜间共缓冲 {total} 条消息，各会话将在其下一次对话时注入；"
            f"今晚抽签：{_fmt_hm(n.get('bed_ts', time.time()))} 睡 → {_fmt_hm(n.get('wake_ts', time.time()))} 醒"
        )

    # ---------- 守门（fail-open） ----------

    @filter.event_message_type(filter.EventMessageType.ALL, priority=sys.maxsize - 10)
    async def guard(self, event: AstrMessageEvent):
        try:
            await self._guard(event)
        except Exception:
            # fail-open：宁可半夜穿帮回一句，不可她永久哑巴
            logger.error("[睡觉] 守门异常，fail-open 放行本条消息", exc_info=True)

    def _chain_head_text(self, event: AstrMessageEvent) -> str:
        """消息链里第一个 Plain 的文本。

        框架 waking_check 阶段会把唤醒前缀从 event.message_str 剥掉（所有插件之前），
        但消息链组件不动——斜杠只在这里还活着。
        """
        if Plain is None:
            return ""
        try:
            for comp in event.get_messages():
                if isinstance(comp, Plain):
                    return (getattr(comp, "text", "") or "").lstrip()
        except Exception:
            pass
        return ""

    def _is_command(self, event: AstrMessageEvent, message_str: str) -> bool:
        head = self._chain_head_text(event)
        return head.startswith(self.cmd_prefixes) or message_str.startswith(self.cmd_prefixes)

    async def _guard(self, event: AstrMessageEvent):
        if not self.enabled:
            return
        umo = event.unified_msg_origin
        if ":FriendMessage:" not in umo and ":GroupMessage:" not in umo:
            return

        text = (event.message_str or "").strip()
        if self._is_command(event, text):
            return  # 指令豁免：睡觉不影响干活

        async with self._lock:
            if self.phase == PHASE_AWAKE:
                if umo in self._morning_pending:
                    self._flush_morning_into(event, umo)
                return
            self._buffer_message(event, umo, text)
        event.stop_event()
        self._save_state()

    def _buffer_message(self, event: AstrMessageEvent, umo: str, text: str):
        entry_text = text
        if not entry_text:
            placeholder = ""
            try:
                for comp in event.get_messages():
                    cls = type(comp).__name__
                    if cls == "Image":
                        placeholder = "[图片]"
                        break
                    if cls in ("Record", "File", "Video"):
                        placeholder = f"[{cls if cls != 'Record' else '语音'}]"
                        break
            except Exception:
                pass
            entry_text = placeholder or "[非文本消息]"
        if len(entry_text) > self.text_truncate:
            entry_text = entry_text[: self.text_truncate] + "…(过长截断)"

        try:
            ts = float(getattr(event.message_obj, "timestamp", 0) or time.time())
        except Exception:
            ts = time.time()
        try:
            sender_id = str(event.get_sender_id() or "?")
            sender_name = str(event.get_sender_name() or sender_id)
        except Exception:
            sender_id, sender_name = "?", "?"

        buf = self._buffers.setdefault(umo, [])
        buf.append({"ts": ts, "id": sender_id, "name": sender_name, "text": entry_text})
        if len(buf) > self.buf_per_session:
            dropped = buf.pop(0)
            if umo not in self._truncated:
                buf.insert(
                    0,
                    {
                        "ts": dropped["ts"],
                        "id": "?",
                        "name": "system",
                        "text": "(夜间消息过多，更早的没看到)",
                    },
                )
                self._truncated.add(umo)
        while sum(len(v) for v in self._buffers.values()) > self.buf_total:
            biggest = max(self._buffers, key=lambda k: len(self._buffers[k]))
            if self._buffers[biggest]:
                self._buffers[biggest].pop(0)
            else:
                break

    def _flush_morning_into(self, event: AstrMessageEvent, umo: str):
        """晨间冲刷：夜间消息本体拼进消息链（入历史→反思引擎可读），事实行走 extra 注入"""
        buf = self._buffers.pop(umo, [])
        info = self._morning_pending.pop(umo, None)
        if not buf:
            return
        lines = [
            self.tpl_body_header.format(
                msg_count=len(buf),
                sleep_time=info.get("sleep_time", "?") if info else "?",
                wake_time=info.get("wake_time", "?") if info else "?",
            )
        ]
        for e in buf:
            lines.append(f"[{_fmt_hm(e['ts'])} {e['name']}] {e['text']}")
        night_block = "\n".join(lines)

        try:
            if Plain is not None:
                event.message_obj.message.insert(0, Plain(night_block + "\n---\n"))
        except Exception:
            logger.warning("[睡觉] 消息链拼接失败，仅改 message_str")
        event.message_str = night_block + "\n---\n" + (event.message_str or "")
        if info:
            self._morning_pending[umo + "#fact"] = info
        logger.info(f"[睡觉] 晨间冲刷：{umo} 注入 {len(buf)} 条夜间消息")

    # ---------- extra 注入（事实行，mark_as_temp 不入历史） ----------

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req=None):
        try:
            if TextPart is None or req is None:
                return
            umo = event.unified_msg_origin
            info = self._morning_pending.pop(umo + "#fact", None)
            parts = []
            if info:
                parts.append(
                    self.tpl_morning_fact.format(
                        sleep_time=info["sleep_time"],
                        wake_time=info["wake_time"],
                        msg_count=info["msg_count"],
                    )
                )
            if (
                self._wake_at
                and self.day_fact_hours > 0
                and time.time() - self._wake_at < self.day_fact_hours * 3600
                and self._sleep_window
            ):
                bed_ts, wake_ts = self._sleep_window
                parts.append(
                    self.tpl_day_fact.format(
                        sleep_time=_fmt_hm(bed_ts),
                        wake_time=_fmt_hm(wake_ts),
                    )
                )
            for p in parts:
                req.extra_user_content_parts.append(TextPart(text=p).mark_as_temp())
        except Exception:
            logger.warning("[睡觉] 事实行注入失败（不影响消息处理）", exc_info=True)

    # ---------- 调试指令（仅管理员） ----------

    @staticmethod
    def _is_admin(event: AstrMessageEvent) -> bool:
        checker = getattr(event, "is_admin", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                return False
        return False

    def _night_line(self):
        n = self._night or {}
        bits = []
        if n.get("insomnia"):
            bits.append("失眠夜")
        if n.get("oversleep"):
            bits.append(f"赖床+{n['bonus_min']:.0f}min")
        if n.get("weekend_extra", 0) > 0:
            bits.append(f"周末+{n['weekend_extra']:.0f}min")
        if n.get("debt_extra", 0) > 0:
            bits.append(f"睡眠债+{n['debt_extra']:.0f}min")
        tag = ("（" + "，".join(bits) + "）") if bits else ""
        return (
            f"今日抽签：{_fmt_hm(n.get('bed_ts', time.time()))} 睡 → "
            f"{_fmt_hm(n.get('wake_ts', time.time()))} 醒，时长 {n.get('dur_min', 0):.0f} 分钟{tag}"
        )

    @filter.command("睡觉状态")
    async def cmd_status(self, event: AstrMessageEvent, args=None):
        """查看当前状态、今日作息抽签、缓冲条数"""
        if not self._is_admin(event):
            yield event.plain_result("没权限")
            return
        if self.phase == PHASE_SLEEPING:
            bed_ts, wake_ts = self._sleep_window or (0, 0)
            total = sum(len(v) for v in self._buffers.values())
            left = max(0, wake_ts - time.time()) // 60
            yield event.plain_result(
                f"状态：睡眠中（{_fmt_hm(bed_ts)} 入睡，预计 {_fmt_hm(wake_ts)} 醒，还有约{left}分钟）\n"
                f"{self._night_line() if self.schedule_random else ''}\n"
                f"夜间缓冲：{total} 条（{len(self._buffers)} 个会话）"
            )
        else:
            sched = self._night_line() if self.schedule_random else (
                f"固定作息：{self.bed_min // 60:02d}:{self.bed_min % 60:02d} 睡 → "
                f"{self.wake_min // 60:02d}:{self.wake_min % 60:02d} 醒"
            )
            yield event.plain_result(f"状态：清醒\n{sched}")

    @filter.command("强制入睡")
    async def cmd_force_sleep(self, event: AstrMessageEvent, args=None):
        """立即入睡；可带 HH:MM 指定今日入睡时刻（起床按公式重导）"""
        if not self._is_admin(event):
            yield event.plain_result("没权限")
            return
        now_dt = datetime.now()
        async with self._lock:
            if args:
                m = _parse_hhmm(str(args).strip())
                if m is None:
                    yield event.plain_result("时间格式应为 HH:MM")
                    return
                if self.schedule_random:
                    rel = _rel_minutes(m, self.c_base)
                    rel = max(self.c_earliest, min(self.c_ins_late, rel))
                    self._roll_tonight(now_dt, force_bed_rel=rel, source="force")
                    note = f"（指定入睡 {_fmt_hm(self._night['bed_ts'])}，起床已按公式重导）"
                else:
                    bed_dt = now_dt.replace(hour=m // 60, minute=m % 60, second=0, microsecond=0)
                    wake = self._next_wake_after(now_dt)
                    await self._enter_sleep(bed_dt.timestamp(), wake.timestamp())
                    yield event.plain_result(f"已入睡（固定模式 {_fmt_hm(bed_dt.timestamp())} → {_fmt_hm(wake.timestamp())}）")
                    return
            else:
                if self.schedule_random:
                    self._ensure_tonight(now_dt)  # 保留今日已抽作息
                    note = "（沿用今日抽签）"
                else:
                    note = ""
            n = self._night
            await self._enter_sleep(n["bed_ts"], n["wake_ts"], note=note)
            self._save_state()
        yield event.plain_result(f"已入睡，{_fmt_hm(n['bed_ts'])} → {_fmt_hm(n['wake_ts'])}，守门挂起")

    @filter.command("立刻起床")
    async def cmd_force_wake(self, event: AstrMessageEvent, args=None):
        """立即起床（冲刷缓冲按会话在下一次对话时注入）"""
        if not self._is_admin(event):
            yield event.plain_result("没权限")
            return
        async with self._lock:
            if self.phase != PHASE_SLEEPING:
                yield event.plain_result("本来就醒着")
                return
            await self._wake()
        yield event.plain_result("起床了，晨间内容将在各会话下次对话时注入")

    @filter.command("强制午睡")
    async def cmd_force_nap(self, event: AstrMessageEvent, args=None):
        """立即睡一段短觉；参数分钟数，默认 20"""
        if not self._is_admin(event):
            yield event.plain_result("没权限")
            return
        try:
            minutes = int(str(args).strip()) if str(args).strip() else 20
        except ValueError:
            minutes = 20
        minutes = max(1, min(minutes, 180))
        now_dt = datetime.now()
        wake = now_dt + timedelta(minutes=minutes)
        async with self._lock:
            await self._enter_sleep(
                now_dt.timestamp(), wake.timestamp(), note=f"（午睡{minutes}分钟）"
            )
        yield event.plain_result(f"眯 {minutes} 分钟，到点自动醒")

    @filter.command("清空夜间缓冲")
    async def cmd_clear_buffer(self, event: AstrMessageEvent):
        """手动清空所有会话的睡眠缓冲队列"""
        if not self._is_admin(event):
            yield event.plain_result("没权限")
            return
        async with self._lock:
            n = sum(len(v) for v in self._buffers.values())
            self._buffers = {}
            self._truncated = set()
            self._morning_pending = {}
            self._save_state()
        yield event.plain_result(f"已清空 {n} 条夜间缓冲")

    @filter.command("重抛作息")
    async def cmd_reroll(self, event: AstrMessageEvent, args=None):
        """重新抽签今晚作息（可反复掷骰观察赖床/失眠分布）"""
        if not self._is_admin(event):
            yield event.plain_result("没权限")
            return
        if not self.schedule_random:
            yield event.plain_result("当前是固定作息模式，无随机作息可抛")
            return
        async with self._lock:
            rec = self._roll_tonight(datetime.now(), source="reroll")
            if self.phase == PHASE_SLEEPING:
                # 睡着中重抛：当前窗口同步替换为新抽签
                self._sleep_window = (rec["bed_ts"], rec["wake_ts"])
            self._save_state()
        yield event.plain_result(f"重新抽签：{self._night_line()}")

    @filter.command("强制失眠")
    async def cmd_insomnia(self, event: AstrMessageEvent, args=None):
        """把今晚钉成失眠夜（入睡=失眠上限，必赖床，验证链路）"""
        if not self._is_admin(event):
            yield event.plain_result("没权限")
            return
        if not self.schedule_random:
            yield event.plain_result("当前是固定作息模式，无失眠夜机制")
            return
        async with self._lock:
            rec = self._roll_tonight(
                datetime.now(), force_bed_rel=self.c_ins_late, force_insomnia=True, source="insomnia"
            )
            if self.phase == PHASE_SLEEPING:
                self._sleep_window = (rec["bed_ts"], rec["wake_ts"])
            self._save_state()
        yield event.plain_result(
            f"今晚钉成失眠夜：{_fmt_hm(rec['bed_ts'])} 睡 → {_fmt_hm(rec['wake_ts'])} 醒（必赖床）"
        )
