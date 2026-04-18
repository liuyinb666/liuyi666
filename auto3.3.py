#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import asyncio
import aiohttp
import aiofiles
import re
import time
import random
import hashlib
import numpy as np
import csv
from io import StringIO
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any, Tuple, Set
from collections import deque, Counter
import logging
import pickle
from dataclasses import dataclass, field, asdict
import traceback
import signal

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters, ConversationHandler
)
from telegram.error import BadRequest, Forbidden, TimedOut
from telegram.constants import ParseMode


# ==================== 配置 ====================
class Config:
    BOT_TOKEN = '8657094615:AAF3oBprdhwObKbjEEFL9bjjYMRu1l-yifI'
    
    # 移除API_ID和API_HASH，不再需要用户账号登录
    PC28_API_BASE = "https://www.pc28.help/api"
    ADMIN_USER_IDS = [5338954122]

    DATA_DIR = Path("data")
    LOGS_DIR = DATA_DIR / "logs"
    CACHE_DIR = DATA_DIR / "cache"
    INITIAL_HISTORY_SIZE = 100
    CACHE_SIZE = 200
    DEFAULT_BASE_AMOUNT = 20000
    DEFAULT_MAX_AMOUNT = 1000000
    DEFAULT_MULTIPLIER = 2.0
    DEFAULT_STOP_LOSS = 0
    DEFAULT_STOP_WIN = 0
    DEFAULT_STOP_BALANCE = 0
    DEFAULT_RESUME_BALANCE = 0
    MIN_BET_AMOUNT = 1
    MAX_BET_AMOUNT = 10000000
    EXCHANGE_RATE = 100000
    REQUEST_TIMEOUT = 15
    MAX_RETRIES = 3
    RETRY_BACKOFF = 2
    MAX_HISTORY = 61
    GAME_CYCLE_SECONDS = 210
    CLOSE_BEFORE_SECONDS = 50
    MANUAL_LINK = "https://t.me/yugejnd/9"

    SCHEDULER_CHECK_INTERVAL = 5
    HEALTH_CHECK_INTERVAL = 60

    EXPLORATION_RATE = 0.10
    EXPLORATION_MIN = 0.01
    EXPLORATION_DECAY = 0.99
    NOISE_SCALE = 0.05

    MODEL_SAVE_FILE = "pc28_model.json"
    PATTERNS_FILE = "pc28_patterns.json"
    LONG_TERM_MEMORY_FILE = "pc28_memory.json"
    TRAINING_STATE_FILE = "training_state.json"

    BALANCE_CACHE_SECONDS = 30
    LOG_RETENTION_DAYS = 7
    ACCOUNT_SAVE_INTERVAL = 30
    MAX_CONCURRENT_PREDICTIONS = 3

    # 对话状态
    ADD_ACCOUNT = 10
    CHASE_NUMBERS, CHASE_PERIODS, CHASE_AMOUNT = range(11, 14)
    SET_BASE_AMOUNT, SET_MAX_AMOUNT, SET_STOP_LOSS, SET_STOP_WIN, SET_STOP_BALANCE, SET_RESUME_BALANCE = range(20, 26)

    MAX_ACCOUNTS_PER_USER = 5

    KENO_HISTORY_DOWNLOAD = 5000
    KJ_HISTORY_DOWNLOAD = 5000

    # 训练参数
    TRAIN_EPOCHS = 200
    TRAIN_BATCH_SIZE = 100
    TRAIN_VALIDATION_SPLIT = 0.2
    MIN_TRAIN_DATA = 1000
    TRAIN_SAMPLES_PER_EPOCH = 800
    TRAIN_VALIDATION_SAMPLES = 200
    TRAIN_PATIENCE = 10
    TRAIN_LR = 0.001
    TRAIN_LR_DECAY = 0.5
    TRAIN_COOLDOWN = 5

    PREDICTION_HISTORY_SIZE = 20

    RISK_PROFILES = {
        '保守': 0.005,
        '稳定': 0.01,
        '激进': 0.02,
    }

    @classmethod
    def init_dirs(cls):
        cls.DATA_DIR.mkdir(exist_ok=True)
        cls.LOGS_DIR.mkdir(exist_ok=True)
        cls.CACHE_DIR.mkdir(exist_ok=True)

    @classmethod
    def validate(cls):
        errors = []
        if not cls.BOT_TOKEN:
            errors.append("BOT_TOKEN未配置")
        if not cls.PC28_API_BASE.startswith(('http://', 'https://')):
            errors.append("PC28_API_BASE必须是有效的URL")
        if cls.MIN_BET_AMOUNT < 0:
            errors.append("最小投注金额不能为负数")
        if cls.MAX_BET_AMOUNT <= cls.MIN_BET_AMOUNT:
            errors.append("最大投注金额必须大于最小投注金额")
        if errors:
            raise ValueError("配置验证失败: " + ", ".join(errors))
        return True


Config.init_dirs()


# ==================== 工具函数 ====================
def increment_qihao(current_qihao: str) -> str:
    if not current_qihao:
        return "1"
    match = re.search(r'(\d+)$', current_qihao)
    if match:
        num_part = match.group(1)
        prefix = current_qihao[:match.start()]
        try:
            next_num = str(int(num_part) + 1).zfill(len(num_part))
            return prefix + next_num
        except:
            return current_qihao + "1"
    else:
        try:
            return str(int(current_qihao) + 1)
        except:
            return current_qihao + "1"


# ==================== 彩色日志 ====================
class ColoredFormatter(logging.Formatter):
    grey = "\x1b[38;20m"
    green = "\x1b[32;20m"
    red = "\x1b[31;20m"
    yellow = "\x1b[33;20m"
    blue = "\x1b[34;20m"
    reset = "\x1b[0m"

    FORMATS = {
        logging.INFO: grey + "%(asctime)s [%(levelname)s] %(message)s" + reset,
        logging.ERROR: red + "%(asctime)s [%(levelname)s] %(message)s" + reset,
        'BETTING': green + "%(asctime)s [投注] %(message)s" + reset,
        'PREDICTION': blue + "%(asctime)s [预测] %(message)s" + reset,
    }

    def format(self, record):
        if hasattr(record, 'betting') and record.betting:
            self._style._fmt = self.FORMATS['BETTING']
        elif hasattr(record, 'prediction') and record.prediction:
            self._style._fmt = self.FORMATS['PREDICTION']
        else:
            self._style._fmt = self.FORMATS.get(record.levelno, self.grey + "%(asctime)s [%(levelname)s] %(message)s" + self.reset)
        return super().format(record)


class BotLogger:
    def __init__(self):
        self.logger = logging.getLogger('PC28Bot')
        self.logger.setLevel(logging.INFO)
        self.logger.handlers.clear()

        console = logging.StreamHandler()
        console.setLevel(logging.INFO)
        console.setFormatter(ColoredFormatter(datefmt='%H:%M:%S'))
        self.logger.addHandler(console)

        log_file = Config.LOGS_DIR / f"bot_{datetime.now().strftime('%Y%m%d')}.log"
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
        self.logger.addHandler(file_handler)

        self._clean_old_logs()

    def _clean_old_logs(self):
        now = datetime.now()
        for f in Config.LOGS_DIR.glob("bot_*.log"):
            try:
                date_str = f.stem.split('_')[1]
                file_date = datetime.strptime(date_str, '%Y%m%d')
                if (now - file_date).days > Config.LOG_RETENTION_DAYS:
                    f.unlink()
            except:
                pass

    def log_system(self, msg): self.logger.info(f"[系统] {msg}")
    def log_account(self, user_id, phone, action): self.logger.info(f"[账户] 用户:{user_id} 手机:{self._mask_phone(phone)} {action}")
    def log_game(self, msg): self.logger.info(f"[游戏] {msg}")
    def log_betting(self, user_id, action, detail):
        extra = {'betting': True}
        self.logger.info(f"用户:{user_id} {action} {detail}", extra=extra)
    def log_prediction(self, user_id, action, detail):
        extra = {'prediction': True}
        self.logger.info(f"用户:{user_id} {action} {detail}", extra=extra)
    def log_analysis(self, msg): self.logger.debug(f"[分析] {msg}")
    def log_error(self, user_id, action, error):
        error_trace = traceback.format_exc()
        self.logger.error(f"[错误] 用户:{user_id} {action}: {error}\n{error_trace}")
    def log_api(self, action, detail): self.logger.debug(f"[API] {action} {detail}")

    def _mask_phone(self, phone: str) -> str:
        if len(phone) >= 8:
            return phone[:5] + "****" + phone[-3:]
        return phone


logger = BotLogger()


