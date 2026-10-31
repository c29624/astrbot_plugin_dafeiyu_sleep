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

第 3 步（本版，设计文档第五节）：
- 叫醒频道（白天@必回的夜间版）：
  · 私聊：消息以叫醒前缀列表任一项开头（默认 醒醒啦/醒醒/!，清空=禁用；
    与 AstrBot 指令前缀冲突的条目剔除+WARNING）
  · 群聊：@她 + 叫醒词双条件缺一不可（默认 醒来/醒醒/起来啦/起床啦/别睡啦）
  · 权限开关仅管理员可叫醒（默认开）：私聊=叫醒白名单（默认=AstrBot管理员名单，皆空则整体禁用）；
    群聊=仅白名单/管理员；开关=0 时任何人都能叫醒。无权限=静默入队不解释
  · 叫醒即冲刷：来源会话积压随叫醒请求注入（她翻未读）；其他会话不动
  · 起床气注入：被叫醒状态模板走 extra parts（半梦半醒）；午睡被叫醒=直接结束转清醒、轻度迷糊无起床气
- 叫醒=彻底转醒（临时清醒/智能回睡是第4步；届时叫醒改走临时清醒流程）

v0.3.1：新增"梦话"——睡眠期被戳（私聊/群里@她/戳一戳）按概率回一句可配置的梦话
（插件直发零token，全局冷却防刷屏，围观闲聊不触发；戳一戳概率独立可调）

第 4 步（v0.4.0，设计文档第六节）：
- 临时清醒：夜间被叫醒不再彻底醒，进入"没醒透"状态——消息正常回复，
  每轮注入"还带着困意"状态行（解决被叫醒后语气迅速恢复正常的问题）
- 智能回睡（不做死板倒计时）：清醒满最短时长（默认40min）后开始观察，
  "对她说的话"全渠道合并冷场（默认5min）→ 回睡；兜底最长上限（默认90min）强制回睡；
  冷场只算"对她说的话"：私聊任何消息 / 群里@她 / 叫她应答昵称（前置否定与引用内容不算）
- 撞原定起床时刻（含睡眠债顺延后）→ 直接转彻底清醒，不再回睡
- 回睡告别语：只发叫醒来源会话；首选 LLM 临场生成（超时/失败回落语料池14句，
  傲娇/困倦/不耐烦/温柔四款可配置）；由回睡定时器主动发送（不依赖入站事件）
- 睡眠债结算：临时清醒 N 分钟 → 起床顺延 N×折算比例（默认50%），作用于当夜窗口
- 午睡被叫醒=直接结束转清醒（不进临时清醒，维持第3步行为）

后续步骤：⑤午睡补偿+没睡醒 ⑥内置指引+配置页打磨
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
    from astrbot.core.message.components import At, Plain
except ImportError:
    At, Plain = None, None

PLUGIN_NAME = "astrbot_plugin_dafeiyu_sleep"
PLUGIN_VERSION = "0.4.0"
STATE_FILE = "sleep_state.json"

