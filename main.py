"""大肥鱼·随机睡觉插件 v0.7.0（六步全部完成 + 安全闸门）

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

第 5 步（v0.5.0）：午睡补偿+没睡醒——失眠/熬夜（入睡晚于赖床阈值）资格，次日 13:00~15:00
随机时刻按概率眯 15~30 分钟（守门/缓冲/叫醒全同夜间）；没睡醒状态行白天持续注入，
抽中午睡蔫到午睡结束、没抽中蔫到 17:00；收口抖动（撞地板+0~10min、顶硬顶−5~0min）消整点感

第 6 步（v0.6.0）：休息日体系+内置指引——休息制度 1单休/2双休/3大小周 + 法定节假日跟随
（chinese-calendar，缺包降级纯星期）+ 补充工作/休息名单，按"醒来那天"判夜；休息日独立作息窗
（留空=工作日窗+30~60分钟随机熬夜偏移）；固定模式上班族抖动（入睡0~20/起床0~15分钟）；
/作息分布 模拟指令（100晚直方图）；表演指引随晨间冲刷注入（人格无关五条）

第 7 步（v0.7.0）：安全闸门——LLM 闸门堵"/必回后门"（/前缀未命中指令的文本被框架剥前缀
当唤醒消息 100% 必回、绕过读空气概率：非管理员静默，真指令/链接解析不受影响）；管理员暗门
睡眠期放行并注入梦话状态行；夜间裸链接放行给解析器（LLM 有闸门兜底）；自回显不进夜间缓冲；
午睡豁免两旋钮（休息日免午睡 / 睡够 X 小时免午睡）
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

try:  # 法定节假日数据（缺包/年底过期自动降级纯星期判定）
    import chinese_calendar as _cc
except Exception:
    _cc = None

try:
    from astrbot.api.star import StarTools
except ImportError:  # 旧版无 StarTools
    StarTools = None

try:
    from astrbot.core.agent.message import TextPart
except ImportError:  # 版本变动时的兜底（fail-open：注入失败不拦消息）
    TextPart = None

try:
    from astrbot.core.message.components import At, Json, Plain
except ImportError:
    At, Json, Plain = None, None, None

PLUGIN_NAME = "astrbot_plugin_dafeiyu_sleep"
PLUGIN_VERSION = "0.8.4"
STATE_FILE = "sleep_state.json"

PHASE_AWAKE = "awake"
PHASE_SLEEPING = "sleeping"
PHASE_TEMP_WAKE = "temp_wake"

# ---- v0.8.0 陪聊熬夜常量（代码级，不暴露配置——懒人三开关之外全走默认）----
GRACE_ACT_WIN_MIN = 10     # 到点"正在聊"判定：最近 10 分钟内有私聊来言
GRACE_COLD_LO, GRACE_COLD_HI = 5.0, 8.0   # 冷场窗口 U(5,8) 每夜随机
GRACE_STEP_CHOICES = (30, 45, 60)          # 困意爬坡升档间隔（分钟，每夜随机抽）
GRACE_HARD_CAP_MIN = 240   # 4 小时硬顶（账本亏空按阶梯收缩）
GRACE_LADDER = ((180, 240), (360, 90), (600, 30))  # 7天亏空(min)→当晚陪聊上限(min)；>600=不陪
DEFICIT_FAST_MIN = 180     # 亏空≥3h → 爬坡加速（起点+1档、间隔减半）
STAYUP_TIER_MIN = (45, 60, 240)  # 次日事实行分级：≥45轻 ≥60很困 ≥240通宵
NAP_RELIEVE_RATIO = 0.4    # 午睡缓解账本亏空的比例
NAP_RELIEVE_CAP = 240      # 单次午睡最多抵的分钟数
EARLY_BED_CAP = 90         # 次日早睡前移上限（分钟）
SILENT_TIER_MAP = {"轻": 1, "中": 2, "重": 3, "从不": 99}  # v0.8.1 无声溜走从几档起可能（默认中）
DROWSY_SAY_MAX = 3  # v0.8.3 困类状态行每日最多注入的请求数——状态说一次靠模板措辞、注入最多3次防"每条都喊困"（模型无已说过记忆，mark_as_temp不进历史）

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
    "night_fact_from": "20:00",  # 夜间事实行起点（off/关闭=不注入）
    "night_fact_template": (
        "[系统信息] 今晚你计划 {bed_time} 左右才睡——现在离入睡还早，"
        "别说\"这就去睡/我也要睡了\"；这个时刻自己知道就行，别把具体几点告诉对方，"
        "被问或要回应告别就说今晚睡得晚、让对方先睡；"
        "这句告别话一晚说一次就够，之后再遇到告别，短短一句收住就行，别重复叮嘱。"
    ),
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
    "nap_enabled": True,
    "nap_chance": 40.0,
    "nap_skip_rest": False,
    "nap_enough_hours": 0.0,
    "nap_window_start": "13:00",
    "nap_window_end": "15:00",
    "nap_duration_min": 15.0,
    "nap_duration_max": 30.0,
    "drowsy_fact_template": "[系统信息] 你昨晚熬夜了，今天没睡醒。这个状态开场提过一次就够了，之后让它体现在句子短、反应慢、懒得动上，别每条消息都喊困。",
    # ---- v0.8.0 陪聊熬夜（懒人三开关 + 困意三档 + 次日分级事实）----
    "sleep_grace_on": True,
    "sleep_stayup_cost_on": True,
    "sleep_sneak_ratio": 75.0,  # % 溜走时留一句嘟囔的概率（达到无声档位后剩余情况无声）
    "sleep_sneak_silent_tier": "中",  # 无声溜走从几档起可能（轻/中/重/从不）
    "grace_drowsy_1_fact": (
        "[系统信息] 你开始有点困了——眼皮有点沉，反应慢了半拍，但还能聊。"
        "可以顺口提一句有点困，也可以不提；还不到告别的时候，自然聊。"
    ),
    "grace_drowsy_2_fact": (
        "[系统信息] 你很困了——哈欠一个接一个，思路偶尔打结，话变短，"
        "有时话说到一半会忘词、打错字。嘴上可能会冒出\"好想睡觉\"\"特别想睡\"，"
        "也可能带点小情绪（怪自己怎么还不困、嫌TA还没说完），但还在撑着陪。"
    ),
    "grace_drowsy_3_fact": (
        "[系统信息] 你困得不行了——意识一阵一阵地断，句子碎成几个字，"
        "打字开始出错、有时只剩省略号，眼皮完全抬不起来。之前嘴硬说不困的，"
        "这时候也扛不住了，会小声承认\"特别特别想睡\"。人已经快撑不住，"
        "随时可能聊着聊着就睡着了。"
    ),
    "stayup_light_fact": "[系统信息] 你昨晚比平时晚睡了{x}分钟，今天有点困倦——不明显但状态一般，提过一次就不用再说，让它体现在语气上。",
    "stayup_heavy_fact": "[系统信息] 你昨晚熬了{x}小时才睡，今天很困——比平常困得明显，容易走神、话变短。开场提过一次就收，之后困意全走短句和慢半拍，别重复喊困。",
    "stayup_allnight_fact": (
        "[系统信息] 你昨晚通宵了——整整撑了{x}小时才去睡{is_rest}。"
        "今天是硬撑的状态：反应慢、话少、随时想眯，别演得精神抖擞。"
        "提过一次就收，硬撑感靠短句和慢半拍体现，别每条都说困。"
    ),
    "grace_sneak_template": (
        "你困得不行了，对方还没回消息，你决定不等了、偷偷溜去睡。"
        "给TA留一句嘟囔——很短，像半梦半醒随手打出来的，别提具体时间，"
        "也别正式道别（你不是在告别，是撑不住先走了）。就一句。"
    ),
    "grace_hardcap_template": (
        "你已经熬到极限了，意识都在断片，对方还在说话，但你真的撑不住必须去睡了。"
        "断断续续留最后一两句——字可能都打不全，别提具体时间。"
    ),
    "grace_bye_pool": [
        "唔…到点了，我先睡啦",
        "困了，先去睡了",
        "撑不住了，睡了睡了",
        "眼皮打架了，明天再聊",
    ],
    "silent_slip_fact_template": (
        "[系统信息] 你昨晚是聊着聊着直接睡着的——没来得及跟对方道别就断片了。"
        "现在想起来，该自然地提一句（比如昨晚聊着聊着就睡着了、对方后面说的你早上才看到），"
        "提一次就好，别过度道歉。"
    ),
    "rest_independent": False,
    "rest_schedule_type": 2,
    "rest_single_weekday": "周日",
    "rest_alt_parity": "双周休周六",
    "rest_holiday_follow": True,
    "rest_extra_work_dates": [],
    "rest_extra_rest_dates": [],
    "rest_bed_earliest": "",
    "rest_bed_latest": "",
    "rest_wake_earliest": "",
    "rest_wake_hardcap": "",
    "fixed_bed_jitter_max": 20.0,
    "fixed_wake_jitter_max": 15.0,
    "perf_guide_template": (
        "[系统信息-表演指引] 关于你刚睡醒，按你自己的性格和语气自然处理，以下几点供参考："
        "不必逐条回应夜里收到的消息，像正常人翻手机，只挑有意思的接；"
        "不必主动汇报作息，被问起才说，十次里提两三次就好；"
        "如果夜里有人深夜倾诉情绪，优先关心那条；"
        "没睡醒的日子说话更蔫、句子更短、容易走神；"
        "午睡醒来被问就说刚才眯着了，不用系统式解释。"
    ),
    "nicknames": ["大肥鱼", "肥鱼", "小鲸鱼"],
    "slash_gate": True,
    "slash_gate_admin_pass": True,
    "night_link_pass": True,
    "night_whisper_template": (
        "[系统信息] 你正在熟睡，被这条消息半梦半醒地牵动，迷迷糊糊回了句——"
        "回复要短、含糊、带着睡意，别清醒，别提系统。"
    ),
    "temp_wake_fact_template": "[系统信息] 你刚被叫醒没多久，还没完全醒透，说话还带着困意。困意开场提过一次就收，之后靠有气无力的短句体现，别每条都说困。",
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


@register(PLUGIN_NAME, "c29624", "大肥鱼的真实作息：到点真睡、早上真醒、记得夜里谁说了什么", PLUGIN_VERSION)
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
        self._morning_flushed = {}  # {"date": "YYYY-MM-DD", "umos": [...]} 晨间冲刷已发生标记（主动私聊插件据此让位晨起，v0.7.2）
        self._wake_at = None
        self._night = None  # 今日/今夜抽签记录（dict，见 _roll_tonight）
        self._last_night = None  # 上一夜完成记录（失眠标记留给第5步）
        self._debt_minutes = 0.0  # 睡眠债（第4步临时清醒累计，第2步先建字段）
        self._is_nap = False  # 当前睡眠窗口是否午睡（叫醒演出区分用）
        self._last_sleep_talk = 0.0  # 上次说梦话的时刻（全局冷却）
        self._temp_wake = None  # 临时清醒 {start_ts, source_umo, last_activity}
        self._nap_plan = None  # 午睡补偿计划 {at_ts, end_ts}（熬夜次日的午后彩蛋）
        self._drowsy_until = 0.0  # "没睡醒"状态行注入截止时刻
        self._fixed_jit = None  # 固定模式当日抖动 {day, bed, wake}（防重抽）
        self._cc_dead_years = set()  # chinese-calendar 不覆盖的年份（降级标记）
        self._lock = asyncio.Lock()
        self._tick_task = None
        self._data_dir = None
        # ---- v0.8.0 陪聊熬夜 ----
        self._grace = None  # 宽限中 {bed_ts, wake_ts, cold_gap, last_act, hard_cap, step_min, level, start_level, last_umo}
        self._stayup_ledger = []  # 滚动账本 [{date, stayup_min}]（负数=午睡抵扣），7 天窗口
        self._next_early_bed = 0.0  # 今晚早睡前移量（分钟，昨晚熬夜×0.5 钳 90）
        self._stayup_tier = 0  # 昨晚熬夜分级 0/1/2/3（次日事实行档）
        self._stayup_h = 0.0  # 昨晚熬夜小时数（渲染 {x} 用）
        self._last_dm_act = {}  # umo -> 私聊来言最后时刻（"到点正在聊"判定）
        self._silent_slip = {}  # v0.8.1 无声溜走标记 {"date","umos"}（次日晨间补一句的依据）
        self._drowsy_said = {"date": "", "n": 0}  # v0.8.3 困类状态行当日已注入次数（节流）

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
        # 夜间事实行（防穿帮：离入睡还早时她聊天里别演"这就去睡"）
        raw_nff = str(cfg.get("night_fact_from", "20:00")).strip()
        if raw_nff.lower() in ("off", "关", "关闭"):
            self.night_fact_from = None  # 显式关
        else:
            self.night_fact_from = _parse_hhmm(raw_nff)
            if self.night_fact_from is None:
                self.night_fact_from = 20 * 60  # 非法回落默认（数值类铁律）
        self.tpl_night_fact = str(cfg.get("night_fact_template", DEFAULTS["night_fact_template"]))
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
        self.tpl_drowsy = str(
            cfg.get("drowsy_fact_template", DEFAULTS["drowsy_fact_template"])
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

        # ---- 午睡补偿 + 没睡醒（第5步：熬夜次日的午后彩蛋） ----
        self.c_nap_on = bool(get("nap_enabled"))
        self.c_nap_chance = num("nap_chance", 0, 100)
        self.c_nap_skip_rest = bool(get("nap_skip_rest"))
        self.c_nap_enough = num("nap_enough_hours", 0, 12)
        nv_lo = _parse_hhmm(get("nap_window_start"))
        nv_hi = _parse_hhmm(get("nap_window_end"))
        if nv_lo is None or nv_hi is None or nv_lo >= nv_hi:
            logger.warning("[睡觉] 配置矛盾：午睡窗口起止非法或次序颠倒，回落默认 13:00~15:00")
            nv_lo = _parse_hhmm(DEFAULTS["nap_window_start"])
            nv_hi = _parse_hhmm(DEFAULTS["nap_window_end"])
        self.c_nap_lo = dtime(nv_lo // 60, nv_lo % 60)
        self.c_nap_hi = dtime(nv_hi // 60, nv_hi % 60)
        self.c_nap_dur_min = num("nap_duration_min", 5, 120)
        self.c_nap_dur_max = num("nap_duration_max", 5, 180)
        if self.c_nap_dur_min > self.c_nap_dur_max:
            logger.warning("[睡觉] 配置矛盾：午睡时长下限 > 上限，双方回落默认")
            self.c_nap_dur_min = DEFAULTS["nap_duration_min"]
            self.c_nap_dur_max = DEFAULTS["nap_duration_max"]

        # ---- v0.8.0 陪聊熬夜（懒人三开关） ----
        self.c_grace_on = bool(get("sleep_grace_on"))
        self.c_stayup_cost = bool(get("sleep_stayup_cost_on"))
        self.c_sneak_ratio = num("sleep_sneak_ratio", 0, 100)
        _st = str(get("sleep_sneak_silent_tier") or DEFAULTS["sleep_sneak_silent_tier"]).strip()
        if _st not in SILENT_TIER_MAP:
            self._warn("sleep_sneak_silent_tier", f"须为 {'/'.join(SILENT_TIER_MAP)}")
            _st = DEFAULTS["sleep_sneak_silent_tier"]
        self.c_silent_tier = _st
        self.tpl_silent_slip = str(get("silent_slip_fact_template"))
        self.tpl_drowsy_1 = str(get("grace_drowsy_1_fact"))
        self.tpl_drowsy_2 = str(get("grace_drowsy_2_fact"))
        self.tpl_drowsy_3 = str(get("grace_drowsy_3_fact"))
        self.tpl_stayup_light = str(get("stayup_light_fact"))
        self.tpl_stayup_heavy = str(get("stayup_heavy_fact"))
        self.tpl_stayup_allnight = str(get("stayup_allnight_fact"))
        self.tpl_sneak = str(get("grace_sneak_template"))
        self.tpl_hardcap = str(get("grace_hardcap_template"))
        _gp = get("grace_bye_pool")
        self.grace_bye_pool = [str(x).strip() for x in (_gp or []) if str(x).strip()] \
            or list(DEFAULTS["grace_bye_pool"])

        # ---- 休息日体系（第6步：周末≠休息日，大小周/单休/调休） ----
        self.rest_independent = bool(get("rest_independent"))
        self.c_rest_type = num("rest_schedule_type", 1, 3, int)
        self.c_single_wd = 5 if "六" in str(get("rest_single_weekday")) else 6
        self.c_alt_even_rest = "双" in str(get("rest_alt_parity"))
        self.c_holiday_follow = bool(get("rest_holiday_follow"))

        def _date_set(v):
            if not isinstance(v, list):
                return set()
            out = set()
            for x in v:
                s = str(x).strip()
                try:
                    datetime.strptime(s, "%Y-%m-%d")
                    out.add(s)
                except ValueError:
                    continue
            return out

        self._rest_work_dates = _date_set(get("rest_extra_work_dates"))
        self._rest_dates = _date_set(get("rest_extra_rest_dates"))

        rb, rl = _parse_hhmm(get("rest_bed_earliest")), _parse_hhmm(get("rest_bed_latest"))
        if rb is None or rl is None or rb >= rl:
            if rb is not None or rl is not None:
                logger.warning("[睡觉] 休息日入睡窗非法或次序颠倒，回落自动（工作日窗+熬夜偏移）")
            self.c_rest_bed = None
        else:
            self.c_rest_bed = (rb, rl)
        rwl, rwh = _parse_hhmm(get("rest_wake_earliest")), _parse_hhmm(get("rest_wake_hardcap"))
        if rwl is None or rwh is None or rwl >= rwh:
            if rwl is not None or rwh is not None:
                logger.warning("[睡觉] 休息日起床窗非法或次序颠倒，回落自动（工作日窗+赖床加成）")
            self.c_rest_wake = None
        else:
            self.c_rest_wake = (rwl, rwh)

        # 固定模式 2.0：上班族式双端小抖动
        self.c_fix_bed_jit = num("fixed_bed_jitter_max", 0, 60)
        self.c_fix_wake_jit = num("fixed_wake_jitter_max", 0, 60)
        self.tpl_perf_guide = str(cfg.get("perf_guide_template", DEFAULTS["perf_guide_template"]))
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
        # ---- 闸门（v0.7.0：/非指令必回后门 + 睡眠期 LLM 兜底）----
        self.c_gate_on = bool(cfg.get("slash_gate", DEFAULTS["slash_gate"]))
        self.c_gate_admin_pass = bool(
            cfg.get("slash_gate_admin_pass", DEFAULTS["slash_gate_admin_pass"])
        )
        self.c_night_link_pass = bool(cfg.get("night_link_pass", DEFAULTS["night_link_pass"]))
        self.tpl_night_whisper = str(
            cfg.get("night_whisper_template", DEFAULTS["night_whisper_template"])
        )

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
            self._nap_plan = data.get("nap_plan")
            self._drowsy_until = float(data.get("drowsy_until", 0) or 0)
            self._fixed_jit = data.get("fixed_jit")
            self._morning_flushed = data.get("morning_flushed") or {}
            self._grace = data.get("grace")
            self._stayup_ledger = data.get("stayup_ledger") or []
            self._next_early_bed = float(data.get("next_early_bed", 0) or 0)
            self._stayup_tier = int(data.get("stayup_tier", 0) or 0)
            self._stayup_h = float(data.get("stayup_h", 0) or 0)
            self._silent_slip = data.get("silent_slip") or {}
            _ds = data.get("drowsy_said") or {}
            self._drowsy_said = _ds if isinstance(_ds, dict) else {"date": "", "n": 0}
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
                "nap_plan": self._nap_plan,
                "drowsy_until": self._drowsy_until,
                "fixed_jit": self._fixed_jit,
                "morning_flushed": self._morning_flushed,
                "grace": self._grace,
                "stayup_ledger": self._stayup_ledger,
                "next_early_bed": self._next_early_bed,
                "stayup_tier": self._stayup_tier,
                "stayup_h": self._stayup_h,
                "silent_slip": self._silent_slip,
                "drowsy_said": self._drowsy_said,
                "drowsy_level": self._drowsy_level_now(),
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

    def _anchor_base_dt(self, anchor_date, base_min=None):
        """某夜的基准入睡时刻。基准在凌晨（00:00~11:59，如 00:00）时进位到锚日
        的次日——"10-29 晚上的那觉"的 00:00 是 10-30 00:00，不是 10-29 00:00。"""
        base = int(self.c_base if base_min is None else base_min)
        day = anchor_date + timedelta(days=1) if base < 12 * 60 else anchor_date
        return datetime.combine(day, dtime(base // 60, base % 60))

    def _holiday_rest(self, d):
        """chinese-calendar 判定（含调休补班）；包缺失/年份过期返回 None 走常规判定"""
        if _cc is None:
            return None
        if d.year in self._cc_dead_years:
            return None
        try:
            return not _cc.is_workday(d)
        except Exception:
            self._cc_dead_years.add(d.year)
            logger.warning(f"[睡觉] chinese-calendar 不覆盖 {d.year} 年（包过期？），节假日判定降级纯星期规则")
            return None

    def _is_rest_day(self, d):
        """五层短路：补工作日 > 补休息日 > 法定节假日 > 调休补班 > 常规周"""
        iso = d.isoformat()
        if iso in self._rest_work_dates:
            return False
        if iso in self._rest_dates:
            return True
        if self.c_holiday_follow:
            r = self._holiday_rest(d)
            if r is not None:
                return r
        wd = d.weekday()
        if self.c_rest_type == 1:
            return wd == self.c_single_wd
        if self.c_rest_type == 3:
            if wd == 6:
                return True
            if wd == 5:
                return (d.isocalendar()[1] % 2 == 0) == self.c_alt_even_rest
            return False
        return wd >= 5

    def _rest_night(self, wake_day):
        """这夜的醒来日是否休息日（独立作息开启且随机模式才分叉）"""
        return self.rest_independent and self.schedule_random and self._is_rest_day(wake_day)

    def _derive_wake(self, anchor_dt, bed_dt, insomnia, thr=None, rest=False):
        """最终起床 = clamp(入睡 + 抽样时长 + 休息日加成 + 睡眠债 + 赖床加成, 最早起床, 硬顶)"""
        thr = self.c_thr if thr is None else thr
        dur = random.gauss(self.c_hours_base * 60, self.c_hours_sigma * 60)
        dur = max(self.c_hours_base * 60 - 2 * self.c_hours_sigma * 60,
                  min(self.c_hours_base * 60 + 2 * self.c_hours_sigma * 60, dur))
        # 休息日判定按起床日：基准+12h 的日期=醒来的那个上午（晚基准23:00→次日、凌晨基准00:00→当天）
        wake_day = (anchor_dt + timedelta(hours=12)).date()
        wk_extra = random.uniform(self.c_wk_min, self.c_wk_max) if rest else 0.0

        # 赖床掷骰
        offset = (bed_dt - anchor_dt).total_seconds() / 60.0  # 相对基准的偏移分钟
        if insomnia:
            hit = True  # 失眠必赖床
        elif offset > thr:
            p = min(
                100.0,
                self.c_os_base + self.c_os_per30 * int((offset - thr) // self.c_os_step),
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
            "late_bed": offset > thr,  # 午睡补偿资格：入睡晚于赖床阈值
            "bonus_min": round(bonus, 1),
            "debt_extra": round(debt_extra, 1),
        }

    def _roll_tonight(self, now_dt=None, force_bed_rel=None, force_insomnia=None, source="auto"):
        """抽今晚的作息。force_* 仅供调试指令拨表，走同一套公式。"""
        now_dt = now_dt or datetime.now()
        anchor_date = now_dt.date()

        # 参数组选择（第6步休息日体系）：独立开启且醒来日为休息日 → 休息日参数组。
        # 留空窗=自动：入睡窗整体 +30~60 随机熬夜偏移；填了窗=独立窗（基准=最早+窗宽×0.55、阈值=窗末−30）
        base_min = self.c_base
        ear, lat, ins_l, thr = self.c_earliest, self.c_latest, self.c_ins_late, self.c_thr
        bed_sigma = self.c_bed_sigma
        rest = self._rest_night((self._anchor_base_dt(anchor_date) + timedelta(hours=12)).date())
        if rest:
            if self.c_rest_bed:
                rb, rl = self.c_rest_bed
                base_min = (rb + int((rl - rb) * 0.55)) % 1440
                ear, lat = _rel_minutes(rb, base_min), _rel_minutes(rl, base_min)
                ins_l, thr = lat + 60, lat - 30
                bed_sigma = max(15.0, (lat - ear) / 4.0)
            else:
                shift = random.uniform(30, 60)
                base_min = (self.c_base + shift) % 1440
                ear, lat = self.c_earliest + shift, self.c_latest + shift
                ins_l, thr = self.c_ins_late + shift, self.c_thr + shift
        base_dt = self._anchor_base_dt(anchor_date, base_min)

        if force_insomnia is not None:
            insomnia = bool(force_insomnia)
        else:
            insomnia = random.random() * 100 < self.c_insomnia_chance
        if force_bed_rel is not None:
            off = float(force_bed_rel)
        elif insomnia:
            off = random.uniform(lat, ins_l)  # 失眠：常规最晚~失眠上限
        else:
            off = random.gauss(0, bed_sigma)
            off = max(ear, min(lat, off))

        bed_dt = base_dt + timedelta(minutes=off)
        rec = self._derive_wake(base_dt, bed_dt, insomnia, thr=thr, rest=rest)
        # 最终起床 = clamp(入睡 + 抽样时长 + 休息日加成 + 睡眠债 + 赖床加成, 最早起床, 硬顶)
        # 起床边界在"基准+12h 的那个上午"（与 _derive_wake 的休息日判定同口径）；休息日独立窗优先
        wake_day = (base_dt + timedelta(hours=12)).date()
        if rest and self.c_rest_wake:
            wl, wh = self.c_rest_wake
            lo = datetime.combine(wake_day, dtime(wl // 60, wl % 60))
            hi = datetime.combine(wake_day, dtime(wh // 60, wh % 60))
        else:
            lo = datetime.combine(wake_day, self.c_wake_lo)
            hi = datetime.combine(wake_day, self.c_wake_hi)
        wake_dt = bed_dt + timedelta(
            minutes=rec["dur_min"] + rec["weekend_extra"] + rec["bonus_min"] + rec["debt_extra"]
        )
        natural = wake_dt
        wake_dt = min(max(wake_dt, lo), hi)
        # 收口抖动（消整点机械感）：撞最早起床地板 +0~10 分钟、顶最晚起床硬顶 −5~0 分钟，
        # 抖完再夹一次保证"不早于地板、不晚于硬顶"的承诺不被极窄窗破坏
        if wake_dt > natural:
            wake_dt += timedelta(minutes=random.uniform(0, 10))
        elif wake_dt < natural:
            wake_dt += timedelta(minutes=random.uniform(-5, 0))
        wake_dt = min(max(wake_dt, lo), hi)
        rec.update(
            anchor=anchor_date.isoformat(),
            bed_ts=bed_dt.timestamp(),
            wake_ts=min(max(wake_dt, lo), hi).timestamp(),
            insomnia=insomnia,
            rest=rest,
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

    def _fixed_offsets(self, day):
        """固定模式 2.0：当日入睡/起床小抖动（上班族式毛边），按日落盘防重抽"""
        rec = self._fixed_jit or {}
        if rec.get("day") == day.isoformat():
            return rec.get("bed", 0.0), rec.get("wake", 0.0)
        rec = {
            "day": day.isoformat(),
            "bed": random.uniform(0, self.c_fix_bed_jit),
            "wake": random.uniform(0, self.c_fix_wake_jit),
        }
        self._fixed_jit = rec
        self._save_state()
        return rec["bed"], rec["wake"]

    def _natural_window(self, now=None):
        now = now or datetime.now()
        bed = now.replace(hour=self.bed_min // 60, minute=self.bed_min % 60, second=0, microsecond=0)
        bo, wo = self._fixed_offsets(bed.date())
        bed += timedelta(minutes=bo)
        wake = bed + timedelta(minutes=self._span_minutes() + wo)
        if bed <= now < wake:
            return bed, wake
        if now >= wake:
            bed += timedelta(days=1)
            bo, wo = self._fixed_offsets(bed.date())
            bed += timedelta(minutes=bo)
            wake = bed + timedelta(minutes=self._span_minutes() + wo)
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
                # v0.8.0 宽限中：冷场/硬顶由宽限自己的钟接管，常规入睡判定暂停
                if self._grace is not None:
                    await self._grace_tick(now)
                elif self.schedule_random:
                    self._ensure_tonight(now_dt)
                    n = self._night
                    if n and n["bed_ts"] <= now:
                        await self._bed_reached(now, n["bed_ts"], n["wake_ts"],
                                                note=f"（今日抽签 {_fmt_hm(n['bed_ts'])}）")
                else:
                    bed, wake = self._natural_window(now_dt)
                    if bed.timestamp() <= now < wake.timestamp():
                        await self._bed_reached(now, bed.timestamp(), wake.timestamp())
                if self._nap_plan and self.phase == PHASE_AWAKE and self._grace is None:
                    if now >= self._nap_plan["end_ts"] + 600:  # 窗口彻底错过（重载/强制状态）→ 作废
                        self._nap_plan = None
                        self._save_state()
                    elif now >= self._nap_plan["at_ts"]:
                        plan, self._nap_plan = self._nap_plan, None
                        await self._enter_sleep(
                            plan["at_ts"], plan["end_ts"], note="（午睡补偿）", nap=True
                        )

    # ---------- v0.8.0 陪聊熬夜（宽限/困意爬坡/熬夜账本） ----------

    def _prune_ledger(self):
        """熬夜账本只留最近 7 天（负数条目=午睡抵扣，随窗口一起淡出）"""
        cut = (datetime.now().date() - timedelta(days=7)).isoformat()
        self._stayup_ledger = [e for e in (self._stayup_ledger or [])
                               if str(e.get("date", "")) > cut]

    def _ledger_deficit(self) -> float:
        self._prune_ledger()
        return sum(float(e.get("stayup_min") or 0) for e in self._stayup_ledger)

    def _grace_allowance_min(self) -> float:
        """7 天亏空 → 今晚陪聊上限阶梯：<3h→4h / 3~6h→90m / 6~10h→30m / ≥10h→不陪"""
        d = self._ledger_deficit()
        for thr, cap in GRACE_LADDER:
            if d < thr:
                return float(cap)
        return 0.0

    @staticmethod
    def _stayup_tier_of(stayup_min: float) -> int:
        if stayup_min >= STAYUP_TIER_MIN[2]:
            return 3
        if stayup_min >= STAYUP_TIER_MIN[1]:
            return 2
        if stayup_min >= STAYUP_TIER_MIN[0]:
            return 1
        return 0

    def _last_grace_umo(self, now: float):
        """最近 30 分钟内最后说话的私聊会话（溜走嘟囔发给谁）"""
        best, bt = None, 0.0
        for umo, ts in (self._last_dm_act or {}).items():
            if float(ts) > bt:
                best, bt = umo, float(ts)
        if best and now - bt <= 30 * 60:
            return best, bt
        return None, 0.0

    def _drowsy_say_gate(self) -> bool:
        """v0.8.3 困类状态行节流：当日第 1~DROWSY_SAY_MAX 次请求放行，之后不再注入——
        状态由对话历史自续（她演过一次，模型看得到），防止每条都喊困"""
        today = datetime.now().date().isoformat()
        d = self._drowsy_said
        if d.get("date") != today:
            d = {"date": today, "n": 0}
            self._drowsy_said = d
        if int(d.get("n") or 0) >= DROWSY_SAY_MAX:
            return False
        d["n"] = int(d.get("n") or 0) + 1
        return True

    def _drowsy_level_now(self) -> int:
        """当前困倦档 0~3（state 落盘供主动私聊读取：宽限=爬坡档；次日=熬夜档钳2）"""
        if self._grace is not None:
            return self._grace_level(time.time())
        if self.phase == PHASE_AWAKE and time.time() < self._drowsy_until:
            return min(2, self._stayup_tier) if self.c_stayup_cost else 0
        return 0

    def _grace_level(self, now: float) -> int:
        """困意爬坡档位：起点+经过时长÷升档间隔；后段必到重档（硬顶前25%≥2档、
        最后30分钟=3档）——保证溜走/截断时她一定困得说不出整句"""
        g = self._grace or {}
        bed_ts = float(g.get("bed_ts") or now)
        hard_cap = float(g.get("hard_cap") or now + GRACE_HARD_CAP_MIN * 60)
        step = max(10.0, float(g.get("step_min") or 45.0))
        start_level = max(1, int(g.get("start_level") or 1))
        lv = start_level + int((now - bed_ts) / (step * 60))
        lv = max(1, min(3, lv))
        if hard_cap - now <= 30 * 60:
            lv = 3
        elif hard_cap - now <= (hard_cap - bed_ts) * 0.25:
            lv = max(lv, 2)
        return lv

    async def _bed_reached(self, now: float, bed_ts: float, wake_ts: float, note: str = ""):
        """到点入睡的三岔口：没在聊→直接睡（v0.7 原样）；在聊+宽限开→进陪聊宽限；
        在聊+宽限关/亏空到顶→不陪但好好道个别（一句，语料池直出零 token）"""
        chatting = any(now - float(ts) <= GRACE_ACT_WIN_MIN * 60
                       for ts in (self._last_dm_act or {}).values())
        if not chatting:
            await self._enter_sleep(bed_ts, wake_ts, note=note)
            return
        allow = self._grace_allowance_min() if self.c_grace_on else 0.0
        if allow <= 0:
            umo, _ = self._last_grace_umo(now)
            if umo:
                try:
                    await self.context.send_message(
                        umo, MessageChain().message(random.choice(self.grace_bye_pool)))
                    logger.info(f"[睡觉] 到点在聊但宽限关/额度用尽：道别后入睡（{umo}）")
                except Exception:
                    logger.warning("[睡觉] 道别发送失败（不影响入睡）", exc_info=True)
            await self._enter_sleep(bed_ts, wake_ts, note="（宽限关，道别即睡）")
            return
        await self._enter_grace(now, bed_ts, wake_ts, allow)

    async def _enter_grace(self, now: float, bed_ts: float, wake_ts: float, allow_min: float):
        last_umo, last_act = self._last_grace_umo(now)
        step = float(random.choice(GRACE_STEP_CHOICES))
        start_level = 2 if (self._last_night or {}).get("insomnia") else 1  # 失眠夜中档起
        if self._ledger_deficit() >= DEFICIT_FAST_MIN:  # 亏空≥3h→爬坡加速
            start_level = min(3, start_level + 1)
            step = step / 2.0
        self._grace = {
            "bed_ts": bed_ts, "wake_ts": wake_ts,
            "cold_gap": round(random.uniform(GRACE_COLD_LO, GRACE_COLD_HI), 1),
            "last_act": last_act or now, "last_umo": last_umo,
            "hard_cap": now + allow_min * 60,
            "step_min": step, "start_level": start_level, "allow_min": allow_min,
        }
        self._save_state()
        logger.info(
            f"[睡觉] 到点还在聊→陪聊宽限（冷场 {self._grace['cold_gap']} 分钟溜走，"
            f"陪聊上限 {allow_min:.0f} 分钟至 {_fmt_hm(self._grace['hard_cap'])}，"
            f"困意爬坡 {step:.0f} 分钟/档从 {start_level} 档起）"
        )

    async def _grace_tick(self, now: float):
        """宽限中的两条出路：冷场到点偷偷溜走（嘟囔比例掷）/ 硬顶截断（必留一句）"""
        g = self._grace
        if not g:
            return
        hard_cap = float(g.get("hard_cap") or 0)
        if hard_cap and now >= hard_cap:
            mutter = await self._gen_mutter(self.tpl_hardcap)
            await self._grace_sleep(now, "硬顶截断", mutter=mutter)
            return
        cold = float(g.get("cold_gap") or 6.0) * 60
        if now - float(g.get("last_act") or now) >= cold:
            # v0.8.1 档位门控：困到设定档位（默认中）才可能无声——轻档必打招呼；
            # 达标后按嘟囔比例掷，无声时记标记（次日晨间补一句"聊着聊着睡着"）
            lv = self._grace_level(now)
            silent_ok = lv >= SILENT_TIER_MAP.get(self.c_silent_tier, 2)
            mutter = None
            if (not silent_ok) or random.random() * 100 < self.c_sneak_ratio:
                mutter = await self._gen_mutter(self.tpl_sneak)
            await self._grace_sleep(now, "冷场溜走", mutter=mutter)

    async def _grace_sleep(self, now: float, why: str, mutter: str = None):
        """宽限收尾：熬夜入账本→次日后果数据面→嘟囔→入睡（起床按债顺延）"""
        g = self._grace or {}
        bed_ts = float(g.get("bed_ts") or now)
        stayup_min = max(0.0, (now - bed_ts) / 60.0)
        self._stayup_ledger.append({"date": datetime.now().date().isoformat(),
                                    "stayup_min": round(stayup_min, 1)})
        self._prune_ledger()
        # 次日后果数据面（代价开关关=当场演出保留、次日全免；账本照记随窗口淡出）
        self._stayup_tier = self._stayup_tier_of(stayup_min)
        self._stayup_h = round(stayup_min / 60.0, 1)
        self._next_early_bed = round(min(stayup_min * 0.5, EARLY_BED_CAP), 1)
        # v0.8.1 无声溜走标记：冷场无声走人才记（硬顶必留一句不算）——次日晨间补话依据
        if mutter is None and why == "冷场溜走" and g.get("last_umo"):
            today = datetime.now().date().isoformat()
            ss = self._silent_slip
            if not isinstance(ss, dict) or ss.get("date") != today:
                ss = {"date": today, "umos": []}
            if g["last_umo"] not in ss["umos"]:
                ss["umos"].append(g["last_umo"])
            self._silent_slip = ss
        if mutter:
            umo = g.get("last_umo")
            if umo and now - float(g.get("last_act") or 0) <= 1800:
                try:
                    await self.context.send_message(umo, MessageChain().message(mutter))
                except Exception:
                    logger.warning("[睡觉] 溜走嘟囔发送失败（不影响入睡）", exc_info=True)
        self._grace = None
        wake_ts = float(g.get("wake_ts") or now + 8 * 3600)
        await self._enter_sleep(now, wake_ts, note="（陪聊宽限后入睡）")
        # 起床顺延走既有睡眠债通道（_apply_debt 内部再乘比例）；休息日补觉总量
        # （周末加成+债顺延）钳 180——先按比例折算再钳，反解传给 debt 的分钟数
        ratio = max(1.0, self.c_debt_ratio)
        debt_pass = stayup_min
        n = self._night or {}
        if n.get("rest"):
            wk = float(n.get("weekend_extra") or 0)
            max_extra = max(0.0, 180.0 - wk)
            debt_pass = min(stayup_min, max_extra * 100.0 / ratio)
        if debt_pass > 0:
            await self._apply_debt(debt_pass)
        logger.info(
            f"[睡觉] 宽限结束（{why}，熬夜 {stayup_min:.0f} 分钟入账，"
            f"7天亏空 {self._ledger_deficit():.0f} 分钟，明日早睡 {self._next_early_bed:.0f} 分钟，"
            f"熬夜档 {self._stayup_tier}）"
        )

    async def _gen_mutter(self, prompt_tpl: str) -> str:
        """嘟囔一句：LLM 临场（带人设）→ 失败/超时回落语料池"""
        try:
            try:
                provider = await self.context.get_using_provider_async(None)
            except Exception:
                provider = self.context.get_using_provider()
            if provider is not None:
                persona_text = ""
                try:
                    persona = await self.context.persona_manager.get_default_persona_v3(None)
                    if isinstance(persona, dict):
                        persona_text = str(persona.get("prompt") or persona.get("system_prompt") or "")
                    elif persona is not None:
                        persona_text = str(getattr(persona, "prompt", "")
                                           or getattr(persona, "system_prompt", ""))
                except Exception:
                    persona_text = ""
                prompt = (persona_text[:1200] + "\n\n" if persona_text else "") + prompt_tpl
                resp = await asyncio.wait_for(
                    provider.text_chat(prompt=prompt, session_id=f"{PLUGIN_NAME}_mutter"),
                    timeout=20,
                )
                text = str(getattr(resp, "completion_text", "") or "").strip()
                if text:
                    return text[:120]
        except Exception:
            pass
        return random.choice(self.grace_bye_pool)

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
        # v0.8.0 昨晚熬夜→今晚早睡（×0.5 已钳 90 分钟；只在随机模式下挪抽签结果）
        if self._next_early_bed > 0 and self._night:
            self._night["bed_ts"] = float(self._night["bed_ts"]) - self._next_early_bed * 60
            logger.info(f"[睡觉] 昨晚熬夜 {self._stayup_h:.1f}h → 今晚入睡提前 "
                        f"{self._next_early_bed:.0f} 分钟（{_fmt_hm(self._night['bed_ts'])}）")
            self._save_state()
        self._next_early_bed = 0.0
        self._decide_nap(actual_ts or time.time())

    def _decide_nap(self, wake_ts):
        """第5步午睡补偿：熬夜（失眠或入睡晚于阈值）次日 13:00~15:00 概率午睡；
        白天注入"没睡醒"状态行——抽中午睡则蔫到午睡结束，没抽中蔫到 17:00。
        v0.8.0：陪聊熬夜档≥1 也走这条线（代价开关管的就是这层）；亏空每 2h 午睡概率 +10%"""
        self._nap_plan = None
        self._drowsy_until = 0.0
        ln = self._last_night
        if not self.c_nap_on or not ln:
            return
        stayup_gate = self.c_stayup_cost and self._stayup_tier > 0
        if not stayup_gate and not (ln.get("insomnia") or ln.get("late_bed")):
            return
        # v0.7.0 补充豁免：休息日睡到饱（按那晚的 rest 判定）不补觉；实际睡眠够长也不补觉
        if self.c_nap_skip_rest and ln.get("rest"):
            return
        if self.c_nap_enough > 0 and (wake_ts - ln.get("bed_ts", wake_ts)) / 3600.0 >= self.c_nap_enough:
            return
        day = datetime.fromtimestamp(wake_ts).date()
        lo = datetime.combine(day, self.c_nap_lo).timestamp()
        hi = datetime.combine(day, self.c_nap_hi).timestamp()
        drowsy_end = datetime.combine(day, dtime(17, 0)).timestamp()
        # v0.8.0 亏空加成：每 2h 亏空 +10%（钳 +40%）
        bonus = min(40.0, self._ledger_deficit() // 120 * 10) if stayup_gate else 0.0
        if random.random() * 100 < self.c_nap_chance + bonus:
            at = random.uniform(max(lo, wake_ts), hi)
            dur = random.uniform(self.c_nap_dur_min, self.c_nap_dur_max) * 60
            self._nap_plan = {"at_ts": at, "end_ts": at + dur}
            drowsy_end = self._nap_plan["end_ts"]
            logger.info(
                f"[睡觉] 熬夜次日午睡计划：{_fmt_hm(at)} 眯 {dur / 60:.0f} 分钟（没睡醒状态生效至午睡结束）"
            )
        self._drowsy_until = drowsy_end

    def _end_nap(self, now=None, note="自然醒"):
        """午睡结束：只恢复清醒，不归档、不重抽今晚作息；清掉窗口防白天事实行误报"""
        now = now or time.time()
        self._set_morning_pending(now)
        self.phase = PHASE_AWAKE
        self._wake_at = now
        self._drowsy_until = 0.0  # 睡过了，困意解除
        # v0.8.0 午睡缓解：抵掉账本亏空的 40%（单次钳 240；账本记账与代价开关无关）
        deficit = max(0.0, self._ledger_deficit())
        if deficit > 0:
            relief = min(NAP_RELIEVE_CAP, deficit * NAP_RELIEVE_RATIO)
            self._stayup_ledger.append({"date": datetime.now().date().isoformat(),
                                        "stayup_min": -round(relief, 1)})
            self._prune_ledger()
            logger.info(f"[睡觉] 午睡缓解熬夜账本：抵扣 {relief:.0f} 分钟，剩 {self._ledger_deficit():.0f}")
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
        if self._is_self_echo(event):
            if self.phase == PHASE_SLEEPING:
                event.stop_event()  # 自回显（她夜里发出的工具产物）不进夜间缓冲
            return

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
                # v0.8.0 陪聊熬夜：私聊来言=在聊（宽限冷场重置 + "到点正在聊"判定源）
                if ":FriendMessage:" in umo:
                    now_ts = time.time()
                    self._last_dm_act[umo] = now_ts
                    if self._grace is not None:
                        self._grace["last_act"] = now_ts
                        self._grace["last_umo"] = umo
                return
            # 睡眠期：先查叫醒频道（白天@必回的夜间版），命中→转醒并放行本条
            wake_kind = self._check_wake(event, umo, text)
            if wake_kind:
                await self._wake_by_channel(event, umo, wake_kind)
                return  # 不 stop_event：叫醒消息正常走管线，她半梦半醒地回
            if self.c_night_link_pass and self._has_link(event, text):
                return  # 夜间裸链接放行给解析器等工具直回（LLM 有闸门兜底，不进对话）
            self._buffer_message(event, umo, text)
        event.stop_event()
        self._save_state()
        await self._maybe_sleep_talk(event, umo)

    def _is_self_echo(self, event: AstrMessageEvent) -> bool:
        """她自己的出站消息回显（NapCat message_sent 等）：不算任何人发的消息"""
        try:
            return str(event.get_sender_id() or "") == str(event.get_self_id() or "")
        except Exception:
            return False

    def _has_link(self, event: AstrMessageEvent, text: str) -> bool:
        """消息里带链接（文本 URL 或 Json 分享卡片）"""
        if "http://" in text or "https://" in text or "www." in text:
            return True
        try:
            for comp in event.get_messages():
                if Json is not None and isinstance(comp, Json):
                    return True
        except Exception:
            pass
        return False

    def _has_command_handler(self, event: AstrMessageEvent) -> bool:
        """框架唤醒阶段是否有注册指令命中本条消息（拿不到注册表=fail-open 放行）"""
        try:
            from astrbot.core.star.filter.command import CommandFilter

            for h in event.get_extra("activated_handlers") or []:
                for f in getattr(h, "event_filters", None) or []:
                    if isinstance(f, CommandFilter):
                        return True
        except Exception:
            return True
        return False

    def _llm_gate(self, event: AstrMessageEvent):
        """LLM 闸门（on_llm_request 最前）：返回 ("pass"|"stop"|"whisper", 原因)。
        ① /前缀且没命中任何注册指令 = "必回后门"（框架剥前缀当唤醒、chat_plus 指令过滤跳过、
        默认 LLM 兜底 100% 回复）：非管理员拦；管理员可放行（默认，睡眠中附梦话状态行）。
        ② 睡眠期兜底：走到 LLM 这一步的非管理员调用（裸链接放行等豁免路径漏进来的）一律拦，
        保证睡着时除管理员暗门外绝不进对话。临时清醒/晨间是别的 phase，不受影响。
        """
        if not self.c_gate_on:
            return "pass", ""
        try:
            umo = event.unified_msg_origin
            if ":FriendMessage:" not in umo and ":GroupMessage:" not in umo:
                return "pass", ""  # WebChat 等平台是主人自己的控制台，不管
        except Exception:
            pass
        try:
            head = self._chain_head_text(event)
            slashy = bool(self.cmd_prefixes) and head.startswith(self.cmd_prefixes)
        except Exception:
            slashy = False
        admin = self._is_admin(event)
        has_cmd = self._has_command_handler(event)
        if slashy and not has_cmd:
            if admin and self.c_gate_admin_pass:
                if self.phase == PHASE_SLEEPING:
                    return "whisper", "管理员深夜暗门"
                return "pass", "管理员暗门"
            return "stop", f"/非指令必回后门 sender={event.get_sender_id()}"
        if (
            self.phase == PHASE_SLEEPING
            and not has_cmd
            and not (admin and self.c_gate_admin_pass)
        ):
            # 兜底只打默认对话路径：真指令发起的 LLM 调用（has_cmd）不拦，睡觉不影响干活
            return "stop", "睡眠期兜底"
        return "pass", ""

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

    def _silent_slip_part(self, umo: str, consume: bool = True) -> str:
        """v0.8.1 次日晨间补话：昨晚无声溜走（聊着聊着睡着）→ 返回事实行（一次性消耗）。
        跨一天未消耗的标记视为过期（他第二天都没来提，再提就尬了）"""
        ss = self._silent_slip or {}
        if not ss.get("umos") or umo not in ss["umos"]:
            return ""
        slip_day = str(ss.get("date") or "")
        today = datetime.now().date().isoformat()
        yesterday = (datetime.now().date() - timedelta(days=1)).isoformat()
        if slip_day not in (today, yesterday):
            self._silent_slip = {}
            return ""
        if consume:
            umos = [u for u in ss["umos"] if u != umo]
            self._silent_slip = {"date": ss.get("date"), "umos": umos} if umos else {}
            self._save_state()
        return self.tpl_silent_slip

    def _flush_morning_into(self, event: AstrMessageEvent, umo: str):
        """晨间冲刷：夜间消息本体拼进消息链（入历史→反思引擎可读），事实行走 extra 注入。
        v0.8.0 E：主动私聊已代答留言（标记文件在）→ 跳过冲刷表演（留言已被她的
        "刚醒看到留言"回应带出），缓冲弹出+记冲刷标记，不再注入防双演"""
        buf = self._buffers.pop(umo, [])
        info = self._morning_pending.pop(umo, None)
        if not buf:
            return
        try:
            mk = (self._data_dir / "dm_morning_reply.json") if self._data_dir else None
            if mk is not None and mk.exists():
                mdata = json.loads(mk.read_text(encoding="utf-8"))
                if (mdata.get("date") == datetime.now().date().isoformat()
                        and umo in (mdata.get("umos") or [])):
                    today = datetime.now().date().isoformat()
                    mf = self._morning_flushed
                    if not isinstance(mf, dict) or mf.get("date") != today:
                        mf = {"date": today, "umos": []}
                        self._morning_flushed = mf
                    if umo not in mf["umos"]:
                        mf["umos"].append(umo)
                    self._silent_slip_part(umo)  # dm 代答会提"聊着聊着睡着"，这里消耗防双提
                    self._save_state()
                    logger.info(f"[睡觉] 晨间冲刷跳过（主动私聊已代答留言）：{umo} {len(buf)} 条")
                    return
        except Exception:
            logger.warning("[睡觉] dm代答标记读取失败，按常规冲刷", exc_info=True)
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
        # 冲刷标记（v0.7.2）：主动私聊插件读这个让位晨起，不再在冲刷对话之后补发早安
        today = datetime.now().date().isoformat()
        mf = self._morning_flushed
        if not isinstance(mf, dict) or mf.get("date") != today:
            mf = {"date": today, "umos": []}
            self._morning_flushed = mf
        if umo not in mf["umos"]:
            mf["umos"].append(umo)
        self._save_state()

    # ---------- extra 注入（事实行，mark_as_temp 不入历史） ----------

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req=None):
        try:
            if TextPart is None or req is None:
                return
            action, why = self._llm_gate(event)
            if action == "stop":
                event.stop_event()  # 框架在调模型前中止本次 LLM 请求（官方支持的否决通道）
                logger.info(f"[睡觉] LLM 闸门拦截：{why}")
                return
            umo = event.unified_msg_origin
            info = self._morning_pending.pop(umo + "#fact", None)
            parts = []
            if action == "whisper":
                # 管理员深夜暗门：放行但带梦话状态行，演出成立
                parts.append(self.tpl_night_whisper)
            if info:
                parts.append(
                    _render(
                        self.tpl_morning_fact,
                        sleep_time=info["sleep_time"],
                        wake_time=info["wake_time"],
                        msg_count=info["msg_count"],
                    )
                )
                # v0.8.1 昨晚聊着聊着睡着（无声溜走）→ 晨间自然补一句
                slip = self._silent_slip_part(umo)
                if slip:
                    parts.append(slip)
                # 被叫醒状态行（午睡版轻度迷糊、夜间版半梦半醒）
                if info.get("woken") == "nap":
                    parts.append(self.tpl_nap_woken)
                elif info.get("woken"):
                    parts.append(self.tpl_woken)
                # 表演指引（人格无关，随晨间冲刷轮注入，不进历史）
                parts.append(self.tpl_perf_guide)
            # 临时清醒期间每轮带"还没醒透"状态行（持续困意）
            if self.phase == PHASE_TEMP_WAKE and self._drowsy_say_gate():
                parts.append(self.tpl_temp_wake_fact)
            # 熬夜次日的"没睡醒"状态行（清醒期、未到截止；午睡结束即解除）
            if self.phase == PHASE_AWAKE and time.time() < self._drowsy_until and self._drowsy_say_gate():
                parts.append(self.tpl_drowsy)
            # v0.8.0 陪聊宽限中的困意爬坡（每轮带当前档，画面体状态行）
            if self._grace is not None:
                lv = self._grace_level(time.time())
                parts.append((self.tpl_drowsy_1, self.tpl_drowsy_2, self.tpl_drowsy_3)[lv - 1])
            # v0.8.0 熬夜次日分级事实行（代价开关管的就是这层；45m轻/1h很困/4h通宵）
            if (self.c_stayup_cost and self._stayup_tier > 0 and self.phase == PHASE_AWAKE
                    and time.time() < self._drowsy_until and self._drowsy_say_gate()):
                is_rest = "，而且还是休息日" if (self._last_night or {}).get("rest") else ""
                if self._stayup_tier == 1:
                    parts.append(_render(self.tpl_stayup_light, x=int(self._stayup_h * 60)))
                elif self._stayup_tier == 2:
                    parts.append(_render(self.tpl_stayup_heavy, x=f"{self._stayup_h:.1f}"))
                else:
                    parts.append(_render(self.tpl_stayup_allnight, x=f"{self._stayup_h:.0f}", is_rest=is_rest))
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
            # 夜间事实行：离入睡还早别演"这就去睡"（聊天里说"我也睡了"实际还没到点=穿帮；
            # 对方道晚安她该说"你再玩会儿/我等下才睡"而不是跟着说要睡）
            if self.night_fact_from is not None and self.phase != PHASE_SLEEPING:
                nnow = time.time()
                nbed = 0.0
                nrec = self._night or {}
                if nrec.get("anchor") == datetime.now().date().isoformat():
                    nbed = float(nrec.get("bed_ts") or 0)
                if not nbed and self._sleep_window and self._sleep_window[0] > nnow:
                    nbed = float(self._sleep_window[0])
                if nbed and nnow < nbed:
                    ndt = datetime.fromtimestamp(nnow)
                    nm = ndt.hour * 60 + ndt.minute
                    if nm >= self.night_fact_from or nm < 8 * 60:  # 起点后到入睡；跨0点续到早8点前
                        parts.append(_render(self.tpl_night_fact, bed_time=_fmt_hm(nbed)))
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
            nap = ""
            if self._nap_plan:
                nap = (
                    f"午睡计划：{_fmt_hm(self._nap_plan['at_ts'])} 眯 "
                    f"{int((self._nap_plan['end_ts'] - self._nap_plan['at_ts']) / 60)} 分钟\n"
                )
            elif time.time() < self._drowsy_until:
                nap = "没睡醒状态生效中（蔫到 17:00 或午睡结束）\n"
            gr = ""
            if self._grace:
                g = self._grace
                idle = (time.time() - float(g.get("last_act") or time.time())) / 60
                gr = (f"陪聊宽限中：冷场 {idle:.0f}/{g.get('cold_gap', 0):.0f} 分钟溜走，"
                      f"硬顶 {_fmt_hm(float(g.get('hard_cap') or 0))}，困意档 {self._grace_level(time.time())}/3\n")
            led = self._ledger_line()
            yield event.plain_result(f"状态：清醒\n{sched}\n{nap}{gr}{led}{self._wake_line()}")

    def _ledger_line(self) -> str:
        """v0.8.0 熬夜账本披露行（/睡觉状态 用）"""
        d = self._ledger_deficit()
        if not self._stayup_ledger:
            return ""
        allow = self._grace_allowance_min()
        tier_txt = {0: "", 1: "（今天有点困）", 2: "（今天很困）", 3: "（通宵后遗症）"}.get(
            self._stayup_tier, "")
        return (f"熬夜账本（7天滚动）：亏空 {d:.0f} 分钟，今晚陪聊上限 "
                f"{allow:.0f} 分钟{tier_txt}\n")

    @filter.command("作息分布")
    async def cmd_dist(self, event: AstrMessageEvent, args=None):
        """按当前配置模拟 100 晚，出入睡/醒来直方图；参数=休息日 则锚休息日夜"""
        if not self._is_admin(event):
            yield event.plain_result("没权限")
            return
        want_rest = any(k in str(args or "") for k in ("休", "周末", "末"))
        async with self._lock:
            snap = (self._night, self._last_night, self._nap_plan, self._drowsy_until,
                    self._debt_minutes, self._fixed_jit)
            _save = self._save_state
            self._save_state = lambda: None  # 模拟期间禁止落盘
            try:
                d = datetime.now().date()
                anchor = None
                for _ in range(10):
                    d += timedelta(days=1)
                    if self._is_rest_day(d + timedelta(days=1)) == want_rest:
                        anchor = datetime.combine(d, dtime(23, 0))
                        break
                if anchor is None:  # 兜底：找不到就硬锚明天
                    anchor = datetime.combine(datetime.now().date() + timedelta(days=1), dtime(23, 0))
                recs = [self._roll_tonight(anchor) for _ in range(100)]
            finally:
                self._save_state = _save
                (self._night, self._last_night, self._nap_plan, self._drowsy_until,
                 self._debt_minutes, self._fixed_jit) = snap

        def _idx(hm, wrap):
            h, m = int(hm[:2]), int(hm[3:5])
            i = (h * 60 + m) // 30
            return i + 48 if wrap and h < 12 else i

        def _histo(keys, lo, hi, wrap=False):
            cnt = {}
            for k in keys:
                cnt[_idx(k, wrap)] = cnt.get(_idx(k, wrap), 0) + 1
            mx = max(cnt.values() or [1])
            lines = []
            for t in range(lo, hi + 1):
                tt = t * 30
                n = cnt.get(t, 0)
                lines.append(f"  {(tt // 60) % 24:02d}:{tt % 60:02d} |{'█' * round(n * 30 / mx)} {n}")
            return "\n".join(lines)

        fmt = lambda t: datetime.fromtimestamp(t).strftime("%H:%M")
        beds = [fmt(r["bed_ts"]) for r in recs]
        wakes = [fmt(r["wake_ts"]) for r in recs]
        lo_ts = min(r["wake_ts"] for r in recs)
        hi_ts = max(r["wake_ts"] for r in recs)
        ins = sum(r["insomnia"] for r in recs)
        overs = sum(bool(r["oversleep"]) for r in recs)
        late = sum(bool(r.get("late_bed")) for r in recs)
        note = "" if self.rest_independent else "\n（休息日独立未开启：两种模式分布相同）"
        yield event.plain_result(
            f"模拟100晚·{'休息日' if want_rest else '工作日'}夜（当前配置）\n"
            f"入睡：\n{_histo(beds, 46, 55, True)}\n"
            f"醒来：\n{_histo(wakes, _idx(fmt(lo_ts), False), _idx(fmt(hi_ts), False))}\n"
            f"失眠{ins}% 赖床{overs}% 熬夜资格{late}%\n"
            f"醒来区间 {fmt(lo_ts)}~{fmt(hi_ts)}{note}"
        )

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