# ==================== 基础数据 ====================
COMBOS = ["小单", "小双", "大单", "大双"]
BASE_PROB = {"小单": 27.11, "小双": 23.83, "大单": 22.32, "大双": 26.74}
SUM_TO_COMBO = {
    0: "小双", 1: "小单", 2: "小双", 3: "小单", 4: "小双", 5: "小单", 6: "小双",
    7: "小单", 8: "小双", 9: "小单", 10: "小双", 11: "小单", 12: "小双", 13: "小单",
    14: "大双", 15: "大单", 16: "大双", 17: "大单", 18: "大双", 19: "大单", 20: "大双",
    21: "大单", 22: "大双", 23: "大单", 24: "大双", 25: "大单", 26: "大双", 27: "大单"
}
TRANSITION_MATRIX = {
    "小单": {"小单": 26.3, "小双": 23.9, "大单": 22.9, "大双": 26.9},
    "小双": {"小单": 27.2, "小双": 22.7, "大单": 22.9, "大双": 27.3},
    "大单": {"小单": 28.2, "小双": 23.9, "大单": 21.5, "大双": 26.5},
    "大双": {"小单": 27.0, "小双": 24.7, "大单": 21.9, "大双": 26.4}
}


# ==================== 预测算法（与原文相同，此处省略） ====================
# ... (保留所有算法类，由于篇幅限制，这里省略，实际使用时需要完整复制)
# 包括: Algorithms, PatternRecognizer, TrendAnalyzer, LongTermMemory, 
# KenoSimilarity, RLModel, TrainingState, ModelManager 等类


# ==================== 简化的账户模型（不需要Telethon客户端） ====================
@dataclass
class BetParams:
    base_amount: int = Config.DEFAULT_BASE_AMOUNT
    max_amount: int = Config.DEFAULT_MAX_AMOUNT
    multiplier: float = Config.DEFAULT_MULTIPLIER
    stop_loss: int = Config.DEFAULT_STOP_LOSS
    stop_win: int = Config.DEFAULT_STOP_WIN
    stop_balance: int = Config.DEFAULT_STOP_BALANCE
    resume_balance: int = Config.DEFAULT_RESUME_BALANCE


@dataclass
class Account:
    """简化的账户模型，不需要登录状态"""
    name: str  # 账户名称（用户自定义）
    owner_user_id: int
    created_time: str = field(default_factory=lambda: datetime.now().isoformat())
    
    # Bot相关配置
    auto_betting: bool = False
    prediction_broadcast: bool = False
    game_group_id: int = 0
    game_group_name: str = ""
    prediction_group_id: int = 0
    prediction_group_name: str = ""
    
    betting_strategy: str = "保守"
    betting_scheme: str = "组合1"
    bet_params: BetParams = field(default_factory=BetParams)
    
    # 统计
    total_bets: int = 0
    total_wins: int = 0
    consecutive_losses: int = 0
    consecutive_wins: int = 0
    
    last_bet_period: Optional[str] = None
    last_bet_types: List[str] = field(default_factory=list)
    last_bet_amount: int = 0
    last_prediction: Dict = field(default_factory=dict)
    
    # 追号
    chase_enabled: bool = False
    chase_numbers: List[int] = field(default_factory=list)
    chase_periods: int = 0
    chase_current: int = 0
    chase_amount: int = 0
    chase_stop_reason: Optional[str] = None
    
    # 推荐模式
    recommend_mode: bool = False
    risk_profile: str = "稳定"
    
    # 播报
    prediction_content: str = "double"
    broadcast_stop_requested: bool = False
    
    # 连输连赢记录
    streak_records_double: List[Dict] = field(default_factory=list)
    streak_records_kill: List[Dict] = field(default_factory=list)
    current_streak_type_double: Optional[str] = None
    current_streak_start_double: Optional[str] = None
    current_streak_count_double: int = 0
    current_streak_type_kill: Optional[str] = None
    current_streak_start_kill: Optional[str] = None
    current_streak_count_kill: int = 0
    
    # 临时数据
    input_mode: Optional[str] = None
    input_buffer: str = ""
    last_message_id: Optional[int] = None
    
    def get_display_name(self) -> str:
        return self.name
    
    def get_risk_factor(self) -> float:
        return Config.RISK_PROFILES.get(self.risk_profile, 0.01)