PHASE_AWAKE = "awake"
PHASE_SLEEPING = "sleeping"
PHASE_TEMP_WAKE = "temp_wake"

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
    "oversleep_per_30": 15.0,       # % / 每个步长
    "oversleep_step_minutes": 30.0,  # 步长（分钟）
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
    "wake_prefixes": ["醒醒啦", "醒醒", "!"],
    "group_wake_words": ["醒来", "醒醒", "起来啦", "起床啦", "别睡啦"],
    "admin_only_wake": True,
    "wake_whitelist": [],
    "woken_fact_template": "[系统信息] 你被强行叫醒，现在半梦半醒。",
    "nap_woken_fact_template": "[系统信息] 你午睡被叫醒，才睡了一会儿，有点迷糊但没有起床气。",
    "sleep_talk_enabled": True,
    "sleep_talk_chance": 8.0,
    "sleep_talk_poke_chance": 25.0,
    "sleep_talk_cooldown_min": 10.0,
    "sleep_talk_phrases": [
        "Zzz…",
        "呼……zzz…",
        "唔……别吵……",
        "（翻了个身继续睡）",
        "呼噜……zzz……",
        "……唔嗯……",
        "（把被子蒙过头）zzz…",
        "……五分钟……再睡五分钟……",
    ],
    "temp_wake_min_minutes": 40.0,
    "temp_wake_max_minutes": 90.0,
    "cold_field_minutes": 5.0,
    "quick_mode": False,
    "quick_earliest_bed": "23:00",
    "quick_bed_range_min": 90.0,
    "quick_latest_wake": "09:30",
    "quick_oversleep_max_chance": 70.0,
    "quick_oversleep_max_min": 40.0,
    "nicknames": ["大肥鱼", "肥鱼", "小鲸鱼"],
    "temp_wake_fact_template": "[系统信息] 你刚被叫醒没多久，还没完全醒透，说话还带着困意。",
    "farewell_llm_enabled": True,
    "farewell_prompt_template": "你要继续睡了，按你平时的语气跟对方道个别。就一句话，短短的，别提任何系统或指令，别解释原因。",
    "farewell_pool": [
        "别吵本鱼了，再吵明天不理你",
        "行了行了，说完了没，鱼要回去睡了",
        "哼，要不是看你顺眼才不陪你到现在",
        "就知道使唤我，鱼也是有觉要睡的",
        "困死了……眼皮打架了，明天再聊",
        "呼……不行了，鱼要沉底睡了",
        "睡意上来了挡不住，晚安",
        "脑袋已经不清醒了，再说下去要说梦话了",
        "都几点了，睡！",
        "没事鱼就继续睡了，别@了",
        "鱼的觉也是觉，散了散了",
        "再聊下去要说过头话了，睡觉！",
        "早点睡吧，你也别熬了，晚安",
        "去睡去睡，明天见",
    ],
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


def _m2s(m):
    """分钟数（可>1440）→ 'HH:MM'（跨午夜自动取模）"""
    m = int(m)
    return f"{(m // 60) % 24:02d}:{m % 60:02d}"


def _rel_minutes(t_min, base_min):
    """跨午夜归一化：把时刻换算成"以基准入睡点为锚的相对分钟数"（±12h）。
    基准 23:00 时：22:15=-45、01:10=+130、02:30=+210。"""
    return (t_min - base_min + 720) % 1440 - 720


def _fmt_dt(ts):
    return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")


def _fmt_hm(ts):
    return datetime.fromtimestamp(ts).strftime("%H:%M")


class _SafeFormatDict(dict):
    """模板变量缺失时渲染为空（设计文档第七节承诺），不抛 KeyError"""

    def __missing__(self, key):
        return ""


def _render(tpl: str, **kw) -> str:
    try:
        return str(tpl).format_map(_SafeFormatDict(kw))
    except Exception:
        return str(tpl)