# ==================== 账户管理器（简化版） ====================
class AccountManager:
    def __init__(self):
        self.accounts_file = Config.DATA_DIR / "accounts.json"
        self.user_states_file = Config.DATA_DIR / "user_states.json"
        self.accounts: Dict[str, Account] = {}  # key: name
        self.user_states: Dict[int, Dict] = {}
        self.update_lock = asyncio.Lock()
        self._dirty: Set[str] = set()
        self._save_task: Optional[asyncio.Task] = None
        self.load_accounts()
        self.load_user_states()
        logger.log_system(f"账户管理器初始化完成，已加载 {len(self.accounts)} 个账户")

    def load_accounts(self):
        if self.accounts_file.exists():
            try:
                with open(self.accounts_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for name, acc_dict in data.items():
                    bet_params_dict = acc_dict.get('bet_params', {})
                    bet_params = BetParams(**bet_params_dict)
                    acc_dict['bet_params'] = bet_params
                    
                    # 确保所有字段存在
                    defaults = {
                        'chase_enabled': False, 'chase_numbers': [], 'chase_periods': 0,
                        'chase_current': 0, 'chase_amount': 0, 'chase_stop_reason': None,
                        'recommend_mode': False, 'risk_profile': "稳定",
                        'streak_records_double': [], 'streak_records_kill': [],
                        'current_streak_type_double': None, 'current_streak_start_double': None,
                        'current_streak_count_double': 0, 'current_streak_type_kill': None,
                        'current_streak_start_kill': None, 'current_streak_count_kill': 0,
                        'last_message_id': None, 'prediction_content': "double",
                        'broadcast_stop_requested': False, 'input_mode': None, 'input_buffer': ""
                    }
                    for k, v in defaults.items():
                        if k not in acc_dict:
                            acc_dict[k] = v
                    
                    self.accounts[name] = Account(**acc_dict)
            except Exception as e:
                logger.log_error(0, "加载账户数据失败", e)

    async def save_accounts(self):
        data = {}
        for name, acc in self.accounts.items():
            acc_dict = asdict(acc)
            data[name] = acc_dict
        try:
            async with aiofiles.open(self.accounts_file, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(data, ensure_ascii=False, indent=2))
        except Exception as e:
            logger.log_error(0, "保存账户数据失败", e)

    async def _periodic_save(self):
        while True:
            await asyncio.sleep(Config.ACCOUNT_SAVE_INTERVAL)
            dirty = None
            async with self.update_lock:
                if self._dirty:
                    dirty = self._dirty.copy()
                    self._dirty.clear()
            if dirty:
                logger.log_system(f"批量保存 {len(dirty)} 个账户")
                await self.save_accounts()

    def load_user_states(self):
        if self.user_states_file.exists():
            try:
                with open(self.user_states_file, 'r', encoding='utf-8') as f:
                    self.user_states = json.load(f)
            except Exception as e:
                logger.log_error(0, "加载用户状态失败", e)

    def save_user_states(self):
        try:
            with open(self.user_states_file, 'w', encoding='utf-8') as f:
                json.dump(self.user_states, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.log_error(0, "保存用户状态失败", e)

    async def add_account(self, user_id, name) -> Tuple[bool, str]:
        async with self.update_lock:
            if user_id not in Config.ADMIN_USER_IDS:
                user_accounts = [acc for acc in self.accounts.values() if acc.owner_user_id == user_id]
                if len(user_accounts) >= Config.MAX_ACCOUNTS_PER_USER:
                    return False, f"每个用户最多只能添加 {Config.MAX_ACCOUNTS_PER_USER} 个账户"
            if name in self.accounts:
                return False, "账户名称已存在"
            if not re.match(r'^[a-zA-Z0-9\u4e00-\u9fa5_]{1,20}$', name):
                return False, "账户名称格式不正确（1-20个字符，支持中文、字母、数字、下划线）"
            self.accounts[name] = Account(name=name, owner_user_id=user_id)
            self._dirty.add(name)
            logger.log_account(user_id, name, "添加账户")
            return True, f"账户 {name} 添加成功"

    def get_account(self, name) -> Optional[Account]:
        return self.accounts.get(name)

    async def update_account(self, name, **kwargs):
        async with self.update_lock:
            if name in self.accounts:
                acc = self.accounts[name]
                for k, v in kwargs.items():
                    if k == 'bet_params' and isinstance(v, dict):
                        for pk, pv in v.items():
                            setattr(acc.bet_params, pk, pv)
                    else:
                        setattr(acc, k, v)
                self._dirty.add(name)
                return True
            return False

    def get_user_accounts(self, user_id):
        return [acc for acc in self.accounts.values() if acc.owner_user_id == user_id]

    def set_user_state(self, user_id, state, data=None):
        self.user_states.setdefault(user_id, {})
        self.user_states[user_id]['state'] = state
        if data:
            self.user_states[user_id].update(data)
        self.user_states[user_id]['last_update'] = datetime.now().isoformat()
        self.save_user_states()

    def get_user_state(self, user_id):
        return self.user_states.get(user_id, {})

    async def start_periodic_save(self):
        self._save_task = asyncio.create_task(self._periodic_save())

    async def stop_periodic_save(self):
        if self._save_task:
            self._save_task.cancel()
            try:
                await self._save_task
            except asyncio.CancelledError:
                pass


# ==================== 消息发送器（使用Bot直接发送） ====================
class MessageSender:
    def __init__(self, application: Application):
        self.application = application
        self._send_locks: Dict[int, asyncio.Lock] = {}

    async def send_message(self, chat_id: int, text: str, parse_mode: str = 'Markdown') -> Optional[int]:
        """发送消息到指定群组/用户"""
        if chat_id not in self._send_locks:
            self._send_locks[chat_id] = asyncio.Lock()
        
        async with self._send_locks[chat_id]:
            try:
                # 移除不支持的Markdown语法
                safe_text = text.replace('```', '`')
                msg = await self.application.bot.send_message(
                    chat_id=chat_id,
                    text=safe_text,
                    parse_mode=parse_mode if parse_mode == 'Markdown' else None
                )
                return msg.message_id
            except Forbidden:
                logger.log_error(0, f"发送消息失败: Bot不在群组 {chat_id} 中", None)
                return None
            except TimedOut:
                logger.log_error(0, f"发送消息超时: {chat_id}", None)
                return None
            except BadRequest as e:
                # 尝试不带格式发送
                try:
                    msg = await self.application.bot.send_message(chat_id=chat_id, text=text)
                    return msg.message_id
                except Exception:
                    logger.log_error(0, f"发送消息失败: {chat_id}", e)
                    return None
            except Exception as e:
                logger.log_error(0, f"发送消息失败: {chat_id}", e)
                return None

    async def send_bet_message(self, chat_id: int, bet_items: List[str]) -> bool:
        """发送投注消息到游戏群"""
        message = " ".join(bet_items)
        logger.log_betting(0, "发送投注", f"群组:{chat_id} 消息:{message}")
        msg_id = await self.send_message(chat_id, message)
        return msg_id is not None


# ==================== API模块（与原文相同，此处省略） ====================
# ... (保留 PC28API 类，与原文相同)


# ==================== 投注执行器（使用Bot发送） ====================
class BetExecutor:
    def __init__(self, account_manager: AccountManager, message_sender: MessageSender):
        self.account_manager = account_manager
        self.message_sender = message_sender
        self.game_stats = {
            'total_cycles': 0, 'betting_cycles': 0,
            'successful_bets': 0, 'failed_bets': 0,
        }

    def _calculate_bet_amount(self, acc: Account, base_override: int = None) -> Tuple[int, Dict]:
        """根据策略和连输连赢计算投注金额"""
        base = base_override if base_override is not None else acc.bet_params.base_amount
        max_amt = acc.bet_params.max_amount
        losses = acc.consecutive_losses
        wins = acc.consecutive_wins
        mult = acc.bet_params.multiplier
        strategy = acc.betting_strategy
        updates = {}

        if strategy == '马丁格尔':
            if wins > 0:
                amt = base
                updates['martingale_reset'] = True
            else:
                amt = base * (mult ** losses)
        elif strategy == '斐波那契':
            if wins > 0:
                amt = base
                updates['fibonacci_reset'] = True
            else:
                fib = [1, 1, 2, 3, 5, 8, 13, 21, 34, 55]
                idx = min(losses, len(fib)-1)
                amt = base * fib[idx]
        elif strategy == '激进':
            amt = base * (1 + losses)
        else:
            amt = base

        amt = min(amt, max_amt)
        amt = max(amt, Config.MIN_BET_AMOUNT)
        return int(amt), updates

    def _get_bet_types(self, pred: Dict, scheme: str) -> List[str]:
        rec = [pred['main'], pred['candidate']]
        if pred['main'] == pred['candidate']:
            rec = [pred['main']]
        if scheme == '组合1':
            return [rec[0]] if rec else ['小双']
        if scheme == '组合2':
            return [rec[1]] if len(rec) > 1 else ['小双']
        if scheme == '组合1+2':
            return rec[:2] if len(rec) >= 2 else rec
        return [rec[0]] if rec else ['小双']

    async def execute_bet(self, name: str, prediction: Dict, latest: Dict) -> bool:
        """执行投注"""
        acc = self.account_manager.get_account(name)
        if not acc:
            return False
        
        if not acc.auto_betting:
            return False
        
        current_qihao = latest.get('qihao')
        if acc.last_bet_period == current_qihao:
            return False
        
        # 检查封盘时间
        now = datetime.now()
        next_open = latest['parsed_time'] + timedelta(seconds=Config.GAME_CYCLE_SECONDS)
        close_time = next_open - timedelta(seconds=Config.CLOSE_BEFORE_SECONDS)
        if now >= close_time:
            logger.log_betting(0, "已封盘，跳过投注", f"账户:{name}")
            return False
        
        # 获取投注类型
        if acc.betting_scheme == '杀主':
            kill_combo = prediction['kill']
            bet_types = [c for c in COMBOS if c != kill_combo]
        else:
            bet_types = self._get_bet_types(prediction, acc.betting_scheme)
        
        # 计算投注金额
        if acc.recommend_mode:
            # 推荐模式：使用固定基础金额（实际游戏没有余额查询，使用配置的基础金额）
            temp_base = acc.bet_params.base_amount
        else:
            temp_base = acc.bet_params.base_amount
        
        bet_amount, updates = self._calculate_bet_amount(acc, temp_base)
        if updates:
            await self.account_manager.update_account(name, **updates)
        
        # 发送投注
        bet_items = [f"{t} {bet_amount}" for t in bet_types]
        
        if not acc.game_group_id:
            logger.log_betting(0, "未设置游戏群", f"账户:{name}")
            return False
        
        success = await self.message_sender.send_bet_message(acc.game_group_id, bet_items)
        
        if success:
            self.game_stats['successful_bets'] += 1
            self.game_stats['betting_cycles'] += 1
            await self.account_manager.update_account(
                name,
                last_bet_period=current_qihao,
                last_bet_types=bet_types,
                last_bet_amount=bet_amount,
                total_bets=acc.total_bets + 1,
                last_prediction={
                    'main': prediction['main'],
                    'candidate': prediction['candidate'],
                    'confidence': prediction['confidence'],
                    'kill': prediction['kill']
                }
            )
            logger.log_betting(0, "投注成功",
                f"账户:{name} 每注金额:{bet_amount} 类型:{bet_types} 置信度:{prediction['confidence']:.1f}%")
        else:
            self.game_stats['failed_bets'] += 1
            logger.log_betting(0, "投注失败", f"账户:{name}")
        
        return success

    async def execute_chase(self, name: str, latest: dict) -> bool:
        """执行追号"""
        acc = self.account_manager.get_account(name)
        if not acc or not acc.chase_enabled:
            return False

        if acc.chase_current >= acc.chase_periods:
            await self.account_manager.update_account(
                name,
                chase_enabled=False,
                chase_stop_reason="期满",
                chase_numbers=[],
                chase_periods=0,
                chase_current=0,
                chase_amount=0
            )
            logger.log_betting(0, "追号期满停止", f"账户:{name}")
            return False

        current_qihao = latest.get('qihao')
        if acc.last_bet_period == current_qihao:
            return False

        bet_amount = acc.chase_amount if acc.chase_amount > 0 else acc.bet_params.base_amount
        bet_amount = min(bet_amount, acc.bet_params.max_amount)
        bet_amount = max(bet_amount, Config.MIN_BET_AMOUNT)

        bet_items = [f"{num}/{bet_amount}" for num in acc.chase_numbers]
        if not bet_items:
            return False

        if not acc.game_group_id:
            return False

        success = await self.message_sender.send_bet_message(acc.game_group_id, bet_items)
        
        if success:
            new_current = acc.chase_current + 1
            await self.account_manager.update_account(
                name,
                chase_current=new_current,
                last_bet_period=current_qihao,
                last_bet_types=[str(num) for num in acc.chase_numbers],
                last_bet_amount=bet_amount,
                total_bets=acc.total_bets + 1
            )
            logger.log_betting(0, "追号成功",
                f"账户:{name} 数字:{acc.chase_numbers} 金额:{bet_amount} 进度:{new_current}/{acc.chase_periods}")
            return True
        
        return False

    def get_stats(self):
        auto = sum(1 for a in self.account_manager.accounts.values() if a.auto_betting)
        broadcast = sum(1 for a in self.account_manager.accounts.values() if a.prediction_broadcast)
        return {
            'auto_betting_accounts': auto,
            'broadcast_accounts': broadcast,
            'game_stats': self.game_stats.copy()
        }


# ==================== 预测播报器（使用Bot发送） ====================
class PredictionBroadcaster:
    def __init__(self, account_manager: AccountManager, model_manager, 
                 api_client, message_sender: MessageSender):
        self.account_manager = account_manager
        self.model = model_manager
        self.api = api_client
        self.message_sender = message_sender
        self.broadcast_tasks: Dict[str, asyncio.Task] = {}
        self.global_predictions = {
            'predictions': [],
            'last_open_qihao': None,
            'next_qihao': None,
            'last_update': None,
            'cached_double_message': None,
            'cached_kill_message': None
        }
        self.last_sent_qihao: Dict[str, str] = {}
        self.stop_target_qihao: Dict[str, str] = {}

    async def start_broadcast(self, name: str, user_id: int) -> Tuple[bool, str]:
        acc = self.account_manager.get_account(name)
        if not acc:
            return False, "账户不存在"
        if not acc.prediction_group_id:
            return False, "请先设置播报群"
        if acc.broadcast_stop_requested:
            await self.account_manager.update_account(name, broadcast_stop_requested=False)
            self.stop_target_qihao.pop(name, None)
        if name in self.broadcast_tasks and not self.broadcast_tasks[name].done():
            return True, "播报器已在运行"
        if name in self.broadcast_tasks:
            self.broadcast_tasks[name].cancel()
        self.last_sent_qihao[name] = self.global_predictions.get('next_qihao')
        task = asyncio.create_task(self._broadcast_loop(name, acc.prediction_group_id))
        self.broadcast_tasks[name] = task
        await self.account_manager.update_account(name, prediction_broadcast=True)
        logger.log_prediction(user_id, "播报器启动", f"账户:{name}")
        return True, "预测播报器启动成功"

    async def stop_broadcast(self, name: str, user_id: int) -> Tuple[bool, str]:
        acc = self.account_manager.get_account(name)
        if not acc:
            return False, "账户不存在"
        if not acc.prediction_broadcast:
            return True, "播报器已停止"
        target = self.global_predictions.get('next_qihao')
        await self.account_manager.update_account(name, broadcast_stop_requested=True)
        self.stop_target_qihao[name] = target
        logger.log_prediction(user_id, "播报器平滑停止请求", f"账户:{name} 目标期号:{target}")
        return True, "将在最后一期开奖后停止播报"

    async def _broadcast_loop(self, name: str, group_id: int):
        error_count = 0
        target_qihao = None
        while True:
            try:
                acc = self.account_manager.get_account(name)
                if not acc:
                    break

                if acc.broadcast_stop_requested:
                    if target_qihao is None:
                        target_qihao = self.stop_target_qihao.get(name)
                    if target_qihao is None:
                        target_qihao = self.global_predictions.get('next_qihao')
                    last_sent = self.last_sent_qihao.get(name)
                    if last_sent != target_qihao:
                        await self._send_prediction(group_id, name)
                        last_sent = target_qihao
                    last_open = self.global_predictions.get('last_open_qihao')
                    if last_open == target_qihao:
                        await self.account_manager.update_account(
                            name, prediction_broadcast=False, broadcast_stop_requested=False)
                        self.last_sent_qihao.pop(name, None)
                        self.stop_target_qihao.pop(name, None)
                        break
                elif not acc.prediction_broadcast:
                    self.last_sent_qihao.pop(name, None)
                    self.stop_target_qihao.pop(name, None)
                    break
                else:
                    await self._send_prediction(group_id, name)

                error_count = 0
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                error_count += 1
                logger.log_error(0, f"播报器循环异常 {name}", e)
                if error_count >= 5:
                    await self.account_manager.update_account(
                        name, prediction_broadcast=False, broadcast_stop_requested=False)
                    break
                await asyncio.sleep(10)

    async def _send_prediction(self, group_id: int, name: str) -> Optional[int]:
        acc = self.account_manager.get_account(name)
        if not acc:
            return None
        
        current_next_qihao = self.global_predictions.get('next_qihao')
        if self.last_sent_qihao.get(name) == current_next_qihao:
            return None

        if acc.prediction_content == "double":
            message = self.global_predictions.get('cached_double_message')
        else:
            message = self.global_predictions.get('cached_kill_message')
        
        if not message:
            self._update_cached_messages()
            message = self.global_predictions.get('cached_double_message') if acc.prediction_content == "double" \
                else self.global_predictions.get('cached_kill_message')
        
        msg_id = await self.message_sender.send_message(group_id, message)
        if msg_id:
            self.last_sent_qihao[name] = current_next_qihao
            await self.account_manager.update_account(name, last_message_id=msg_id)
        return msg_id

    def _update_cached_messages(self):
        lines = ["🤖 强化学习中"]
        lines.append("-" * 30)
        lines.append("期号    主推候选  状态  和值")
        for p in self.global_predictions['predictions'][-Config.PREDICTION_HISTORY_SIZE:]:
            q = p['qihao'][-4:] if len(p['qihao']) >= 4 else p['qihao']
            combo_str = p['main'] + p['candidate']
            mark = "✅" if p.get('correct_double') is True else "❌" if p.get('correct_double') is False else "⏳"
            s = str(p['sum']) if p['sum'] is not None else "--"
            lines.append(f"{q:4s}   {combo_str:4s}   {mark:2s}   {s:>2s}")
        self.global_predictions['cached_double_message'] = "AI双组预测\n```" + "\n".join(lines) + "\n```"

        kill_lines = ["🤖 Keno暗线匹配灰盒杀"]
        kill_lines.append("-" * 30)
        kill_lines.append("期号    杀组    状态  和值")
        for p in self.global_predictions['predictions'][-Config.PREDICTION_HISTORY_SIZE:]:
            q = p['qihao'][-4:] if len(p['qihao']) >= 4 else p['qihao']
            kill = p.get('kill', '--')
            mark = "✅" if p.get('correct_kill') is True else "❌" if p.get('correct_kill') is False else "⏳"
            s = str(p['sum']) if p['sum'] is not None else "--"
            kill_lines.append(f"{q:4s}   {kill:4s}   {mark:2s}   {s:>2s}")
        self.global_predictions['cached_kill_message'] = "AI杀组预测\n```" + "\n".join(kill_lines) + "\n```"

    async def update_global_predictions(self, prediction, next_qihao, latest):
        current_open_qihao = latest.get('qihao')
        current_sum = latest.get('sum')
        current_combo = latest.get('combo')

        matched_pred = None
        for p in self.global_predictions['predictions']:
            if p.get('qihao') == current_open_qihao:
                matched_pred = p
                break

        if matched_pred:
            matched_pred['actual'] = current_combo
            matched_pred['sum'] = current_sum
            matched_pred['correct_double'] = (matched_pred['main'] == current_combo or 
                                               matched_pred['candidate'] == current_combo)
            matched_pred['correct_kill'] = (matched_pred['kill'] != current_combo)
            await self.model.learn(matched_pred, current_combo, current_open_qihao, current_sum)
            self.model.pattern_recognizer.learn_pattern(await self.api.get_history(50))
            self.model.long_term_memory.learn(await self.api.get_history(1))

        new_pred = {
            'qihao': next_qihao,
            'main': prediction['main'],
            'candidate': prediction['candidate'],
            'kill': prediction['kill'],
            'confidence': prediction['confidence'],
            'time': datetime.now().isoformat(),
            'actual': None,
            'sum': None,
            'correct_double': None,
            'correct_kill': None,
            'message_id': None,
            'algo_details': prediction.get('algo_details', []),
        }

        existing = None
        for i, p in enumerate(self.global_predictions['predictions']):
            if p.get('qihao') == next_qihao:
                existing = p
                break

        if existing:
            existing.update(new_pred)
        else:
            self.global_predictions['predictions'].append(new_pred)
            if len(self.global_predictions['predictions']) > Config.PREDICTION_HISTORY_SIZE:
                self.global_predictions['predictions'] = self.global_predictions['predictions'][-Config.PREDICTION_HISTORY_SIZE:]

        self.global_predictions['last_open_qihao'] = current_open_qihao
        self.global_predictions['next_qihao'] = next_qihao
        self.global_predictions['last_update'] = datetime.now().isoformat()
        self._update_cached_messages()


# ==================== 全局调度器 ====================
class GlobalScheduler:
    def __init__(self, account_manager: AccountManager, model_manager, api_client,
                 prediction_broadcaster: PredictionBroadcaster, bet_executor: BetExecutor):
        self.account_manager = account_manager
        self.model = model_manager
        self.api = api_client
        self.prediction_broadcaster = prediction_broadcaster
        self.bet_executor = bet_executor
        self.task = None
        self.running = False
        self.last_qihao = None
        self.check_interval = Config.SCHEDULER_CHECK_INTERVAL
        self.health_check_interval = Config.HEALTH_CHECK_INTERVAL
        self.last_health_check = 0
        self.tasks = set()

    def _is_maintenance_time(self, now: datetime) -> bool:
        beijing_time = now + timedelta(hours=8)
        hour = beijing_time.hour
        minute = beijing_time.minute
        is_dst = 4 <= now.month <= 10
        if is_dst:
            return (hour == 19 and minute >= 55) or (hour == 20 and minute <= 30)
        else:
            return (hour == 20 and minute >= 55) or (hour == 21 and minute <= 30)

    async def start(self):
        if self.running:
            return
        self.running = True
        self.task = asyncio.create_task(self._run())
        self.tasks.add(self.task)
        logger.log_system("全局调度器已启动")

    async def stop(self):
        self.running = False
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        logger.log_system("全局调度器已停止")

    async def _run(self):
        init_success = False
        for attempt in range(5):
            if await self.api.initialize_history():
                init_success = True
                break
            logger.log_system(f"历史数据初始化失败，5秒后重试 ({attempt+1}/5)")
            await asyncio.sleep(5)
        if not init_success:
            logger.log_error(0, "全局调度器", "无法初始化历史数据，调度器将继续运行但可能无法预测")

        while self.running:
            try:
                now = datetime.now()

                if self._is_maintenance_time(now):
                    logger.log_system("当前处于维护时段，暂停实时检测")
                    await asyncio.sleep(1800)
                    continue

                if (now.timestamp() - self.last_health_check) > self.health_check_interval:
                    await self._health_check()
                    self.last_health_check = now.timestamp()

                latest = await self.api.get_latest_result()
                if latest:
                    qihao = latest.get('qihao')
                    if qihao != self.last_qihao:
                        logger.log_game(f"检测到新期号: {qihao}")
                        await self._on_new_period(qihao, latest)
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.log_error(0, "全局调度器异常", e)
                await asyncio.sleep(10)

    async def _health_check(self):
        now = datetime.now()
        # 清理旧的账户状态等
        pass

    async def _on_new_period(self, qihao, latest):
        try:
            # 更新Keno数据
            keno = await self.api.get_latest_keno()
            if keno:
                self.model.keno_similarity.add_keno_data(keno, latest)
                self.model.keno_similarity.current_keno_nbrs = keno['nbrs']
                self.model.current_keno_nbrs = keno['nbrs']

            history = await self.api.get_history(50)
            if len(history) < 3:
                logger.log_game("历史数据不足，跳过预测")
                return

            prediction = self.model.predict(history, latest)
            next_qihao = increment_qihao(qihao)

            await self.prediction_broadcaster.update_global_predictions(prediction, next_qihao, latest)

            # 执行自动投注
            for name, acc in self.account_manager.accounts.items():
                if acc.auto_betting and acc.game_group_id and acc.last_bet_period != qihao:
                    await self.bet_executor.execute_chase(name, latest)
                    await self.bet_executor.execute_bet(name, prediction, latest)

            self.last_qihao = qihao

        except Exception as e:
            logger.log_error(0, f"处理新期号 {qihao} 失败", e)


# ==================== 主Bot类 ====================
class PC28Bot:
    def __init__(self):
        self.api = PC28API()
        self.account_manager = AccountManager()
        # 注意：这里需要完整的ModelManager，由于篇幅省略，实际使用时需要完整复制
        # self.model = ModelManager()
        self.model = None  # 占位
        
        self.application = Application.builder().token(Config.BOT_TOKEN).build()
        self.message_sender = MessageSender(self.application)
        self.bet_executor = BetExecutor(self.account_manager, self.message_sender)
        self.prediction_broadcaster = PredictionBroadcaster(
            self.account_manager, self.model, self.api, self.message_sender)
        self.global_scheduler = GlobalScheduler(
            self.account_manager, self.model, self.api,
            self.prediction_broadcaster, self.bet_executor)
        
        self._register_handlers()
        logger.log_system("PC28 Bot 初始化完成")

    def _register_handlers(self):
        self.application.add_handler(CommandHandler("start", self.cmd_start))
        self.application.add_handler(CommandHandler("cancel", self.cmd_cancel))

        # 添加账户对话
        add_account_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.add_account_start, pattern=r'^add_account$')],
            states={
                Config.ADD_ACCOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_account_input)],
            },
            fallbacks=[CommandHandler('cancel', self.cmd_cancel)],
        )
        self.application.add_handler(add_account_conv)

        # 设置基础金额对话
        amount_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.amount_set_start, pattern=r'^amount_set:([^:]+):([^:]+)$')],
            states={
                Config.SET_BASE_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.amount_set_input)],
                Config.SET_MAX_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.amount_set_input)],
                Config.SET_STOP_LOSS: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.amount_set_input)],
                Config.SET_STOP_WIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.amount_set_input)],
                Config.SET_STOP_BALANCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.amount_set_input)],
                Config.SET_RESUME_BALANCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.amount_set_input)],
            },
            fallbacks=[CommandHandler('cancel', self.cmd_cancel)],
        )
        self.application.add_handler(amount_conv)

        # 追号对话
        chase_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.chase_start, pattern=r'^action:setchaze:([^:]+)$')],
            states={
                Config.CHASE_NUMBERS: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.chase_input_numbers)],
                Config.CHASE_PERIODS: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.chase_input_periods)],
                Config.CHASE_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.chase_input_amount)],
            },
            fallbacks=[CommandHandler('cancel', self.cmd_cancel)],
        )
        self.application.add_handler(chase_conv)

        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text_message))
        self.application.add_handler(CallbackQueryHandler(self.handle_callback))
        self.application.add_error_handler(self.error_handler)

    async def error_handler(self, update, context):
        logger.log_error(0, "Bot错误", str(context.error))

    async def cmd_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("✅ 操作已取消")
        return ConversationHandler.END

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [
            [InlineKeyboardButton("📱 账户管理", callback_data="menu:accounts")],
            [InlineKeyboardButton("🎯 智能预测", callback_data="menu:prediction")],
            [InlineKeyboardButton("📊 系统状态", callback_data="menu:status")],
            [InlineKeyboardButton("❓ 帮助", callback_data="menu:help")],
            [InlineKeyboardButton("📖 使用手册", url=Config.MANUAL_LINK)]
        ]
        await update.message.reply_text(
            "🎰 *PC28 智能预测投注系统*\n\n"
            "✨ Bot会直接向游戏群发送投注消息，无需登录Telegram账号！\n\n"
            "请选择操作：",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )

    async def add_account_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(
            "📱 *添加账户*\n\n"
            "请输入账户名称（1-20个字符，支持中文、字母、数字、下划线）：\n\n"
            "例如：`主账户`、`test_001`\n\n"
            "点击 /cancel 取消",
            parse_mode='Markdown'
        )
        return Config.ADD_ACCOUNT

    async def add_account_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        name = update.message.text.strip()
        ok, msg = await self.account_manager.add_account(user_id, name)
        if ok:
            await update.message.reply_text(f"✅ {msg}")
            await self._show_account_detail(update.message, user_id, name)
        else:
            await update.message.reply_text(f"❌ {msg}")
            await self._show_main_menu(update.message)
        return ConversationHandler.END

    async def amount_set_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        param_name = query.data.split(':')[1]
        name = query.data.split(':')[2]
        context.user_data['amount_param'] = param_name
        context.user_data['amount_name'] = name
        
        param_display = {
            'base_amount': '基础金额',
            'max_amount': '最大金额',
            'stop_loss': '止损金额',
            'stop_win': '止盈金额',
            'stop_balance': '停止余额',
            'resume_balance': '恢复余额'
        }.get(param_name, param_name)
        
        await query.edit_message_text(
            f"🔢 请输入新的 {param_display}（整数KK）：\n\n点击 /cancel 取消",
            parse_mode='Markdown'
        )
        
        state_map = {
            'base_amount': Config.SET_BASE_AMOUNT,
            'max_amount': Config.SET_MAX_AMOUNT,
            'stop_loss': Config.SET_STOP_LOSS,
            'stop_win': Config.SET_STOP_WIN,
            'stop_balance': Config.SET_STOP_BALANCE,
            'resume_balance': Config.SET_RESUME_BALANCE,
        }
        return state_map.get(param_name, Config.SET_BASE_AMOUNT)

    async def amount_set_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        name = context.user_data.get('amount_name')
        param_name = context.user_data.get('amount_param')
        
        if not name or not param_name:
            await update.message.reply_text("❌ 会话已过期，请重新操作")
            return ConversationHandler.END
        
        try:
            amount = int(update.message.text.strip())
            if amount < 0:
                await update.message.reply_text("❌ 金额不能为负数")
                return
        except ValueError:
            await update.message.reply_text("❌ 请输入整数金额")
            return
        
        await self.account_manager.update_account(name, bet_params={param_name: amount})
        
        # 如果设置了基础金额，关闭推荐模式
        if param_name == 'base_amount':
            await self.account_manager.update_account(name, recommend_mode=False)
        
        logger.log_betting(user_id, "设置金额参数", f"账户:{name} {param_name}={amount}")
        await update.message.reply_text(f"✅ {param_name} 已设置为 {amount} KK")
        await self._show_account_detail(update.message, user_id, name)
        
        context.user_data.pop('amount_name', None)
        context.user_data.pop('amount_param', None)
        return ConversationHandler.END

    async def chase_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        name = query.data.split(':')[1]
        context.user_data['chase_name'] = name
        
        text = (
            "🔢 *设置数字追号 - 第1步/共3步*\n\n"
            "请输入要追的数字（0-27），多个数字可用空格、逗号或顿号分隔。\n"
            "例如：`0 5 12` 或 `0,5,12` 或 `0、5、12`\n\n"
            "📌 说明：追号将每期自动投注您指定的所有数字，直到期数用完或手动停止。\n\n"
            "点击 /cancel 取消"
        )
        await query.edit_message_text(text, parse_mode='Markdown')
        return Config.CHASE_NUMBERS

    async def chase_input_numbers(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip()
        parts = re.split(r'[,\s、]+', text)
        numbers = []
        for p in parts:
            p = p.strip()
            if p.lstrip('-').isdigit():
                num = int(p)
                if 0 <= num <= 27:
                    numbers.append(num)
        numbers = list(set(numbers))

        if not numbers:
            await update.message.reply_text("❌ 未输入有效数字（0-27），请重新输入：")
            return Config.CHASE_NUMBERS

        context.user_data['chase_numbers'] = numbers

        text = (
            f"✅ 已记录数字：{', '.join(map(str, numbers))}\n\n"
            "🔢 *第2步/共3步：请输入追号期数*\n\n"
            "请输入一个正整数，表示要连续追多少期。\n"
            "例如：`10` 表示连续追10期。\n\n"
            "点击 /cancel 取消"
        )
        await update.message.reply_text(text, parse_mode='Markdown')
        return Config.CHASE_PERIODS

    async def chase_input_periods(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip()
        if not text.isdigit() or int(text) <= 0:
            await update.message.reply_text("❌ 期数必须是正整数，请重新输入：")
            return Config.CHASE_PERIODS

        periods = int(text)
        context.user_data['chase_periods'] = periods

        text = (
            f"✅ 已设置期数：{periods} 期\n\n"
            "🔢 *第3步/共3步：请输入每注金额*\n\n"
            "请输入一个整数（单位：KK）。\n"
            "• 如果输入 `0`，则使用当前账户的基础金额。\n"
            "例如：`1000` 表示每注1000KK。\n\n"
            "点击 /cancel 取消"
        )
        await update.message.reply_text(text, parse_mode='Markdown')
        return Config.CHASE_AMOUNT

    async def chase_input_amount(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip()
        try:
            amount = int(text)
        except ValueError:
            await update.message.reply_text("❌ 金额必须是整数，请重新输入：")
            return Config.CHASE_AMOUNT

        if amount < 0:
            await update.message.reply_text("❌ 金额不能为负数，请重新输入：")
            return Config.CHASE_AMOUNT

        name = context.user_data.get('chase_name')
        numbers = context.user_data.get('chase_numbers', [])
        periods = context.user_data.get('chase_periods', 0)

        if not name:
            await update.message.reply_text("❌ 会话已过期，请重新操作")
            return ConversationHandler.END

        await self.account_manager.update_account(
            name,
            chase_enabled=True,
            chase_numbers=numbers,
            chase_periods=periods,
            chase_current=0,
            chase_amount=amount,
            chase_stop_reason=None
        )

        user_id = update.effective_user.id
        self.account_manager.set_user_state(user_id, 'account_selected', {'current_account': name})

        await update.message.reply_text(
            f"✅ *追号设置成功！*\n\n"
            f"📌 数字：{', '.join(map(str, numbers))}\n"
            f"📌 期数：{periods}\n"
            f"📌 每注金额：{amount if amount>0 else '使用基础金额'}KK\n\n"
            f"🔍 您可以在账户详情页查看追号状态。"
        )

        context.user_data.pop('chase_name', None)
        context.user_data.pop('chase_numbers', None)
        context.user_data.pop('chase_periods', None)

        await self._show_account_detail(update.message, user_id, name)
        return ConversationHandler.END

    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # 处理手动投注命令（在群组中）
        chat_id = update.effective_chat.id
        text = update.message.text.strip()
        
        # 检查是否是投注命令格式（如 "大 10000"）
        match = re.match(r'^([大小单双大小单双]{1,2})\s+(\d+)$', text)
        if match:
            bet_type = match.group(1)
            amount = int(match.group(2))
            
            # 查找使用该群组的账户
            for name, acc in self.account_manager.accounts.items():
                if acc.game_group_id == chat_id:
                    if amount < Config.MIN_BET_AMOUNT or amount > Config.MAX_BET_AMOUNT:
                        await update.message.reply_text(f"❌ 金额必须在{Config.MIN_BET_AMOUNT}-{Config.MAX_BET_AMOUNT}KK之间")
                        return
                    
                    success = await self.message_sender.send_bet_message(chat_id, [f"{bet_type} {amount}"])
                    if success:
                        await update.message.reply_text(f"✅ 已投注: {bet_type} {amount}KK")
                        logger.log_betting(update.effective_user.id, "手动投注", 
                                         f"账户:{name} 类型:{bet_type} 金额:{amount}")
                    else:
                        await update.message.reply_text("❌ 投注发送失败")
                    return

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data
        user = query.from_user.id

        route_map = {
            "menu:main": self._show_main_menu,
            "menu:prediction": self._show_prediction_menu,
            "menu:status": self._show_status_menu,
            "menu:help": self._show_help_menu,
            "add_account": self.add_account_start,
            "run_analysis": self._process_run_analysis,
            "refresh_status": self._show_status_menu,
        }
        if data in route_map:
            await route_map[data](query)
            return

        if data == "menu:accounts":
            await self._show_accounts_menu(query, user)
            return

        if data.startswith("select_account:"):
            name = data.split(":")[1]
            await self._show_account_detail(query, user, name)
            return

        if data.startswith("action:"):
            parts = data.split(":")
            action = parts[1]
            name = parts[2] if len(parts) > 2 else None
            await self._process_action(query, user, action, name)
            return

        if data.startswith("amount_menu:"):
            name = data.split(":")[1]
            await self._show_amount_menu(query, user, name)
            return

        if data.startswith("set_strategy:"):
            parts = data.split(":")
            name = parts[1]
            strategy = parts[2]
            await self._process_set_strategy(query, user, name, strategy)
            return

        if data.startswith("set_scheme:"):
            parts = data.split(":")
            name = parts[1]
            scheme = parts[2]
            await self._process_set_scheme(query, user, name, scheme)
            return

        if data.startswith("toggle_content:"):
            name = data.split(":")[1]
            await self._toggle_prediction_content(query, user, name)
            return

        if data.startswith("clear_streak:"):
            name = data.split(":")[1]
            await self._clear_streak_records(query, user, name)
            return

        if data.startswith("amount_recommend:"):
            name = data.split(":")[1]
            await self._toggle_recommend_mode(query, user, name)
            return

    async def _show_main_menu(self, query):
        kb = [
            [InlineKeyboardButton("📱 账户管理", callback_data="menu:accounts")],
            [InlineKeyboardButton("🎯 智能预测", callback_data="menu:prediction")],
            [InlineKeyboardButton("📊 系统状态", callback_data="menu:status")],
            [InlineKeyboardButton("❓ 帮助", callback_data="menu:help")],
            [InlineKeyboardButton("📖 使用手册", url=Config.MANUAL_LINK)]
        ]
        text = "🎮 *PC28 智能投注系统*\n\nBot会直接向游戏群发送投注消息，无需登录Telegram账号！\n\n请选择操作："
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

    async def _show_accounts_menu(self, query, user):
        accounts = self.account_manager.get_user_accounts(user)
        kb = []
        if not accounts:
            text = "📭 您还没有添加账户"
        else:
            text = "📱 *您的账户列表*\n\n"
            for acc in accounts:
                status = "🟢" if acc.auto_betting else "⚪"
                text += f"{status} {acc.get_display_name()}\n"
        kb.append([InlineKeyboardButton("➕ 添加账户", callback_data="add_account")])
        if accounts:
            for acc in accounts:
                kb.append([InlineKeyboardButton(f"{acc.get_display_name()}", callback_data=f"select_account:{acc.name}")])
        kb.append([InlineKeyboardButton("🔙 返回", callback_data="menu:main")])
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

    async def _show_account_detail(self, query_or_message, user, name):
        self.account_manager.set_user_state(user, 'account_selected', {'current_account': name})
        acc = self.account_manager.get_account(name)
        if not acc:
            if hasattr(query_or_message, 'edit_message_text'):
                await query_or_message.edit_message_text("❌ 账户不存在")
            else:
                await query_or_message.reply_text("❌ 账户不存在")
            return

        status = "🟢 自动投注中" if acc.auto_betting else "⚪ 未投注"
        if acc.prediction_broadcast:
            status += " | 📊 播报中"
        if acc.broadcast_stop_requested:
            status += " | ⏳ 停止中"
        if acc.recommend_mode:
            status += f" | 💰 推荐({acc.risk_profile})"
        if acc.chase_enabled:
            status += f" | 🔢 追{acc.chase_current}/{acc.chase_periods}"

        bet_button = "🛑 停止自动投注" if acc.auto_betting else "🤖 开启自动投注"
        pred_button = "🛑 停止播报" if acc.prediction_broadcast else "📊 开启播报"
        if acc.broadcast_stop_requested:
            pred_button = "⏳ 停止请求中"

        content_type = "双组" if acc.prediction_content == "double" else "杀组"
        base_amount_text = "一键推荐模式" if acc.recommend_mode else f"{acc.bet_params.base_amount} KK"

        kb = [
            [InlineKeyboardButton("💬 游戏群", callback_data=f"action:setgroup:{name}"),
             InlineKeyboardButton("📢 播报群", callback_data=f"action:setpredgroup:{name}")],
            [InlineKeyboardButton("🎯 投注方案", callback_data=f"set_scheme:{name}:select"),
             InlineKeyboardButton("📈 金额策略", callback_data=f"set_strategy:{name}:select")],
            [InlineKeyboardButton("💰 金额设置", callback_data=f"amount_menu:{name}"),
             InlineKeyboardButton("🔢 设置追号", callback_data=f"action:setchaze:{name}")],
            [InlineKeyboardButton(f"🎛️ 播报内容({content_type})", callback_data=f"toggle_content:{name}")],
            [InlineKeyboardButton(bet_button, callback_data=f"action:toggle_bet:{name}"),
             InlineKeyboardButton(pred_button, callback_data=f"action:toggle_pred:{name}")],
            [InlineKeyboardButton("📊 账户统计", callback_data=f"action:status:{name}"),
             InlineKeyboardButton("📊 连输连赢", callback_data=f"action:streak:{name}")],
        ]
        
        if acc.chase_enabled:
            kb.insert(4, [InlineKeyboardButton("🛑 停止追号", callback_data=f"action:stopchase:{name}")])
        
        kb.append([InlineKeyboardButton("🔙 返回", callback_data="menu:accounts")])

        text = f"📱 *账户: {acc.get_display_name()}*\n\n状态: {status}\n基础金额: {base_amount_text}\n净盈利: {acc.total_wins * 2 - acc.total_bets:.0f}K\n\n选择操作:"

        if hasattr(query_or_message, 'edit_message_text'):
            try:
                await query_or_message.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
            except BadRequest as e:
                if "Message is not modified" not in str(e):
                    raise
        else:
            await query_or_message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

    async def _show_amount_menu(self, query, user, name):
        acc = self.account_manager.get_account(name)
        if not acc:
            await query.edit_message_text("❌ 账户不存在")
            return

        base_amount_text = "推荐模式" if acc.recommend_mode else f"{acc.bet_params.base_amount} KK"
        recommend_button = "🛑 停止推荐" if acc.recommend_mode else "🤖 一键推荐"

        text = f"""
💰 *金额设置*

📱 账户: {acc.get_display_name()}

当前设置:
• 基础金额: {base_amount_text}
• 最大金额: {acc.bet_params.max_amount} KK
• 停止余额: {acc.bet_params.stop_balance} KK
• 止损金额: {acc.bet_params.stop_loss} KK
• 止盈金额: {acc.bet_params.stop_win} KK
• 恢复余额: {acc.bet_params.resume_balance} KK
        """
        kb = [
            [InlineKeyboardButton("💰 基础金额", callback_data=f"amount_set:base_amount:{name}"),
             InlineKeyboardButton("💎 最大金额", callback_data=f"amount_set:max_amount:{name}")],
            [InlineKeyboardButton("🛑 停止余额", callback_data=f"amount_set:stop_balance:{name}"),
             InlineKeyboardButton("⛔ 止损金额", callback_data=f"amount_set:stop_loss:{name}")],
            [InlineKeyboardButton("✅ 止盈金额", callback_data=f"amount_set:stop_win:{name}"),
             InlineKeyboardButton("🔄 恢复余额", callback_data=f"amount_set:resume_balance:{name}")],
            [InlineKeyboardButton(recommend_button, callback_data=f"amount_recommend:{name}")],
            [InlineKeyboardButton("🔙 返回", callback_data=f"select_account:{name}")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

    async def _toggle_recommend_mode(self, query, user, name):
        acc = self.account_manager.get_account(name)
        if not acc:
            await query.edit_message_text("❌ 账户不存在")
            return

        new_mode = not acc.recommend_mode
        await self.account_manager.update_account(name, recommend_mode=new_mode)
        
        if new_mode:
            await query.edit_message_text("✅ 已启用推荐模式，投注金额将根据余额动态调整。")
        else:
            await query.edit_message_text("✅ 已退出推荐模式，恢复手动设置")
        
        await self._show_amount_menu(query, user, name)

    async def _toggle_prediction_content(self, query, user, name):
        acc = self.account_manager.get_account(name)
        if not acc:
            await query.edit_message_text("❌ 账户不存在")
            return
        new_content = "kill" if acc.prediction_content == "double" else "double"
        await self.account_manager.update_account(name, prediction_content=new_content)
        await query.edit_message_text(f"✅ 播报内容已切换为 {'杀组' if new_content=='kill' else '双组'}")
        await self._show_account_detail(query, user, name)

    async def _clear_streak_records(self, query, user, name):
        await self.account_manager.update_account(name, streak_records_double=[], streak_records_kill=[])
        await query.edit_message_text("✅ 所有连输连赢记录已删除")
        await self._show_account_detail(query, user, name)

    async def _process_action(self, query, user, action, name):
        if not name:
            await query.edit_message_text("❌ 账户不存在")
            return

        if action == "toggle_bet":
            acc = self.account_manager.get_account(name)
            if acc.auto_betting:
                await self.account_manager.update_account(name, auto_betting=False)
                await query.edit_message_text("✅ 自动投注已关闭")
            else:
                await self.account_manager.update_account(name, auto_betting=True)
                await query.edit_message_text("✅ 自动投注已开启")
            await self._show_account_detail(query, user, name)
        
        elif action == "toggle_pred":
            acc = self.account_manager.get_account(name)
            if acc.prediction_broadcast:
                await self.prediction_broadcaster.stop_broadcast(name, user)
                await query.edit_message_text("✅ 播报器已停止")
            else:
                ok, msg = await self.prediction_broadcaster.start_broadcast(name, user)
                await query.edit_message_text(f"{'✅' if ok else '❌'} {msg}")
            await self._show_account_detail(query, user, name)
        
        elif action == "stopchase":
            await self.account_manager.update_account(
                name,
                chase_enabled=False,
                chase_stop_reason="手动停止",
                chase_numbers=[],
                chase_periods=0,
                chase_current=0,
                chase_amount=0
            )
            await query.edit_message_text("✅ 追号已停止")
            await self._show_account_detail(query, user, name)
        
        elif action == "setgroup":
            await query.edit_message_text(
                "💬 *设置游戏群*\n\n"
                "请将Bot添加到您的游戏群中，然后在此输入群ID。\n\n"
                "如何获取群ID？\n"
                "1. 将Bot添加到群组\n"
                "2. 在群中发送任意消息\n"
                "3. 使用 @userinfobot 获取群ID\n\n"
                "请输入群ID（负数）：",
                parse_mode='Markdown'
            )
            context = query.message
            # 等待用户输入群ID
            # 这里简化处理，实际应该用ConversationHandler
            await query.message.reply_text("⚠️ 请先获取群ID，然后使用 /setgroup {name} {group_id} 命令设置")
        
        elif action == "setpredgroup":
            await query.message.reply_text(
                "⚠️ 请使用 /setpredgroup {name} {group_id} 命令设置播报群"
            )
        
        elif action == "status":
            await self._show_account_status(query, name)
        
        elif action == "streak":
            await self._show_streak_records(query, name)

    async def _show_account_status(self, query, name):
        acc = self.account_manager.get_account(name)
        if not acc:
            await query.edit_message_text("❌ 账户不存在")
            return

        params = acc.bet_params
        net = acc.total_wins * 2 - acc.total_bets

        text = f"""
📱 *账户状态 - {acc.get_display_name()}*

*投注设置:*
• 策略: {acc.betting_strategy}
• 方案: {acc.betting_scheme}
• 基础金额: {params.base_amount} KK
• 最大金额: {params.max_amount} KK
• 止损: {params.stop_loss} KK
• 止盈: {params.stop_win} KK

*统计:*
• 总投注: {acc.total_bets}
• 命中次数: {acc.total_wins}
• 净盈利: {net}K
• 连赢: {acc.consecutive_wins} | 连输: {acc.consecutive_losses}
        """
        if acc.chase_enabled:
            text += f"""
*追号状态:*
• 数字: {', '.join(map(str, acc.chase_numbers))}
• 进度: {acc.chase_current}/{acc.chase_periods}
• 每注: {acc.chase_amount if acc.chase_amount>0 else '基础'}KK
        """
        
        kb = [[InlineKeyboardButton("🔙 返回", callback_data=f"select_account:{name}")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

    async def _show_streak_records(self, query, name):
        acc = self.account_manager.get_account(name)
        if not acc:
            await query.edit_message_text("❌ 账户不存在")
            return

        records_double = acc.streak_records_double
        records_kill = acc.streak_records_kill

        text = f"📊 *连输连赢记录 - {acc.get_display_name()}*\n\n"

        if records_double:
            text += "**双组记录:**\n"
            for r in records_double[-10:]:
                type_str = "✅ 连赢" if r.get('type') == 'win' else "❌ 连输"
                text += f"• {type_str} {r.get('count')}期\n"
        
        if records_kill:
            text += "\n**杀组记录:**\n"
            for r in records_kill[-10:]:
                type_str = "✅ 连赢" if r.get('type') == 'win' else "❌ 连输"
                text += f"• {type_str} {r.get('count')}期\n"

        if not records_double and not records_kill:
            text += "暂无记录"

        kb = [
            [InlineKeyboardButton("🗑️ 删除所有", callback_data=f"clear_streak:{name}")],
            [InlineKeyboardButton("🔙 返回", callback_data=f"select_account:{name}")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

    async def _process_set_strategy(self, query, user, name, strategy):
        if strategy == "select":
            kb = []
            for s in ['保守', '平衡', '激进', '马丁格尔', '斐波那契']:
                kb.append([InlineKeyboardButton(s, callback_data=f"set_strategy:{name}:{s}")])
            kb.append([InlineKeyboardButton("🔙 返回", callback_data=f"select_account:{name}")])
            await query.edit_message_text("📊 *选择投注策略:*", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
            return

        # 应用策略配置
        strategy_config = {
            '保守': {'base_amount': 10000, 'max_amount': 100000, 'multiplier': 1.5},
            '平衡': {'base_amount': 50000, 'max_amount': 500000, 'multiplier': 2.0},
            '激进': {'base_amount': 100000, 'max_amount': 1000000, 'multiplier': 2.5},
            '马丁格尔': {'base_amount': 10000, 'max_amount': 10000000, 'multiplier': 2.0},
            '斐波那契': {'base_amount': 10000, 'max_amount': 10000000, 'multiplier': 1.0},
        }
        cfg = strategy_config.get(strategy, strategy_config['平衡'])
        
        await self.account_manager.update_account(
            name,
            betting_strategy=strategy,
            bet_params={
                'base_amount': cfg['base_amount'],
                'max_amount': cfg['max_amount'],
                'multiplier': cfg['multiplier'],
            }
        )
        if strategy in ['保守', '平衡', '激进']:
            await self.account_manager.update_account(name, risk_profile=strategy)
        
        await query.edit_message_text(f"✅ 已设置为 {strategy} 策略")
        await self._show_account_detail(query, user, name)

    async def _process_set_scheme(self, query, user, name, scheme):
        if scheme == "select":
            kb = []
            for s in ['组合1', '组合2', '组合1+2', '杀主']:
                kb.append([InlineKeyboardButton(s, callback_data=f"set_scheme:{name}:{s}")])
            kb.append([InlineKeyboardButton("🔙 返回", callback_data=f"select_account:{name}")])
            await query.edit_message_text("🎯 *选择投注方案:*", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
            return

        await self.account_manager.update_account(name, betting_scheme=scheme)
        await query.edit_message_text(f"✅ 投注方案已设置为: {scheme}")
        await self._show_account_detail(query, user, name)

    async def _show_prediction_menu(self, query):
        kb = [
            [InlineKeyboardButton("🔮 运行预测", callback_data="run_analysis")],
            [InlineKeyboardButton("🔙 返回", callback_data="menu:main")]
        ]
        await query.edit_message_text("🎯 *预测分析菜单*", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

    async def _process_run_analysis(self, query):
        await query.edit_message_text("🔍 正在生成预测...")
        history = await self.api.get_history(50)
        if len(history) < 3:
            await query.edit_message_text("❌ 历史数据不足，至少需要3期数据")
            return
        latest = history[0]
        keno_latest = await self.api.get_latest_keno()
        if keno_latest and self.model:
            self.model.keno_similarity.current_keno_nbrs = keno_latest['nbrs']
        
        if self.model:
            pred = self.model.predict(history, latest)
            text = f"""
🎯 *Canada28预测结果*

📊 *数据信息：*
• 最新期号: {latest.get('qihao', 'N/A')}
• 最新结果: {latest.get('sum', 'N/A')} ({latest.get('combo', 'N/A')})

🏆 *推荐预测：*
• 主推: {pred['main']}
• 候选: {pred['candidate']}
• 杀组: {pred['kill']}
• 置信度: {pred['confidence']}%
            """
        else:
            text = "❌ 模型未初始化，请稍后再试"
        
        kb = [[InlineKeyboardButton("🔄 刷新预测", callback_data="run_analysis")],
              [InlineKeyboardButton("🔙 返回", callback_data="menu:prediction")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

    async def _show_status_menu(self, query):
        api_stats = self.api.get_statistics() if hasattr(self.api, 'get_statistics') else {}
        sched_stats = self.bet_executor.get_stats()
        total_accounts = len(self.account_manager.accounts)
        auto = sched_stats['auto_betting_accounts']
        broadcast = sched_stats['broadcast_accounts']
        
        total_bets = sum(a.total_bets for a in self.account_manager.accounts.values())
        total_wins = sum(a.total_wins for a in self.account_manager.accounts.values())
        net = total_wins * 2 - total_bets

        text = f"""
📊 *系统状态*

*数据状态*
• 缓存数据: {api_stats.get('缓存数据量', 'N/A')}期
• 最新期号: {api_stats.get('最新期号', 'N/A')}

*账户状态*
• 总账户: {total_accounts}
• 自动投注: {auto}
• 预测播报: {broadcast}

*统计*
• 总投注: {total_bets}
• 总命中: {total_wins}
• 净盈利: {net}K
        """
        kb = [[InlineKeyboardButton("🔄 刷新", callback_data="refresh_status")],
              [InlineKeyboardButton("🔙 返回", callback_data="menu:main")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

    async def _show_help_menu(self, query):
        text = """
📚 *帮助菜单*

所有操作均可通过菜单按钮完成。

*快速开始:*
1. 添加账户：在“账户管理”中点击“➕ 添加账户”，输入账户名称
2. 设置游戏群：将Bot添加到游戏群，在账户详情中点击“💬 游戏群”并输入群ID
3. 设置播报群：同上，点击“📢 播报群”
4. 设置投注策略：点击“📈 金额策略”选择策略
5. 开启自动投注：点击“🤖 开启自动投注”

*获取群ID:*
1. 将 @userinfobot 添加到群组
2. 发送 /start
3. 机器人会返回群ID

*手动投注:*
在游戏群发送 `类型 金额`，如 `大 10000`

*常用命令:*
/start - 显示主菜单
/cancel - 取消当前操作
        """
        kb = [[InlineKeyboardButton("🔙 返回", callback_data="menu:main")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')


# ==================== 启动 ====================
async def post_init(application: Application):
    bot = application.bot_data.get('bot')
    if bot:
        await bot.account_manager.start_periodic_save()
        if hasattr(bot, 'global_scheduler'):
            await bot.global_scheduler.start()
    logger.log_system("Bot 初始化完成，已启动调度器")


def main():
    def handle_shutdown(signum, frame):
        print("\n🛑 接收到停止信号，正在优雅关闭...")
        if 'bot' in globals():
            try:
                loop = asyncio.get_running_loop()
                loop.call_soon_threadsafe(lambda: asyncio.create_task(bot.global_scheduler.stop()))
                loop.call_soon_threadsafe(lambda: asyncio.create_task(bot.account_manager.stop_periodic_save()))
                loop.call_soon_threadsafe(lambda: asyncio.create_task(bot.api.close()))
            except RuntimeError:
                asyncio.run(bot.global_scheduler.stop())
                asyncio.run(bot.account_manager.stop_periodic_save())
                asyncio.run(bot.api.close())
        print("✅ 已安全关闭")
        exit(0)

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    print("""
========================================
PC28 智能预测投注系统（Bot直发版）
========================================
启动中...
    """)
    try:
        Config.validate()
    except ValueError as e:
        print(f"❌ 配置错误: {e}")
        return
    
    bot = PC28Bot()
    bot.application.bot_data['bot'] = bot
    bot.application.post_init = post_init
    
    print("✅ Bot已启动")
    print("ℹ️ 使用 /start 开始使用")
    print("ℹ️ 将Bot添加到游戏群后，Bot会自动发送投注消息")
    
    bot.application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    random.seed(time.time())
    np.random.seed(int(time.time()))
    main()