@register(PLUGIN_NAME, "29624", "大肥鱼的真实作息：到点真睡、早上真醒、记得夜里谁说了什么", PLUGIN_VERSION)
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
        self._is_nap = False  # 当前睡眠窗口是否午睡（叫醒演出区分用）
        self._last_sleep_talk = 0.0  # 上次说梦话的时刻（全局冷却）
        self._temp_wake = None  # 临时清醒 {start_ts, source_umo, last_activity}
        self._lock = asyncio.Lock()
        self._tick_task = None
        self._data_dir = None

    # ---------- 配置解析与校验（数值类：回落默认+WARNING；跨字段矛盾：双方回落） ----------

    def _warn(self, key, why):
        logger.warning(f"[睡觉] 配置项 {key} {why}，已回落默认值 {DEFAULTS.get(key)}")

    @staticmethod
    def _flatten(cfg):
        """schema 为 object 分组，配置值嵌套在各组 dict 里；合并为扁平视图（嵌套组优先，兼容旧扁平配置/测试）"""
        flat = {k: v for k, v in cfg.items() if not isinstance(v, dict)}
        for v in cfg.values():
            if isinstance(v, dict):
                flat.update(v)
        return flat

    def _apply_config(self, cfg):
        cfg = self._flatten(cfg)
        get = lambda k: cfg.get(k, DEFAULTS[k])

        # 简单配置：快速设定 5 旋钮派生作息参数（quick_mode 默认关，老配置零影响）。
        # 派生值只覆盖映射表内字段，时长/波动/周末幅度/失眠概率等仍读详细配置。
        derived = {}
        if bool(get("quick_mode")) and bool(get("random_schedule")):
            qe = _parse_hhmm(get("quick_earliest_bed"))
            qw = _parse_hhmm(get("quick_latest_wake"))
            try:
                qrng = float(get("quick_bed_range_min"))
                qc = float(get("quick_oversleep_max_chance"))
                qm = float(get("quick_oversleep_max_min"))
            except Exception:
                qrng = qc = qm = -1.0
            if qe is None or qw is None or not (15 <= qrng <= 360) or not (10 <= qc <= 100) or not (10 <= qm <= 180):
                logger.warning("[睡觉] 快速设定参数非法，简单配置不生效，回落详细配置")
            else:
                qw_abs = qw + 1440 if qw <= qe else qw  # 起床在入睡次日的绝对分钟轴上
                latest = qe + int(qrng)
                base = qe + int(qrng * 0.55)  # 稍偏夜猫的入睡中心
                ins_late = latest + 60
                thr = latest - 30
                try:
                    hours = max(4.0, min(12.0, float(get("sleep_hours_base"))))
                except Exception:
                    hours = 7.5
                wfloor = min(qe + int(hours * 60) - 30, qw_abs - 60)
                step = max(15.0, min(45.0, round(qrng / 4 / 5) * 5))
                os_base = qc * 0.5
                per = (qc - os_base) / max(1.0, (latest - thr) / step)
                derived = {
                    "bedtime_base": _m2s(base),
                    "bedtime_earliest": _m2s(qe),
                    "bedtime_latest": _m2s(latest),
                    "insomnia_latest": _m2s(ins_late),
                    "oversleep_threshold": _m2s(thr),
                    "wake_earliest": _m2s(wfloor),
                    "wake_hardcap": _m2s(qw),
                    "oversleep_base": os_base,
                    "oversleep_step_minutes": step,
                    "oversleep_per_30": per,
                    "oversleep_bonus_min": round(qm * 0.25, 1),
                    "oversleep_bonus_max": qm,
                }
                logger.info(
                    f"[睡觉] 简单配置生效：入睡窗 {_m2s(qe)}~{_m2s(latest)}（基准 {_m2s(base)}）、"
                    f"起床 {_m2s(wfloor)}~{_m2s(qw)}、失眠至 {_m2s(ins_late)}、赖床阈值 {_m2s(thr)}"
                )
                _get_user = get
                get = lambda k: derived.get(k, _get_user(k))

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
        self.c_os_step = num("oversleep_step_minutes", 1, 240)
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

        # ---- 叫醒频道配置（设计文档第五节） ----
        self.tpl_woken = str(cfg.get("woken_fact_template", DEFAULTS["woken_fact_template"]))
        self.tpl_nap_woken = str(
            cfg.get("nap_woken_fact_template", DEFAULTS["nap_woken_fact_template"])
        )

        raw_pfx = cfg.get("wake_prefixes", DEFAULTS["wake_prefixes"])
        if not isinstance(raw_pfx, list):
            raw_pfx = DEFAULTS["wake_prefixes"]
        pfx = []
        for item in raw_pfx:
            s = str(item).strip()
            if not s:
                continue
            if s in self.cmd_prefixes:  # 与指令前缀冲突：剔除该条并警告，其余不受影响
                logger.warning(f"[睡觉] 叫醒前缀 '{s}' 与指令前缀冲突，已剔除")
                continue
            pfx.append(s)
        self.c_wake_prefixes = tuple(dict.fromkeys(pfx))  # 空=私聊叫醒禁用

        raw_words = cfg.get("group_wake_words", DEFAULTS["group_wake_words"])
        if not isinstance(raw_words, list):
            raw_words = DEFAULTS["group_wake_words"]
        self.c_group_wake_words = tuple(
            dict.fromkeys(str(w).strip() for w in raw_words if str(w).strip())
        )

        self.c_admin_only_wake = bool(cfg.get("admin_only_wake", DEFAULTS["admin_only_wake"]))
        raw_wl = cfg.get("wake_whitelist", [])
        if not isinstance(raw_wl, list):
            raw_wl = []
        wake_ids = [str(x).strip() for x in raw_wl if str(x).strip()]
        if not wake_ids:  # 默认=AstrBot管理员名单（主人QQ天然在内）
            try:
                wake_ids = [str(a) for a in self.context.get_config().get("admins_id", [])]
            except Exception:
                wake_ids = []
        if self.c_admin_only_wake and not wake_ids:
            logger.warning("[睡觉] 叫醒白名单与管理员名单均为空，叫醒频道整体禁用")
        self._wake_ids = set(wake_ids)

        # ---- 梦话（睡眠期被戳时偶尔冒一句，插件直发零token） ----
        self.c_sleep_talk_on = bool(cfg.get("sleep_talk_enabled", DEFAULTS["sleep_talk_enabled"]))
        self.c_sleep_talk_chance = num("sleep_talk_chance", 0, 100)
        self.c_sleep_talk_poke_chance = num("sleep_talk_poke_chance", 0, 100)
        self.c_sleep_talk_cd = num("sleep_talk_cooldown_min", 0, 1440)
        raw_ph = cfg.get("sleep_talk_phrases", DEFAULTS["sleep_talk_phrases"])
        if not isinstance(raw_ph, list):
            raw_ph = DEFAULTS["sleep_talk_phrases"]
        self.c_sleep_talk_phrases = tuple(
            dict.fromkeys(str(s).strip() for s in raw_ph if str(s).strip())
        )

        # ---- 第4步：临时清醒 / 智能回睡 / 告别语 ----
        self.c_tw_min = num("temp_wake_min_minutes", 1, 480)
        self.c_tw_max = num("temp_wake_max_minutes", 1, 720)
        self.c_cold = num("cold_field_minutes", 1, 120)
        if self.c_tw_min > self.c_tw_max:
            logger.warning(
                f"[睡觉] 配置矛盾：最短清醒({self.c_tw_min}) > 最长上限({self.c_tw_max})，双方回落默认"
            )
            self.c_tw_min, self.c_tw_max = DEFAULTS["temp_wake_min_minutes"], DEFAULTS["temp_wake_max_minutes"]
        raw_nick = cfg.get("nicknames", DEFAULTS["nicknames"])
        if not isinstance(raw_nick, list):
            raw_nick = DEFAULTS["nicknames"]
        self.c_nicknames = tuple(dict.fromkeys(str(n).strip() for n in raw_nick if str(n).strip()))
        self.tpl_temp_wake_fact = str(
            cfg.get("temp_wake_fact_template", DEFAULTS["temp_wake_fact_template"])
        )
        self.c_farewell_llm = bool(cfg.get("farewell_llm_enabled", DEFAULTS["farewell_llm_enabled"]))
        self.tpl_farewell_prompt = str(
            cfg.get("farewell_prompt_template", DEFAULTS["farewell_prompt_template"])
        )
        raw_fw = cfg.get("farewell_pool", DEFAULTS["farewell_pool"])
        if not isinstance(raw_fw, list):
            raw_fw = DEFAULTS["farewell_pool"]
        self.c_farewell_pool = tuple(dict.fromkeys(str(s).strip() for s in raw_fw if str(s).strip()))

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
        line = f"[睡觉] v{PLUGIN_VERSION} 已启动：状态={self.phase}"
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
            self._is_nap = bool(data.get("is_nap", False))
            self._temp_wake = data.get("temp_wake")
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
                "is_nap": self._is_nap,
                "temp_wake": self._temp_wake,
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
                self._wake_at = now  # 白天事实行6小时窗口起点
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
        """某夜的基准入睡时刻。基准在凌晨（00:00~11:59，如 00:00）时进位到锚日
        的次日——"10-29 晚上的那觉"的 00:00 是 10-30 00:00，不是 10-29 00:00。"""
        base = self.c_base
        day = anchor_date + timedelta(days=1) if base < 12 * 60 else anchor_date
        return datetime.combine(day, dtime(base // 60, base % 60))

    def _derive_wake(self, anchor_dt, bed_dt, insomnia):
        """最终起床 = clamp(入睡 + 抽样时长 + 周末加成 + 睡眠债 + 赖床加成, 最早起床, 硬顶)"""
        dur = random.gauss(self.c_hours_base * 60, self.c_hours_sigma * 60)
        dur = max(self.c_hours_base * 60 - 2 * self.c_hours_sigma * 60,
                  min(self.c_hours_base * 60 + 2 * self.c_hours_sigma * 60, dur))
        # 周末判定按起床日：基准+12h 的日期=醒来的那个上午（晚基准23:00→次日、凌晨基准00:00→当天）
        wake_day = (anchor_dt + timedelta(hours=12)).date()
        weekend = wake_day.weekday() >= 5
        wk_extra = random.uniform(self.c_wk_min, self.c_wk_max) if weekend else 0.0

        # 赖床掷骰
        offset = (bed_dt - anchor_dt).total_seconds() / 60.0  # 相对基准的偏移分钟
        if insomnia:
            hit = True  # 失眠必赖床
        elif offset > self.c_thr:
            p = min(
                100.0,
                self.c_os_base + self.c_os_per30 * int((offset - self.c_thr) // self.c_os_step),
            )
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
        # 起床边界在"基准+12h 的那个上午"（与 _derive_wake 的周末判定同口径）
        wake_day = (base_dt + timedelta(hours=12)).date()
        lo = datetime.combine(wake_day, self.c_wake_lo)
        hi = datetime.combine(wake_day, self.c_wake_hi)
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
        """滚动抽签规则：「这夜还没过完」= now < 原定起床时刻——即使已跨午夜
        （入睡可以晚到 01:10/02:30），只要夜没结束签就有效，绝不作废重抽；
        只有原定起床已过且还没抽新一天才滚动。睡眠中/临时清醒一律不动。"""
        if self.phase != PHASE_AWAKE:
            return None
        n = self._night
        if n is None:
            return self._roll_tonight(now_dt)
        if now_dt.timestamp() < n.get("wake_ts", 0):
            return n  # 跨午夜但夜未结束（00:00~入睡点待睡/补时）——签仍有效
        today = now_dt.date().isoformat()
        if n.get("anchor") != today:
            rec = self._roll_tonight(now_dt)
            logger.info(
                f"[睡觉] 跨日滚动：新一天抽签 {_fmt_hm(rec['bed_ts'])} 睡 → {_fmt_hm(rec['wake_ts'])} 醒"
            )
            return rec
        return n

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
            if self._night:
                self._night["wake_ts"] = new_wake  # 显示层同步（anchor 可能已跨日，不苛求）
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
                    if self._is_nap:
                        self._end_nap(now)  # 午睡自然醒：只恢复清醒，不动今晚作息
                    else:
                        await self._wake()
            elif self.phase == PHASE_TEMP_WAKE:
                tw = self._temp_wake or {}
                start = tw.get("start_ts", now)
                if self._sleep_window and now >= self._sleep_window[1]:
                    await self._wake()  # 撞原定起床（含债顺延）→ 彻底醒，不回睡
                elif now >= start + self.c_tw_max * 60:
                    await self._back_to_sleep("到清醒上限")
                elif (
                    now >= start + self.c_tw_min * 60
                    and now - tw.get("last_activity", start) >= self.c_cold * 60
                ):
                    await self._back_to_sleep("冷场")
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

    async def _enter_sleep(self, bed_ts, wake_ts, note="", nap=False):
        self.phase = PHASE_SLEEPING
        self._sleep_window = (bed_ts, wake_ts)
        self._is_nap = nap
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

    def _end_nap(self, now=None, note="自然醒"):
        """午睡结束：只恢复清醒，不归档、不重抽今晚作息；清掉窗口防白天事实行误报"""
        now = now or time.time()
        self._set_morning_pending(now)
        self.phase = PHASE_AWAKE
        self._wake_at = now
        self._is_nap = False
        self._sleep_window = None
        self._save_state()
        logger.info(f"[睡觉] 午睡{note}，恢复清醒（今晚作息不动）")

    async def _wake(self):
        actual = time.time()
        self._temp_wake = None
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
            if self.phase in (PHASE_AWAKE, PHASE_TEMP_WAKE):
                if umo in self._morning_pending:
                    self._flush_morning_into(event, umo)
                if self.phase == PHASE_TEMP_WAKE and self._counts_as_activity(
                    event, umo, text
                ):
                    tw = self._temp_wake or {}
                    tw["last_activity"] = time.time()  # 全渠道合并冷场计时重置
                    self._temp_wake = tw
                return
            # 睡眠期：先查叫醒频道（白天@必回的夜间版），命中→转醒并放行本条
            wake_kind = self._check_wake(event, umo, text)
            if wake_kind:
                await self._wake_by_channel(event, umo, wake_kind)
                return  # 不 stop_event：叫醒消息正常走管线，她半梦半醒地回
            self._buffer_message(event, umo, text)
        event.stop_event()
        self._save_state()
        await self._maybe_sleep_talk(event, umo)

    def _buffer_message(self, event: AstrMessageEvent, umo: str, text: str):
        entry_text = text
        if not entry_text:
            placeholder = ""
            try:
                for comp in event.get_messages():
                    cls = type(comp).__name__
                    if cls == "Poke":
                        placeholder = "[戳一戳]"
                        break
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
        # 上限：单会话超限丢最旧的真实消息；截断标记常驻队头（不会被后续挤出丢掉）
        while len(buf) > self.buf_per_session:
            if buf[0].get("name") == "system":
                marker = buf.pop(0)
                buf.pop(0)
                buf.insert(0, marker)
            else:
                buf.pop(0)
                if umo not in self._truncated:
                    buf.insert(
                        0,
                        {
                            "ts": ts,
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

    # ---------- 叫醒频道（设计文档第五节：白天@必回的夜间版） ----------

    def _at_bot(self, event: AstrMessageEvent) -> bool:
        if At is None:
            return False
        try:
            self_id = str(event.get_self_id())
            for comp in event.get_messages():
                if isinstance(comp, At) and str(getattr(comp, "qq", "")) == self_id:
                    return True
        except Exception:
            pass
        return False

    def _wake_allowed(self, event: AstrMessageEvent) -> bool:
        if not self.c_admin_only_wake:
            return True  # 放养模式：任何人都能叫醒
        try:
            sid = str(event.get_sender_id() or "")
        except Exception:
            return False
        return sid in self._wake_ids

    def _check_wake(self, event: AstrMessageEvent, umo: str, text: str):
        """返回 None=不构成叫醒（照常缓冲）；'night'=夜间叫醒；'nap'=午睡叫醒"""
        if not text:
            return None
        kind = "nap" if self._is_nap else "night"
        if ":FriendMessage:" in umo:
            if not self.c_wake_prefixes:
                return None  # 前缀列表清空=私聊叫醒禁用
            if not any(text.startswith(p) for p in self.c_wake_prefixes):
                return None
            return kind if self._wake_allowed(event) else None  # 无权限=静默入队
        # 群聊：@她 + 叫醒词双条件缺一不可
        if not any(w in text for w in self.c_group_wake_words):
            return None
        if not self._at_bot(event):
            return None
        return kind if self._wake_allowed(event) else None

    async def _wake_by_channel(self, event: AstrMessageEvent, umo: str, wake_kind: str):
        """被叫醒：夜间→临时清醒（第4步，回睡/撞起床由定时器接管）；午睡→直接转清醒"""
        now = time.time()
        window = self._sleep_window  # _end_nap 会清窗口，先存给兜底用
        self._set_morning_pending(now)  # 全部有积压的会话登记晨间事实（各随其下次请求注入）
        if wake_kind == "nap":
            self._end_nap(now, note="被叫醒")
        else:
            self.phase = PHASE_TEMP_WAKE
            self._temp_wake = {"start_ts": now, "source_umo": umo, "last_activity": now}
        # 叫醒即冲刷：来源会话积压随本条消息注入（她翻未读）
        self._flush_morning_into(event, umo)
        info = self._morning_pending.get(umo + "#fact")
        if info is None:  # 来源会话无积压（这是睡眠期第一条）——仍给作息事实+状态行
            bed_ts, wake_ts = window or (now, now)
            info = {"sleep_time": _fmt_hm(bed_ts), "wake_time": _fmt_hm(now), "msg_count": 0}
        info["woken"] = wake_kind
        self._morning_pending[umo + "#fact"] = info
        self._save_state()
        logger.info(
            f"[睡觉] 被叫醒（{'午睡→转清醒' if wake_kind == 'nap' else '夜间→临时清醒'}，来源 {umo}），"
            f"叫醒即冲刷完成"
        )

    # ---------- 临时清醒 → 智能回睡 → 告别语（设计文档第六节） ----------

    async def _back_to_sleep(self, reason: str):
        """回睡：结算睡眠债（清醒N分钟→起床顺延N×比例），守门重挂，告别语只发叫醒来源会话"""
        now = time.time()
        tw = self._temp_wake or {}
        awake_min = max(0.0, (now - tw.get("start_ts", now)) / 60.0)
        src = tw.get("source_umo")
        farewell = await self._gen_farewell()
        self._temp_wake = None
        self.phase = PHASE_SLEEPING
        self._is_nap = False
        await self._apply_debt(awake_min)  # 内含落盘；窗口顺延不越入睡+12h
        logger.info(
            f"[睡觉] 回睡（{reason}，清醒{awake_min:.0f}分钟，睡眠债累计{self._debt_minutes:.0f}分钟，"
            f"起床顺延至{_fmt_hm(self._sleep_window[1]) if self._sleep_window else '?'}）"
        )
        if src and now - tw.get("last_activity", 0) <= 1800:
            # 距离最后活动太久（如重启后补结算）就不发告别语了，深夜空群冒泡很怪
            try:
                await self.context.send_message(src, MessageChain().message(farewell))
                logger.info(f"[睡觉] 回睡告别语已发 {src}：{farewell}")
            except Exception:
                logger.warning("[睡觉] 告别语发送失败（不影响回睡）", exc_info=True)

    async def _gen_farewell(self) -> str:
        """告别语：首选 LLM 临场生成（带现用人设，"按你的人设跟群里道别"），
        失败/超时/关闭回落语料池"""
        if self.c_farewell_llm:
            try:
                try:
                    provider = await self.context.get_using_provider_async(None)
                except Exception:
                    provider = self.context.get_using_provider()
                if provider is not None:
                    persona_text = ""
                    try:
                        persona = await self.context.persona_manager.get_default_persona_v3(
                            None
                        )
                        if isinstance(persona, dict):
                            persona_text = str(persona.get("prompt") or persona.get("system_prompt") or "")
                        elif persona is not None:
                            persona_text = str(
                                getattr(persona, "prompt", "") or getattr(persona, "system_prompt", "")
                            )
                    except Exception:
                        persona_text = ""
                    prompt = (
                        (persona_text[:1500] + "\n\n") if persona_text else ""
                    ) + self.tpl_farewell_prompt
                    resp = await asyncio.wait_for(
                        provider.text_chat(prompt=prompt, session_id="dafeiyu_sleep_farewell"),
                        timeout=25,
                    )
                    text = str(getattr(resp, "completion_text", "") or "").strip()
                    if text:
                        return text[:100]
            except Exception:
                logger.warning("[睡觉] 告别语LLM生成失败，回落语料池", exc_info=True)
        if self.c_farewell_pool:
            return random.choice(self.c_farewell_pool)
        return "睡了，明天见"

    def _counts_as_activity(self, event: AstrMessageEvent, umo: str, text: str) -> bool:
        """冷场判定：只算"对她说的话"——私聊任何消息 / 群里@她 / 叫她应答昵称"""
        if ":FriendMessage:" in umo:
            return True
        if self._at_bot(event):
            return True
        return self._nickname_called(text, event)

    def _nickname_called(self, text: str, event: AstrMessageEvent) -> bool:
        """应答昵称包含匹配 + 两防误判：前置否定不算、引用内容不算"""
        if not text or not self.c_nicknames:
            return False
        try:
            if any(type(c).__name__ == "Reply" for c in event.get_messages()):
                return False  # 引用/回复里的昵称是转述，不算叫她
        except Exception:
            pass
        for nick in self.c_nicknames:
            i = text.find(nick)
            while i != -1:
                # 前两个字内出现否定词不算（文档例："今天不吃肥鱼了"——不隔着动词）
                window = text[max(0, i - 2) : i]
                if not any(c in "不没别非莫" for c in window):
                    return True
                i = text.find(nick, i + 1)
        return False

    def _is_poke(self, event: AstrMessageEvent) -> bool:
        try:
            return any(type(c).__name__ == "Poke" for c in event.get_messages())
        except Exception:
            return False

    async def _maybe_sleep_talk(self, event: AstrMessageEvent, umo: str):
        """梦话：睡眠期被戳（私聊/群里@她/戳一戳）按概率直发一句可配置梦话。

        零 token（不走模型）；全局冷却防刷屏；围观闲聊不触发（只回应戳她的人）；
        戳一戳是明确的"戳"动作，概率独立配置（默认高于普通消息）。
        """
        if not self.c_sleep_talk_on or not self.c_sleep_talk_phrases:
            return
        now = time.time()
        if now - self._last_sleep_talk < self.c_sleep_talk_cd * 60:
            return
        is_poke = self._is_poke(event)
        if not is_poke and ":FriendMessage:" not in umo and not self._at_bot(event):
            return  # 群里只被@才冒梦话，路人闲聊不理
        chance = self.c_sleep_talk_poke_chance if is_poke else self.c_sleep_talk_chance
        if random.random() * 100 >= chance:
            return
        self._last_sleep_talk = now
        line = random.choice(self.c_sleep_talk_phrases)
        try:
            await self.context.send_message(umo, MessageChain().message(line))
            logger.info(f"[睡觉] 梦话：{umo} <- {line}")
        except Exception:
            logger.warning("[睡觉] 梦话发送失败（不影响缓冲）", exc_info=True)

    def _flush_morning_into(self, event: AstrMessageEvent, umo: str):
        """晨间冲刷：夜间消息本体拼进消息链（入历史→反思引擎可读），事实行走 extra 注入"""
        buf = self._buffers.pop(umo, [])
        info = self._morning_pending.pop(umo, None)
        if not buf:
            return
        lines = [
            _render(
                self.tpl_body_header,
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
                    _render(
                        self.tpl_morning_fact,
                        sleep_time=info["sleep_time"],
                        wake_time=info["wake_time"],
                        msg_count=info["msg_count"],
                    )
                )
                # 被叫醒状态行（午睡版轻度迷糊、夜间版半梦半醒）
                if info.get("woken") == "nap":
                    parts.append(self.tpl_nap_woken)
                elif info.get("woken"):
                    parts.append(self.tpl_woken)
            # 临时清醒期间每轮带"还没醒透"状态行（持续困意）
            if self.phase == PHASE_TEMP_WAKE:
                parts.append(self.tpl_temp_wake_fact)
            if (
                self._wake_at
                and self.day_fact_hours > 0
                and time.time() - self._wake_at < self.day_fact_hours * 3600
                and self._sleep_window
            ):
                bed_ts, wake_ts = self._sleep_window
                parts.append(
                    _render(
                        self.tpl_day_fact,
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

    def _wake_line(self):
        who = "仅白名单/管理员" if self.c_admin_only_wake else "任何人"
        return (
            f"叫醒频道：私聊前缀 {'/'.join(self.c_wake_prefixes) or '（禁用）'}；"
            f"群@+词 {'/'.join(self.c_group_wake_words) or '（禁用）'}｜{who}"
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
                f"夜间缓冲：{total} 条（{len(self._buffers)} 个会话）\n{self._wake_line()}"
            )
        elif self.phase == PHASE_TEMP_WAKE:
            tw = self._temp_wake or {}
            now = time.time()
            been = (now - tw.get("start_ts", now)) / 60
            idle = (now - tw.get("last_activity", now)) / 60
            yield event.plain_result(
                f"状态：临时清醒（被叫醒{been:.0f}分钟，最短{self.c_tw_min:.0f}/上限{self.c_tw_max:.0f}分钟，"
                f"冷场{idle:.0f}/{self.c_cold:.0f}分钟）\n"
                f"清醒满最短且冷场到点 → 回睡；撞原定起床时刻 → 彻底醒\n{self._wake_line()}"
            )
        else:
            sched = self._night_line() if self.schedule_random else (
                f"固定作息：{self.bed_min // 60:02d}:{self.bed_min % 60:02d} 睡 → "
                f"{self.wake_min // 60:02d}:{self.wake_min % 60:02d} 醒"
            )
            yield event.plain_result(f"状态：清醒\n{sched}\n{self._wake_line()}")

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
            if self.phase == PHASE_TEMP_WAKE:
                await self._wake()  # 临时清醒中手动起床=彻底醒（夜间归档+抽明晚）
                yield event.plain_result("彻底醒了，晨间内容将在各会话下次对话时注入")
                return
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
                now_dt.timestamp(), wake.timestamp(), note=f"（午睡{minutes}分钟）", nap=True
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
