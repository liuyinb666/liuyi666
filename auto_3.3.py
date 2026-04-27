#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PC28 智能预测投注系统 - 增强稳定性和准确率版
基于双杀组+双Y融合算法 | 多算法投票 | 交叉验证 | 历史准确率反馈
"""

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
from typing import Optional, Dict, List, Any, Tuple, Union, Set
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
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, FloodWaitError

# ==================== 配置 ====================
class Config:
    BOT_TOKEN = os.environ.get('BOT_TOKEN', '')
    API_ID = int(os.environ.get('API_ID', 0))
    API_HASH = os.environ.get('API_HASH', '')
    PC28_API_BASE = "https://www.pc28.help/api/kj.json?nbr=200"
    ADMIN_USER_IDS = [7673012566]
    
    # 硅基流动API配置
    SILICONFLOW_API_KEY = os.environ.get('SILICONFLOW_API_KEY', 'sk-vipzurajvbmxqdnqffipqcfvfuquklhyudcwarjhqyitjpcp')
    SILICONFLOW_MODEL = "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B"
    SILICONFLOW_BACKUP_API_KEY = os.environ.get('SILICONFLOW_BACKUP_API_KEY', '')
    
    DATA_DIR = Path("data")
    SESSIONS_DIR = DATA_DIR / "sessions"
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
    BALANCE_BOT = "kkpayPc28Bot"
    REQUEST_TIMEOUT = 15
    MAX_RETRIES = 3
    RETRY_BACKOFF = 2
    MAX_HISTORY = 61
    GAME_CYCLE_SECONDS = 210
    CLOSE_BEFORE_SECONDS = 50
    MANUAL_LINK = "https://t.me/yugejnd/9"
    SCHEDULER_CHECK_INTERVAL = 5
    HEALTH_CHECK_INTERVAL = 60
    EXPLORATION_RATE = 0.03
    EXPLORATION_MIN = 0.005
    EXPLORATION_DECAY = 0.95
    NOISE_SCALE = 0.05
    MODEL_SAVE_FILE = "pc28_model.json"
    BALANCE_CACHE_SECONDS = 60
    MAX_CONCURRENT_BETS = 3
    LOG_RETENTION_DAYS = 7
    ACCOUNT_SAVE_INTERVAL = 30
    MAX_CONCURRENT_PREDICTIONS = 1
    LOGIN_SELECT, LOGIN_CODE, LOGIN_PASSWORD = range(3)
    ADD_ACCOUNT = 10
    CHASE_NUMBERS, CHASE_PERIODS, CHASE_AMOUNT = range(11, 14)
    MAX_ACCOUNTS_PER_USER = 5
    KJ_HISTORY_DOWNLOAD = 1000

    @classmethod
    def init_dirs(cls):
        cls.DATA_DIR.mkdir(exist_ok=True)
        cls.SESSIONS_DIR.mkdir(exist_ok=True)
        cls.LOGS_DIR.mkdir(exist_ok=True)
        cls.CACHE_DIR.mkdir(exist_ok=True)

    @classmethod
    def validate(cls):
        errors = []
        if not cls.BOT_TOKEN: errors.append("BOT_TOKEN未配置")
        if cls.API_ID <= 0: errors.append("API_ID必须为正整数")
        if not cls.API_HASH: errors.append("API_HASH未配置")
        if not cls.PC28_API_BASE.startswith(('http://', 'https://')): errors.append("PC28_API_BASE必须是有效的URL")
        if cls.MIN_BET_AMOUNT < 0: errors.append("最小投注金额不能为负数")
        if cls.MAX_BET_AMOUNT <= cls.MIN_BET_AMOUNT: errors.append("最大投注金额必须大于最小投注金额")
        if cls.MAX_CONCURRENT_BETS < 1: errors.append("并发投注数至少为1")
        if errors: raise ValueError("配置验证失败: " + ", ".join(errors))
        return True

Config.init_dirs()

# ==================== 工具函数 ====================
def increment_qihao(current_qihao: str) -> str:
    if not current_qihao: return "1"
    match = re.search(r'(\d+)$', current_qihao)
    if match:
        num_part = match.group(1)
        prefix = current_qihao[:match.start()]
        try:
            next_num = str(int(num_part) + 1).zfill(len(num_part))
            return prefix + next_num
        except: return current_qihao + "1"
    else:
        try: return str(int(current_qihao) + 1)
        except: return current_qihao + "1"

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
        if hasattr(record, 'betting') and record.betting: self._style._fmt = self.FORMATS['BETTING']
        elif hasattr(record, 'prediction') and record.prediction: self._style._fmt = self.FORMATS['PREDICTION']
        else: self._style._fmt = self.FORMATS.get(record.levelno, self.grey + "%(asctime)s [%(levelname)s] %(message)s" + self.reset)
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
                if (now - file_date).days > Config.LOG_RETENTION_DAYS: f.unlink()
            except: pass

    def log_system(self, msg): self.logger.info(f"[系统] {msg}")
    def log_account(self, user_id, phone, action): self.logger.info(f"[账户] 用户:{user_id} 手机:{self._mask_phone(phone)} {action}")
    def log_game(self, msg): self.logger.info(f"[游戏] {msg}")
    def log_betting(self, user_id, action, detail):
        extra = {'betting': True}; self.logger.info(f"用户:{user_id} {action} {detail}", extra=extra)
    def log_prediction(self, user_id, action, detail):
        extra = {'prediction': True}; self.logger.info(f"用户:{user_id} {action} {detail}", extra=extra)
    def log_analysis(self, msg): self.logger.debug(f"[分析] {msg}")
    def log_error(self, user_id, action, error):
        error_trace = traceback.format_exc(); self.logger.error(f"[错误] 用户:{user_id} {action}: {error}\n{error_trace}")
    def log_api(self, action, detail): self.logger.debug(f"[API] {action} {detail}")
    def log_heartbeat(self): self.logger.info("[心跳] 系统运行正常")
    def _mask_phone(self, phone: str) -> str:
        if len(phone) >= 8: return phone[:5] + "****" + phone[-3:]
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

# ==================== PC28规则预测器（增强稳定性和准确率版） ====================
class PC28RulePredictor:
    """
    增强版PC28预测器
    - 多算法投票杀组
    - 历史准确率反馈
    - 交叉验证
    - 预测平滑
    - 置信度动态校准
    """
    
    def __init__(self):
        self.combos = COMBOS
        self.size_map = {"小": "大", "大": "小"}
        self.parity_map = {"单": "双", "双": "单"}
        
        # 3Y池取数顺序映射
        self.order_3y_map = {
            0: ['3Y0', '3Y1', '3Y2'],
            1: ['3Y1', '3Y2', '3Y0'],
            2: ['3Y2', '3Y0', '3Y1'],
        }
        
        # 5Y特码池
        self.pool_5y = {
            0: [0, 5, 10, 15, 20, 25],
            1: [1, 6, 11, 16, 21],
            2: [2, 7, 12, 17, 22],
            3: [3, 8, 13, 18, 23],
            4: [4, 9, 14, 19, 24],
        }
        
        # 3Y特码池
        self.pool_3y = {
            0: [0, 3, 6, 9, 12, 15, 18, 21, 24, 27],
            1: [1, 4, 7, 10, 13, 16, 19, 22, 25],
            2: [2, 5, 8, 11, 14, 17, 20, 23, 26],
        }
        
        # 高频防组合加权
        self.high_freq_combos = ["小单", "小双", "大双"]
        
        # 历史准确率跟踪
        self.prediction_history = deque(maxlen=100)
        self.algorithm_accuracy = {'kill': 0.5, 'main': 0.5, 'double': 0.5}
        self.confidence_calibration = 1.0
        
        # 预测平滑
        self.last_prediction = None
        self.last_prediction_qihao = None
        
        # 多算法权重
        self.algo_weights = {
            'kill_algo1': 0.35,
            'kill_algo2': 0.35,
            'pattern_algo': 0.15,
            'trend_algo': 0.15,
        }
        
        # 加载缓存
        self._load_accuracy_cache()
    
    def _load_accuracy_cache(self):
        try:
            accuracy_file = Config.CACHE_DIR / "predictor_accuracy.json"
            if accuracy_file.exists():
                with open(accuracy_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.algorithm_accuracy = data.get('algorithm_accuracy', {'kill': 0.5, 'main': 0.5, 'double': 0.5})
                    self.confidence_calibration = data.get('confidence_calibration', 1.0)
                    logger.log_system(f"加载预测器准确率缓存完成")
        except Exception as e:
            logger.log_error(0, "加载准确率缓存失败", e)
    
    def _save_accuracy_cache(self):
        try:
            accuracy_file = Config.CACHE_DIR / "predictor_accuracy.json"
            data = {
                'algorithm_accuracy': self.algorithm_accuracy,
                'confidence_calibration': self.confidence_calibration,
                'last_save': datetime.now().isoformat()
            }
            with open(accuracy_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.log_error(0, "保存准确率缓存失败", e)
    
    def record_prediction_result(self, prediction: Dict, actual: str, actual_sum: int):
        """记录预测结果用于准确率反馈"""
        is_kill_correct = (prediction.get('kill') != actual)
        is_main_correct = (actual in prediction.get('main', []))
        is_candidate_correct = (actual == prediction.get('candidate'))
        
        record = {
            'qihao': prediction.get('qihao'),
            'time': datetime.now().isoformat(),
            'kill': prediction.get('kill'),
            'kill_correct': is_kill_correct,
            'main': prediction.get('main', []),
            'main_correct': is_main_correct,
            'candidate': prediction.get('candidate'),
            'candidate_correct': is_candidate_correct,
            'actual': actual,
            'actual_sum': actual_sum,
            'confidence': prediction.get('confidence', 50)
        }
        self.prediction_history.append(record)
        self._update_algorithm_accuracy()
        
        if len(self.prediction_history) % 10 == 0:
            self._save_accuracy_cache()
    
    def _update_algorithm_accuracy(self):
        """更新各算法的历史准确率"""
        if len(self.prediction_history) < 10:
            return
        
        recent = list(self.prediction_history)[-30:]
        
        kill_correct = sum(1 for r in recent if r.get('kill_correct', False))
        self.algorithm_accuracy['kill'] = kill_correct / len(recent) if recent else 0.5
        
        main_correct = sum(1 for r in recent if r.get('main_correct', False))
        self.algorithm_accuracy['main'] = main_correct / len(recent) if recent else 0.5
        
        double_correct = sum(1 for r in recent if r.get('main_correct', False) or r.get('candidate_correct', False))
        self.algorithm_accuracy['double'] = double_correct / len(recent) if recent else 0.5
        
        # 动态调整置信度校准
        if len(self.prediction_history) >= 30:
            long_term_acc = sum(1 for r in list(self.prediction_history)[-30:] if r.get('main_correct', False)) / 30
            if long_term_acc < 0.45:
                self.confidence_calibration = max(0.7, self.confidence_calibration * 0.99)
            elif long_term_acc > 0.65:
                self.confidence_calibration = min(1.15, self.confidence_calibration * 1.005)
        
        logger.log_analysis(f"准确率统计 - 杀组:{self.algorithm_accuracy['kill']:.1%} "
                           f"主推:{self.algorithm_accuracy['main']:.1%} "
                           f"双组合:{self.algorithm_accuracy['double']:.1%}")
    
    def _calc_y_value(self, a: int, b: int, c: int, total: int) -> int:
        concat_num = a * 100 + b * 10 + c
        new_num = concat_num + total
        return sum(int(d) for d in str(new_num))
    
    def _calc_3y(self, total: int) -> int:
        return total % 3
    
    def _calc_5y(self, total: int) -> int:
        return total % 5
    
    def _calc_kill_by_algo1(self, latest: Dict, history_10: List[Dict]) -> Tuple[Optional[str], int]:
        """子算法1：基于Y值和位差和判定杀组"""
        try:
            a1 = latest.get('a', 0)
            b1 = latest.get('b', 0)
            c1 = latest.get('c', 0)
            h1 = latest.get('sum', 0)
            
            y1 = self._calc_y_value(a1, b1, c1, h1)
            
            target_idx = -1
            match_conf = 70
            for i, h in enumerate(history_10):
                h_y = h.get('y_value')
                if h_y is not None:
                    if h_y == y1:
                        target_idx = i
                        match_conf = 85
                        break
                    elif abs(h_y - y1) <= 1:
                        target_idx = i
                        match_conf = 70
                        break
            
            if target_idx == -1:
                return None, 0
            
            target = history_10[target_idx]
            a2 = target.get('a', 0)
            b2 = target.get('b', 0)
            c2 = target.get('c', 0)
            
            s = abs(a1 - a2) + abs(b1 - b2) + abs(c1 - c2)
            size = "小" if s < 14 else "大"
            parity = "单" if s % 2 == 1 else "双"
            
            return size + parity, match_conf
        except Exception as e:
            logger.log_error(0, "子算法1失败", e)
            return None, 0
    
    def _calc_kill_by_algo2(self, latest: Dict) -> Tuple[Optional[str], int]:
        """子算法2：基于和值运算判定杀组"""
        try:
            h1 = latest.get('sum', 0)
            a1 = latest.get('a', 0)
            
            step1_raw = h1 * 3 * h1
            step1_str = str(step1_raw)[-3:].zfill(3)
            d2 = sum(int(d) for d in step1_str)
            
            s = d2 + a1
            if s > 27:
                s = s - 27
            
            size = "小" if s < 14 else "大"
            parity = "单" if s % 2 == 1 else "双"
            temp_combo = size + parity
            
            opposite_size = self.size_map[size]
            opposite_parity = self.parity_map[parity]
            
            if s == 13 or s == 14:
                conf = 60
            elif s <= 5 or s >= 22:
                conf = 85
            else:
                conf = 75
            
            return opposite_size + opposite_parity, conf
        except Exception as e:
            logger.log_error(0, "子算法2失败", e)
            return None, 0
    
    def _calc_kill_by_pattern(self, history_10: List[Dict]) -> Tuple[Optional[str], int]:
        """子算法3：基于模式识别的杀组"""
        if len(history_10) < 15:
            return None, 0
        
        combos = [h.get('combo') for h in history_10[:15] if h.get('combo')]
        if not combos:
            return None, 0
        
        freq = Counter(combos)
        hot_combo = max(freq, key=freq.get)
        return hot_combo, 55
    
    def _calc_kill_by_trend(self, history_10: List[Dict]) -> Tuple[Optional[str], int]:
        """子算法4：基于趋势分析的杀组"""
        if len(history_10) < 10:
            return None, 0
        
        combos = [h.get('combo') for h in history_10[:10] if h.get('combo')]
        if not combos:
            return None, 0
        
        # 检测连开趋势
        streak = 1
        for i in range(1, len(combos)):
            if combos[i] == combos[0]:
                streak += 1
            else:
                break
        
        if streak >= 3:
            # 连开3期以上，杀该组合
            return combos[0], 70
        elif streak == 2:
            # 连开2期，杀该组合概率中等
            return combos[0], 55
        
        return None, 0
    
    def _get_kill_by_vote(self, latest: Dict, history_10: List[Dict]) -> Tuple[str, int]:
        """多算法投票杀组"""
        kill_results = []
        
        kill1, conf1 = self._calc_kill_by_algo1(latest, history_10)
        if kill1:
            kill_results.append({'kill': kill1, 'conf': conf1, 'weight': self.algo_weights['kill_algo1']})
        
        kill2, conf2 = self._calc_kill_by_algo2(latest)
        if kill2:
            kill_results.append({'kill': kill2, 'conf': conf2, 'weight': self.algo_weights['kill_algo2']})
        
        kill3, conf3 = self._calc_kill_by_pattern(history_10)
        if kill3:
            kill_results.append({'kill': kill3, 'conf': conf3, 'weight': self.algo_weights['pattern_algo']})
        
        kill4, conf4 = self._calc_kill_by_trend(history_10)
        if kill4:
            kill_results.append({'kill': kill4, 'conf': conf4, 'weight': self.algo_weights['trend_algo']})
        
        if not kill_results:
            return random.choice(self.combos), 50
        
        vote_scores = {c: 0 for c in self.combos}
        for result in kill_results:
            kill = result['kill']
            weight = result.get('weight', 0.25)
            conf = result.get('conf', 60) / 100
            vote_scores[kill] += weight * conf
        
        final_kill = max(vote_scores, key=vote_scores.get)
        final_conf = int(min(92, vote_scores[final_kill] * 100 + 10))
        
        unique_kills = set(r['kill'] for r in kill_results)
        if len(unique_kills) == 1:
            final_conf = min(92, final_conf + 8)
        
        return final_kill, final_conf
    
    def _calculate_tail_numbers(self, history_10: List[Dict], latest: Dict) -> Tuple[List[int], str, int]:
        """双Y融合算法 - 尾数计算"""
        h1 = latest.get('sum', 0)
        y3 = self._calc_3y(h1)
        
        order = self.order_3y_map.get(y3, ['3Y0', '3Y1', '3Y2'])
        
        pools_3y = {'3Y0': [], '3Y1': [], '3Y2': []}
        for h in history_10[:10]:
            total = h.get('sum', 0)
            y3_val = self._calc_3y(total)
            pool_key = f'3Y{y3_val}'
            pools_3y[pool_key].append(h)
        
        ball_map = {'3Y0': 'a', '3Y1': 'b', '3Y2': 'c'}
        
        tail_sums = []
        data_sufficient = True
        
        for pool_key in order:
            pool = pools_3y.get(pool_key, [])
            ball = ball_map.get(pool_key, 'a')
            
            if len(pool) >= 3:
                values = [h.get(ball, 0) for h in pool[:3]]
                tail_sum = sum(values) % 10
            elif len(pool) >= 1:
                values = [h.get(ball, 0) for h in pool]
                tail_sum = int((sum(values) / len(values)) % 10)
                data_sufficient = False
            else:
                tail_sum = 0
                data_sufficient = False
            tail_sums.append(tail_sum)
        
        h2 = sum(tail_sums) % 10
        
        base_size = "小" if h2 < 14 else "大"
        base_parity = "单" if h2 % 2 == 1 else "双"
        base_combo = base_size + base_parity
        
        confidence = 80 if data_sufficient else 60
        
        return tail_sums, base_combo, confidence
    
    def _calculate_scores(self, history_10: List[Dict], base_combo: str, rule_kill: str) -> Dict[str, Dict]:
        """双权重打分系统"""
        combo_count = {c: 0 for c in self.combos}
        combo_sequence = []
        
        for h in history_10[:10]:
            combo = h.get('combo', '')
            if combo in combo_count:
                combo_count[combo] += 1
                combo_sequence.append(combo)
        
        # 趋势分析
        streak_combos = {}
        if len(combo_sequence) >= 3:
            current_streak = 1
            current_combo = combo_sequence[0]
            for i in range(1, len(combo_sequence)):
                if combo_sequence[i] == current_combo:
                    current_streak += 1
                else:
                    if current_streak >= 2:
                        streak_combos[current_combo] = max(streak_combos.get(current_combo, 0), current_streak)
                    current_combo = combo_sequence[i]
                    current_streak = 1
            if current_streak >= 2:
                streak_combos[current_combo] = max(streak_combos.get(current_combo, 0), current_streak)
        
        scores = {}
        for combo in self.combos:
            if combo == rule_kill:
                scores[combo] = {'score': 0, 'tail_score': 0, 'freq_score': 0, 'trend_score': 0, 'is_kill': True}
                continue
            
            if combo == base_combo:
                tail_score = 40
            elif combo[0] == base_combo[0] or combo[1] == base_combo[1]:
                tail_score = 30
            else:
                tail_score = 10
            
            count = combo_count.get(combo, 0)
            if count >= 3:
                freq_score = 50
            elif count >= 1:
                freq_score = 35
            else:
                freq_score = 15
            
            trend_score = 0
            if combo in streak_combos:
                streak_len = streak_combos[combo]
                if streak_len >= 3:
                    trend_score = 10
                elif streak_len == 2:
                    trend_score = 5
            else:
                if count == 0:
                    trend_score = 5
            
            if combo in self.high_freq_combos:
                freq_score += 10
            
            total_score = tail_score + freq_score + trend_score
            
            scores[combo] = {
                'score': total_score,
                'tail_score': tail_score,
                'freq_score': freq_score,
                'trend_score': trend_score,
                'count': count,
                'is_kill': False
            }
        
        return scores
    
    def _cross_validate(self, main_combos: List[str], candidate_combo: str, kill_combo: str, 
                        history_10: List[Dict]) -> Tuple[List[str], str, str, int]:
        """交叉验证"""
        if len(history_10) < 15:
            return main_combos, candidate_combo, kill_combo, 0
        
        test_data = history_10[:15]
        correct = 0
        total = 0
        
        for i in range(10, len(test_data)):
            train = test_data[:i]
            target = test_data[i]
            
            kill, _ = self._get_kill_by_vote(train[0], train[:10])
            if kill == target.get('combo'):
                correct += 1
            total += 1
        
        backtest_acc = correct / total if total > 0 else 0.5
        
        if backtest_acc < 0.35:
            logger.log_analysis(f"交叉验证准确率偏低({backtest_acc:.1%})，启用备用方案")
            freq = Counter([h.get('combo') for h in history_10[:10] if h.get('combo')])
            if freq:
                main_combos = [min(freq, key=freq.get)]
                candidate_combo = [c for c in self.combos if c != main_combos[0]][0]
                kill_combo = max(freq, key=freq.get)
        
        return main_combos, candidate_combo, kill_combo, int(backtest_acc * 100)
    
    def _apply_smoothing(self, current_main: List[str], current_candidate: str, 
                         current_kill: str) -> Tuple[List[str], str, str]:
        """预测平滑"""
        if not self.last_prediction:
            self.last_prediction = {'main': current_main, 'candidate': current_candidate, 'kill': current_kill}
            return current_main, current_candidate, current_kill
        
        last_main = self.last_prediction.get('main', [])
        last_candidate = self.last_prediction.get('candidate')
        last_kill = self.last_prediction.get('kill')
        
        smoothed_main = current_main.copy()
        smoothed_candidate = current_candidate
        smoothed_kill = current_kill
        
        # 主推平滑
        if last_main:
            for r in self.prediction_history:
                if r.get('main') == last_main and r.get('main_correct', False):
                    if last_main[0] not in current_main and len(current_main) < 2:
                        smoothed_main.append(last_main[0])
                        smoothed_main = list(set(smoothed_main))
                    break
        
        self.last_prediction = {
            'main': smoothed_main,
            'candidate': smoothed_candidate,
            'kill': smoothed_kill
        }
        
        return smoothed_main, smoothed_candidate, smoothed_kill
    
    def _get_special_numbers(self, latest: Dict, history_10: List[Dict]) -> List[int]:
        """获取特码数字"""
        h1 = latest.get('sum', 0)
        y5 = self._calc_5y(h1)
        y3 = self._calc_3y(h1)
        
        pool_5y = self.pool_5y.get(y5, [0, 5, 10, 15, 20, 25])
        pool_3y = self.pool_3y.get(y3, [])
        
        intersection = [n for n in pool_5y if n in pool_3y]
        
        sum_count = Counter()
        for h in history_10[:10]:
            s = h.get('sum', 0)
            sum_count[s] += 1
        
        remaining = []
        for num in pool_5y:
            if num not in intersection:
                remaining.append((num, sum_count.get(num, 0)))
        remaining.sort(key=lambda x: x[1], reverse=True)
        
        special_numbers = intersection.copy()
        for num, _ in remaining:
            if len(special_numbers) >= 4:
                break
            special_numbers.append(num)
        
        while len(special_numbers) < 4:
            special_numbers.append(random.randint(0, 27))
        
        return special_numbers[:4]
    
    def get_rule_based_predictions(self, history_10: List[Dict], qihao: str = None) -> Dict:
        """完整的基于规则的预测（增强稳定性和准确率版）"""
        if len(history_10) < 10:
            logger.log_analysis(f"历史数据不足10期，当前{len(history_10)}期")
            return None
        
        latest = history_10[0]
        
        # 计算Y值
        for h in history_10:
            if 'y_value' not in h and h.get('a') is not None:
                h['y_value'] = self._calc_y_value(
                    h.get('a', 0), h.get('b', 0), h.get('c', 0), h.get('sum', 0)
                )
        
        # 杀组
        rule_kill, kill_conf = self._get_kill_by_vote(latest, history_10)
        
        # 尾数计算
        tail_sums, base_combo, tail_conf = self._calculate_tail_numbers(history_10, latest)
        
        # 打分
        scores = self._calculate_scores(history_10, base_combo, rule_kill)
        
        # 排序
        sorted_combos = sorted(
            [(c, data) for c, data in scores.items() if not data.get('is_kill')],
            key=lambda x: x[1]['score'],
            reverse=True
        )
        
        # 分级
        main_combos = []
        candidate_combo = None
        
        for combo, data in sorted_combos:
            score = data['score']
            if score >= 85 and len(main_combos) < 2:
                main_combos.append(combo)
            elif 70 <= score < 85 and candidate_combo is None:
                candidate_combo = combo
        
        if len(main_combos) < 2 and candidate_combo:
            main_combos.append(candidate_combo)
            candidate_combo = None
        
        for combo, data in sorted_combos:
            if combo not in main_combos and combo != candidate_combo and len(main_combos) < 2:
                main_combos.append(combo)
        
        if not main_combos:
            main_combos = [base_combo]
        
        if not candidate_combo:
            candidate_combo = main_combos[-1] if main_combos else self.combos[0]
        
        # 去重
        if candidate_combo == main_combos[0] and len(main_combos) > 1:
            candidate_combo = main_combos[1]
        if rule_kill in main_combos:
            main_combos = [c for c in main_combos if c != rule_kill]
        if rule_kill == candidate_combo:
            candidate_combo = [c for c in self.combos if c != rule_kill and c not in main_combos][0]
        
        # 交叉验证
        main_combos, candidate_combo, rule_kill, backtest_acc = self._cross_validate(
            main_combos, candidate_combo, rule_kill, history_10
        )
        
        # 平滑
        if qihao:
            self.last_prediction_qihao = qihao
        main_combos, candidate_combo, rule_kill = self._apply_smoothing(
            main_combos, candidate_combo, rule_kill
        )
        
        # 特码
        special_numbers = self._get_special_numbers(latest, history_10)
        
        # 置信度
        main_scores = [scores.get(c, {}).get('score', 0) for c in main_combos]
        avg_main_score = sum(main_scores) / len(main_scores) if main_scores else 0
        
        base_conf = avg_main_score * 0.4 + kill_conf * 0.3 + tail_conf * 0.2
        
        if backtest_acc > 0:
            base_conf = base_conf * 0.7 + backtest_acc * 30
        
        historical_acc = self.algorithm_accuracy.get('main', 0.5)
        base_conf = base_conf * 0.6 + historical_acc * 100 * 0.4
        
        final_conf = int(min(92, max(45, base_conf * self.confidence_calibration)))
        
        # 跳开提示
        kill_freq = sum(1 for h in history_10[:10] if h.get('combo') == rule_kill)
        kill_warning = f"⚠️ 注意：杀组{rule_kill}近期出现{kill_freq}次" if kill_freq >= 2 else f"杀组{rule_kill}近期低频"
        jump_risk = f"{kill_warning}；核心圈（{','.join(main_combos)}）覆盖近期高概率方向"
        
        result = {
            'main': main_combos,
            'candidate': candidate_combo,
            'kill': rule_kill,
            'kill_confidence': kill_conf,
            'confidence': final_conf,
            'special_numbers': special_numbers,
            'jump_risk': jump_risk,
            'base_combo': base_combo,
            'tail_sums': tail_sums,
            'backtest_accuracy': backtest_acc,
            'scores': {c: data['score'] for c, data in scores.items()},
            'algo_details': [
                {"name": "多算法投票杀组", "kill": rule_kill, "confidence": kill_conf},
                {"name": "双Y融合算法", "main": main_combos, "candidate": candidate_combo},
                {"name": "5Y+3Y特码池", "numbers": special_numbers},
                {"name": "交叉验证", "accuracy": f"{backtest_acc}%"}
            ]
        }
        
        if qihao:
            result['qihao'] = qihao
        
        logger.log_prediction(0, "增强版预测完成", 
                            f"主推:{main_combos} 候选:{candidate_combo} 杀组:{rule_kill} "
                            f"置信度:{final_conf} 回测:{backtest_acc}%")
        
        return result


# ==================== 硅基流动AI客户端 ====================
class SiliconFlowAIClient:
    def __init__(self, api_key=None, model_name="deepseek-ai/DeepSeek-R1-Distill-Qwen-7B"):
        self.api_url = "https://api.siliconflow.cn/v1/chat/completions"
        self.api_key = api_key or Config.SILICONFLOW_API_KEY
        self.backup_api_key = Config.SILICONFLOW_BACKUP_API_KEY
        self.model_name = model_name
        self.timeout = aiohttp.ClientTimeout(total=30, connect=10, sock_read=25)
        self._active_requests = {}
        self._global_predict_lock = asyncio.Lock()
        self._last_prediction = None
        self._last_qihao = None
        
        self.rule_predictor = PC28RulePredictor()
    
    def _build_rule_based_prompt(self, history: List[Dict], rule_result: Dict) -> str:
        if not rule_result:
            return self._build_fallback_prompt(history)
        
        combos_10 = [h.get('combo', '') for h in history[:10] if h.get('combo')]
        sums_10 = [h.get('sum', 0) for h in history[:10] if h.get('sum') is not None]
        
        combo_count = Counter(combos_10)
        
        current_streak = 1
        if combos_10:
            for i in range(1, len(combos_10)):
                if combos_10[i] == combos_10[0]:
                    current_streak += 1
                else:
                    break
        
        prompt = f"""你是PC28彩票预测验证专家。以下是基于确定性规则算法的预测结果，请验证其合理性。

【规则算法预测结果】
- 最终杀组（必排除）：{rule_result['kill']}（置信度{rule_result['kill_confidence']}%）
- 核心主攻组合：{rule_result['main']}
- 高概率稳防组合：{rule_result['candidate']}
- 预测置信度：{rule_result['confidence']}%
- 核心特码：{rule_result['special_numbers']}
- 回测准确率：{rule_result.get('backtest_accuracy', 0)}%

【近期走势数据（最近10期）】
- 组合序列：{" → ".join(combos_10[:10])}
- 和值序列：{sums_10[:10]}
- 当前连开：{current_streak}期（{combos_10[0] if combos_10 else '无'}）
- 组合频次：{dict(combo_count)}

【验证任务】
请基于PC28开奖规律，验证上述规则预测是否合理，并输出JSON格式结果：

{{
    "validation": "合理/需调整/不合理",
    "main_confirm": [确认的主攻组合，保持原顺序],
    "candidate_confirm": "确认的稳防组合",
    "kill_confirm": "确认的杀组",
    "adjustment_reason": "调整原因（如无需调整填'无'）",
    "final_confidence": 整数置信度(40-92)
}}

注意：
- 如果杀组近期出现频次≥2次，考虑调整
- 如果主攻组合连续3期未出，考虑加强
- 如果当前连开≥3期，考虑反转
- 输出简洁，仅输出JSON"""
        
        return prompt
    
    def _build_fallback_prompt(self, history: List[Dict]) -> str:
        combos_10 = [h.get('combo', '') for h in history[:10] if h.get('combo')]
        sums_10 = [h.get('sum', 0) for h in history[:10] if h.get('sum') is not None]
        
        combo_count = Counter(combos_10)
        
        prompt = f"""你是PC28彩票预测专家。基于以下数据预测下一期。

【最近10期数据】
组合序列：{" → ".join(combos_10[:10])}
和值序列：{sums_10[:10]}
组合频次：{dict(combo_count)}

【输出格式】
仅输出JSON：{{"main":"组合","candidate":"组合","kill":"组合","confidence":整数}}
可选组合：小单、小双、大单、大双
置信度范围：40-90"""
        
        return prompt
    
    def _parse_ai_response(self, text: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[int]]:
        try:
            start = text.find('{')
            end = text.rfind('}') + 1
            if start == -1 or end == 0:
                return None, None, None, None
            
            json_str = text[start:end]
            data = json.loads(json_str)
            
            main = data.get("main") or data.get("main_confirm")
            candidate = data.get("candidate") or data.get("candidate_confirm")
            kill = data.get("kill") or data.get("kill_confirm")
            confidence = data.get("confidence") or data.get("final_confidence")
            
            if not main or not candidate or not kill:
                return None, None, None, None
            
            if isinstance(main, list):
                main = main[0] if main else candidate
            
            if main not in COMBOS or candidate not in COMBOS or kill not in COMBOS:
                return None, None, None, None
            
            if main == candidate or main == kill or candidate == kill:
                return None, None, None, None
            
            if not isinstance(confidence, (int, float)):
                confidence = 60
            
            return main, candidate, kill, int(confidence)
        except Exception as e:
            logger.log_error(0, "解析AI响应失败", e)
            return None, None, None, None
    
    async def _call_ai_with_prompt(self, prompt: str, qihao: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[int]]:
        prompt_hash = hashlib.md5(prompt.encode()).hexdigest()
        
        if prompt_hash in self._active_requests:
            logger.log_api("检测到重复请求", f"hash={prompt_hash[:8]}")
            try:
                result = await asyncio.wait_for(self._active_requests[prompt_hash], timeout=60)
                return result
            except asyncio.TimeoutError:
                del self._active_requests[prompt_hash]
        
        payload = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 256,
            "stream": False
        }
        
        request_task = asyncio.create_task(self._do_predict(payload, prompt_hash))
        self._active_requests[prompt_hash] = request_task
        
        try:
            result = await request_task
            return result
        finally:
            if prompt_hash in self._active_requests:
                del self._active_requests[prompt_hash]
    
    async def _do_predict(self, payload: Dict, prompt_hash: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[int]]:
        max_retries = 3
        base_delay = 2
        
        for attempt in range(max_retries):
            try:
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                }
                
                async with aiohttp.ClientSession(timeout=self.timeout) as session:
                    async with session.post(self.api_url, headers=headers, json=payload) as resp:
                        if resp.status == 200:
                            result = await resp.json()
                            response_text = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                            parsed = self._parse_ai_response(response_text)
                            if parsed[0] is not None:
                                logger.log_api("AI请求成功", f"第{attempt+1}次尝试")
                                return parsed
                        elif resp.status == 429 and attempt < max_retries - 1:
                            await asyncio.sleep(base_delay * (2 ** attempt) * 2)
                        elif attempt < max_retries - 1:
                            await asyncio.sleep(base_delay * (2 ** attempt))
            except asyncio.TimeoutError:
                if attempt == max_retries - 1:
                    return None, None, None, None
                await asyncio.sleep(base_delay * (2 ** attempt))
            except Exception as e:
                logger.log_error(0, "AI请求异常", e)
                if attempt == max_retries - 1:
                    return None, None, None, None
                await asyncio.sleep(base_delay)
        
        return None, None, None, None
    
    async def predict(self, history: List[Dict], qihao: str = None) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[int]]:
        if qihao and self._last_qihao == qihao and self._last_prediction:
            return self._last_prediction
        
        async with self._global_predict_lock:
            if qihao and self._last_qihao == qihao and self._last_prediction:
                return self._last_prediction
            
            # 使用规则算法计算
            rule_result = self.rule_predictor.get_rule_based_predictions(list(history)[:10], qihao)
            
            if rule_result:
                logger.log_analysis(f"规则算法结果: 主推{rule_result['main']}, 杀组{rule_result['kill']}")
                
                prompt = self._build_rule_based_prompt(history, rule_result)
                ai_result = await self._call_ai_with_prompt(prompt, qihao)
                
                if ai_result[0] is not None:
                    logger.log_prediction(0, "AI验证规则结果", f"AI确认: 主推{ai_result[0]}, 杀组{ai_result[2]}")
                    result = ai_result
                else:
                    main = rule_result['main'][0] if rule_result['main'] else rule_result['candidate']
                    candidate = rule_result['candidate']
                    kill = rule_result['kill']
                    confidence = rule_result['confidence']
                    logger.log_analysis(f"AI验证失败，使用规则结果")
                    result = (main, candidate, kill, confidence)
            else:
                logger.log_analysis("规则计算失败，回退到AI预测")
                prompt = self._build_fallback_prompt(history)
                result = await self._call_ai_with_prompt(prompt, qihao)
                
                if result[0] is None:
                    result = (random.choice(COMBOS), random.choice(COMBOS), random.choice(COMBOS), 50)
            
            if qihao:
                self._last_qihao = qihao
                self._last_prediction = result
            
            return result


# ==================== 模型管理器 ====================
class ModelManager:
    def __init__(self):
        self.ai_client = SiliconFlowAIClient(
            api_key=Config.SILICONFLOW_API_KEY,
            model_name=Config.SILICONFLOW_MODEL
        )
        self.rule_predictor = PC28RulePredictor()
        self.prediction_history = []
        self.recent_accuracy = deque(maxlen=50)
        self._last_predict_result = None
        self._last_predict_qihao = None
        self._predict_lock = asyncio.Lock()
        self._save_lock = asyncio.Lock()
    
    async def save(self):
        async with self._save_lock:
            try:
                data = {
                    'history': self.prediction_history[-100:],
                    'last_save': datetime.now().isoformat()
                }
                async with aiofiles.open(Config.MODEL_SAVE_FILE, 'w', encoding='utf-8') as f:
                    await f.write(json.dumps(data, ensure_ascii=False, indent=2))
            except Exception as e:
                logger.log_error(0, "保存预测历史失败", e)
    
    async def predict(self, history: List[Dict], latest: Dict = None) -> Dict:
        qihao = latest.get('qihao') if latest else None
        
        if qihao and self._last_predict_qihao == qihao and self._last_predict_result:
            return self._last_predict_result
        
        async with self._predict_lock:
            if qihao and self._last_predict_qihao == qihao and self._last_predict_result:
                return self._last_predict_result
            
            main, candidate, kill, confidence = await self.ai_client.predict(list(history), qihao)
            
            if main is None:
                rule_result = self.rule_predictor.get_rule_based_predictions(list(history)[:10], qihao)
                if rule_result:
                    main = rule_result['main'][0] if rule_result['main'] else rule_result['candidate']
                    candidate = rule_result['candidate']
                    kill = rule_result['kill']
                    confidence = rule_result['confidence']
                else:
                    main = random.choice(COMBOS)
                    candidate = random.choice([c for c in COMBOS if c != main])
                    kill = random.choice([c for c in COMBOS if c != main and c != candidate])
                    confidence = 50
            
            if main == candidate:
                candidate = random.choice([c for c in COMBOS if c != main])
            if main == kill or candidate == kill:
                kill = random.choice([c for c in COMBOS if c != main and c != candidate])
            
            result = {
                "main": main,
                "candidate": candidate,
                "kill": kill,
                "confidence": min(92, max(40, confidence)),
                "algo_details": [
                    {"name": "多算法投票杀组+双Y融合", "kill": kill},
                    {"name": "硅基流动AI验证", "result": f"主推{main}"}
                ]
            }
            
            if qihao:
                self._last_predict_qihao = qihao
                self._last_predict_result = result
            
            return result
    
    async def learn(self, prediction: Dict, actual: str, qihao: str, sum_val: int):
        is_correct = (actual == prediction['main'] or actual == prediction['candidate'])
        
        record = {
            "time": datetime.now().isoformat(),
            "qihao": qihao,
            "main": prediction['main'],
            "candidate": prediction['candidate'],
            "kill": prediction.get('kill'),
            "actual": actual,
            "sum": sum_val,
            "correct": is_correct
        }
        self.prediction_history.append(record)
        self.recent_accuracy.append(1 if is_correct else 0)
        
        # 同步到规则预测器
        rule_pred = {
            'main': [prediction['main']],
            'candidate': prediction['candidate'],
            'kill': prediction.get('kill'),
            'qihao': qihao,
            'confidence': prediction.get('confidence', 50)
        }
        self.rule_predictor.record_prediction_result(rule_pred, actual, sum_val)
        
        if len(self.prediction_history) % 10 == 0:
            asyncio.create_task(self.save())
    
    def get_accuracy_stats(self):
        recent = sum(self.recent_accuracy) / len(self.recent_accuracy) if self.recent_accuracy else 0
        total = sum(1 for r in self.prediction_history if r.get('correct', False)) / len(self.prediction_history) if self.prediction_history else 0
        return {
            'overall': {'recent': recent, 'total': total},
            'algorithms': {'增强版规则+AI': recent}
        }
    
    def clear_history(self):
        self.prediction_history = []
        self.recent_accuracy.clear()
        asyncio.create_task(self.save())


# ==================== API模块（开奖数据） ====================
class PC28API:
    def __init__(self):
        self.base_url = Config.PC28_API_BASE
        self.session = None
        self.call_stats = {
            'total_calls': 0, 'successful_calls': 0, 'failed_calls': 0,
            'last_call_time': None, 'last_success_time': None,
            'response_times': deque(maxlen=100)
        }
        self.cache_file = Config.CACHE_DIR / "history_cache.pkl"
        self.history_cache = deque(maxlen=Config.CACHE_SIZE)
        self.load_cache()
        logger.log_system("异步API模块初始化完成")
    
    async def ensure_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=Config.REQUEST_TIMEOUT))
    
    def load_cache(self):
        try:
            if self.cache_file.exists():
                with open(self.cache_file, 'rb') as f: self.history_cache.extend(pickle.load(f)[:Config.CACHE_SIZE])
        except Exception as e: logger.log_error(0, "加载缓存失败", e)
    
    def save_cache(self):
        try:
            with open(self.cache_file, 'wb') as f: pickle.dump(list(self.history_cache), f)
        except Exception as e: logger.log_error(0, "保存缓存失败", e)
    
    async def _make_api_call(self, endpoint, params=None):
        await self.ensure_session()
        for retry in range(Config.MAX_RETRIES):
            self.call_stats['total_calls'] += 1
            start = time.time()
            try:
                url = f"{self.base_url}/{endpoint}.json"
                if params: url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
                async with self.session.get(url) as resp:
                    resp.raise_for_status()
                    try:
                        data = await resp.json()
                    except json.JSONDecodeError as e:
                        logger.log_error(0, f"JSON解析失败 {endpoint}", e)
                        if retry < Config.MAX_RETRIES-1:
                            await asyncio.sleep(Config.RETRY_BACKOFF ** retry)
                            continue
                        else:
                            self.call_stats['failed_calls'] += 1
                            return None
                    if data.get('message') != 'success':
                        if retry < Config.MAX_RETRIES-1:
                            await asyncio.sleep(Config.RETRY_BACKOFF ** retry)
                            continue
                        else: return None
                    elapsed = time.time() - start
                    self.call_stats['successful_calls'] += 1
                    self.call_stats['response_times'].append(elapsed)
                    return data.get('data', [])
            except Exception as e:
                if retry < Config.MAX_RETRIES-1:
                    await asyncio.sleep(Config.RETRY_BACKOFF ** retry)
                else:
                    self.call_stats['failed_calls'] += 1
                    return None
        return None
    
    async def download_csv_data(self, url: str) -> List[Dict]:
        await self.ensure_session()
        try:
            async with self.session.get(url) as resp:
                resp.raise_for_status()
                text = await resp.text()
                if text.startswith('\ufeff'): text = text[1:]
                reader = csv.DictReader(StringIO(text))
                rows = [{k.strip(): v.strip() for k, v in row.items()} for row in reader]
                return rows
        except Exception as e:
            logger.log_error(0, f"下载CSV失败 {url}", e)
            return []
    
    def _parse_kj_csv_row(self, row: Dict) -> Optional[Dict]:
        try:
            qihao = row.get('期号', '').strip()
            date_str = row.get('日期', '').strip()
            time_str = row.get('时间', '').strip()
            number_str = row.get('号码', '').strip()
            combo = row.get('组合类型', '').strip()
            
            total = None
            a = b = c = 0
            if '+' in number_str:
                parts = number_str.split('+')
                if len(parts) == 3:
                    a, b, c = int(parts[0]), int(parts[1]), int(parts[2])
                    total = a + b + c
            
            if combo and len(combo) >= 2:
                size, parity = combo[0], combo[1]
            elif total is not None:
                size = "大" if total >= 14 else "小"
                parity = "单" if total % 2 else "双"
                combo = size + parity
            else: 
                return None
            
            return {
                'qihao': qihao, 'opentime': f"{date_str} {time_str}", 'opennum': str(total) if total else '',
                'sum': total, 'size': size, 'parity': parity, 'combo': combo,
                'a': a, 'b': b, 'c': c,
                'parsed_time': self._parse_time(date_str, time_str),
                'fetch_time': datetime.now().isoformat(),
                'hash': hashlib.md5(f"{qihao}_{total}".encode()).hexdigest()[:8]
            }
        except Exception as e:
            return None
    
    async def fetch_kj(self, nbr=1):
        data = await self._make_api_call('kj', {'nbr': nbr})
        if not data: return []
        
        processed = []
        for item in data:
            try:
                qihao = str(item.get('nbr', '')).strip()
                if not qihao: continue
                
                number = item.get('number') or item.get('num')
                if number is None: continue
                
                a = b = c = total = 0
                if isinstance(number, str) and '+' in number:
                    parts = number.split('+')
                    if len(parts) == 3:
                        a, b, c = int(parts[0]), int(parts[1]), int(parts[2])
                        total = a + b + c
                else:
                    try: total = int(number)
                    except: continue
                
                combo = item.get('combination', '')
                if combo and len(combo) >= 2:
                    size, parity = combo[0], combo[1]
                else:
                    size = "大" if total >= 14 else "小"
                    parity = "单" if total % 2 else "双"
                    combo = size + parity
                
                date_str = item.get('date', '')
                time_str = item.get('time', '')
                
                processed.append({
                    'qihao': qihao, 'opentime': f"{date_str} {time_str}", 'opennum': str(total),
                    'sum': total, 'size': size, 'parity': parity, 'combo': combo,
                    'a': a, 'b': b, 'c': c,
                    'parsed_time': self._parse_time(date_str, time_str),
                    'fetch_time': datetime.now().isoformat(),
                    'hash': hashlib.md5(f"{qihao}_{total}".encode()).hexdigest()[:8]
                })
            except Exception as e: 
                continue
        
        processed.sort(key=lambda x: x.get('parsed_time', datetime.now()), reverse=True)
        return processed
    
    def _parse_time(self, date_str, time_str):
        try:
            dt_str = f"{date_str} {time_str}".strip()
            if not dt_str or dt_str == ' ': 
                return datetime.now()
            formats = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%m-%d %H:%M:%S", "%H:%M:%S"]
            for fmt in formats:
                try:
                    dt = datetime.strptime(dt_str, fmt)
                    if fmt == "%H:%M:%S":
                        now = datetime.now()
                        dt = dt.replace(year=now.year, month=now.month, day=now.day)
                    elif fmt == "%m-%d %H:%M:%S":
                        dt = dt.replace(year=datetime.now().year)
                    return dt
                except ValueError: 
                    continue
            return datetime.now()
        except Exception: 
            return datetime.now()
    
    async def initialize_history(self, count=Config.INITIAL_HISTORY_SIZE, max_retries=3):
        logger.log_system("正在初始化历史数据...")
        kj_csv_url = f"https://www.pc28.help/api/kj.json?nbr={Config.KJ_HISTORY_DOWNLOAD}"
        kj_rows = await self.download_csv_data(kj_csv_url)
        if kj_rows:
            self.history_cache.clear()
            for row in kj_rows:
                parsed = self._parse_kj_csv_row(row)
                if parsed: 
                    self.history_cache.append(parsed)
            self.save_cache()
            logger.log_system(f"从CSV加载开奖数据 {len(self.history_cache)} 条")
            if len(self.history_cache) >= 30: 
                return True
        
        for attempt in range(max_retries):
            if attempt > 0: await asyncio.sleep(2)
            test_data = await self.fetch_kj(nbr=1)
            if not test_data: continue
            if len(self.history_cache) >= 50: return True
            kj_data = await self.fetch_kj(nbr=count)
            if not kj_data: continue
            kj_data.sort(key=lambda x: x.get('parsed_time', datetime.now()), reverse=True)
            self.history_cache.clear()
            for item in kj_data:
                if not any(x.get('qihao') == item['qihao'] for x in self.history_cache):
                    self.history_cache.append(item)
            self.save_cache()
            return len(self.history_cache) >= 30
        return False
    
    async def get_latest_result(self):
        latest_api = await self.fetch_kj(nbr=1)
        if not latest_api: return None
        latest = latest_api[0]
        if self.history_cache and self.history_cache[0].get('qihao') == latest['qihao']: 
            return None
        if not any(x.get('qihao') == latest['qihao'] for x in self.history_cache):
            self.history_cache.appendleft(latest)
            if len(self.history_cache) > Config.CACHE_SIZE: 
                self.history_cache.pop()
            self.save_cache()
        return latest
    
    async def get_history(self, count=50):
        return list(self.history_cache)[:count]
    
    async def close(self):
        if self.session and not self.session.closed: 
            await self.session.close()
    
    def get_statistics(self):
        avg = np.mean(self.call_stats['response_times']) if self.call_stats['response_times'] else 0
        success_rate = (self.call_stats['successful_calls'] / self.call_stats['total_calls']) if self.call_stats['total_calls'] else 0
        return {
            '缓存数据量': len(self.history_cache),
            '总API调用': self.call_stats['total_calls'],
            '成功调用': self.call_stats['successful_calls'],
            '成功率': f"{success_rate:.1%}",
            '平均响应时间': f"{avg:.2f}秒",
            '最新期号': self.history_cache[0].get('qihao') if self.history_cache else '无'
        }


# ==================== 数据模型 ====================
@dataclass
class BetParams:
    base_amount: int = Config.DEFAULT_BASE_AMOUNT
    max_amount: int = Config.DEFAULT_MAX_AMOUNT
    multiplier: float = Config.DEFAULT_MULTIPLIER
    stop_loss: int = Config.DEFAULT_STOP_LOSS
    stop_win: int = Config.DEFAULT_STOP_WIN
    stop_balance: int = Config.DEFAULT_STOP_BALANCE
    resume_balance: int = Config.DEFAULT_RESUME_BALANCE
    dynamic_base_ratio: float = 0.0

@dataclass
class Account:
    phone: str
    owner_user_id: int
    created_time: str = field(default_factory=lambda: datetime.now().isoformat())
    is_logged_in: bool = False
    auto_betting: bool = False
    prediction_broadcast: bool = False
    display_name: str = ""
    telegram_user_id: int = 0
    game_group_id: int = 0
    game_group_name: str = ""
    prediction_group_id: int = 0
    prediction_group_name: str = ""
    betting_strategy: str = "保守"
    betting_scheme: str = "组合1"
    bet_params: BetParams = field(default_factory=BetParams)
    balance: float = 0
    initial_balance: float = 0
    session_profit: float = 0
    session_loss: float = 0
    total_profit: float = 0
    total_loss: float = 0
    consecutive_losses: int = 0
    consecutive_wins: int = 0
    total_bets: int = 0
    total_wins: int = 0
    last_bet_time: Optional[str] = None
    last_bet_period: Optional[str] = None
    last_bet_types: List[str] = field(default_factory=list)
    last_bet_amount: int = 0
    last_bet_total: int = 0
    last_prediction: Dict = field(default_factory=dict)
    pending_bet: Optional[Dict] = None
    last_balance_check: Optional[str] = None
    last_balance: float = 0
    input_mode: Optional[str] = None
    input_buffer: str = ""
    stop_reason: Optional[str] = None
    martingale_reset: bool = True
    fibonacci_reset: bool = True
    needs_2fa: bool = False
    login_temp_data: dict = field(default_factory=dict)
    chase_enabled: bool = False
    chase_numbers: List[int] = field(default_factory=list)
    chase_periods: int = 0
    chase_current: int = 0
    chase_amount: int = 0
    chase_stop_reason: Optional[str] = None
    streak_records: List[Dict] = field(default_factory=list)
    current_streak_type: Optional[str] = None
    current_streak_start: Optional[str] = None
    current_streak_messages: List[Dict] = field(default_factory=list)
    current_streak_count: int = 0
    last_message_id: Optional[int] = None
    prediction_content: str = "double"
    broadcast_stop_requested: bool = False
    betting_in_progress: bool = False

    def get_display_name(self) -> str:
        return self.display_name if self.display_name else self.phone


# ==================== 账户管理器（简化版） ====================
class AccountManager:
    def __init__(self):
        self.accounts_file = Config.DATA_DIR / "accounts.json"
        self.user_states_file = Config.DATA_DIR / "user_states.json"
        self.accounts: Dict[str, Account] = {}
        self.user_states: Dict[int, Dict] = {}
        self.clients: Dict[str, TelegramClient] = {}
        self.login_sessions: Dict[str, Dict] = {}
        self.update_lock = asyncio.Lock()
        self.account_locks: Dict[str, asyncio.Lock] = {}
        self.balance_cache: Dict[str, Dict] = {}
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
                for phone, acc_dict in data.items():
                    bet_params_dict = acc_dict.get('bet_params', {})
                    bet_params = BetParams(**bet_params_dict)
                    acc_dict['bet_params'] = bet_params
                    self.accounts[phone] = Account(**acc_dict)
            except Exception as e:
                logger.log_error(0, "加载账户数据失败", e)
    
    async def save_accounts(self):
        data = {}
        for phone, acc in self.accounts.items():
            acc_dict = asdict(acc)
            data[phone] = acc_dict
        try:
            async with aiofiles.open(self.accounts_file, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(data, ensure_ascii=False, indent=2))
        except Exception as e:
            logger.log_error(0, "保存账户数据失败", e)
    
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
    
    async def add_account(self, user_id, phone) -> Tuple[bool, str]:
        async with self.update_lock:
            if user_id not in Config.ADMIN_USER_IDS:
                user_accounts = [acc for acc in self.accounts.values() if acc.owner_user_id == user_id]
                if len(user_accounts) >= Config.MAX_ACCOUNTS_PER_USER:
                    return False, f"每个用户最多只能添加 {Config.MAX_ACCOUNTS_PER_USER} 个账户"
            if phone in self.accounts: 
                return False, "账户已存在"
            if not re.match(r'^\+\d{10,15}$', phone):
                return False, "手机号格式不正确，需包含国际区号，如 +861234567890"
            self.accounts[phone] = Account(phone=phone, owner_user_id=user_id)
            self._dirty.add(phone)
            return True, f"账户 {phone} 添加成功"
    
    def get_account(self, phone) -> Optional[Account]:
        return self.accounts.get(phone)
    
    async def update_account(self, phone, **kwargs):
        async with self.update_lock:
            if phone not in self.account_locks:
                self.account_locks[phone] = asyncio.Lock()
        async with self.account_locks[phone]:
            if phone in self.accounts:
                acc = self.accounts[phone]
                for k, v in kwargs.items():
                    if k == 'bet_params' and isinstance(v, dict):
                        for pk, pv in v.items():
                            setattr(acc.bet_params, pk, pv)
                    else:
                        setattr(acc, k, v)
                async with self.update_lock:
                    self._dirty.add(phone)
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
    
    def set_login_session(self, phone, session_data):
        self.login_sessions[phone] = session_data
    
    def get_login_session(self, phone):
        return self.login_sessions.get(phone)
    
    def create_client(self, phone):
        try:
            session_name = phone.replace('+', '')
            session_path = Config.SESSIONS_DIR / session_name
            client = TelegramClient(str(session_path), Config.API_ID, Config.API_HASH)
            self.clients[phone] = client
            return client
        except Exception as e:
            logger.log_error(0, f"创建客户端失败 {phone}", e)
            return None
    
    async def ensure_client_connected(self, phone):
        client = self.clients.get(phone)
        if not client:
            await self.update_account(phone, is_logged_in=False)
            return False
        if not client.is_connected():
            try:
                await client.connect()
            except:
                await self.update_account(phone, is_logged_in=False)
                return False
        try:
            if not await client.is_user_authorized():
                await self.update_account(phone, is_logged_in=False)
                return False
        except:
            await self.update_account(phone, is_logged_in=False)
            return False
        return True
    
    def get_cached_balance(self, phone):
        cache = self.balance_cache.get(phone)
        if cache and (datetime.now() - cache['time']).seconds < Config.BALANCE_CACHE_SECONDS:
            return cache['balance']
        return None
    
    def update_balance_cache(self, phone, balance):
        self.balance_cache[phone] = {'balance': balance, 'time': datetime.now()}
    
    async def verify_login_status(self):
        for phone, acc in self.accounts.items():
            if acc.is_logged_in:
                connected = await self.ensure_client_connected(phone)
                if not connected:
                    await self.update_account(phone, is_logged_in=False)
    
    async def reset_auto_flags_on_start(self):
        logger.log_system("启动时保留账户的自动投注和播报标志")
    
    async def start_periodic_save(self):
        async def periodic_save():
            while True:
                await asyncio.sleep(Config.ACCOUNT_SAVE_INTERVAL)
                if self._dirty:
                    await self.save_accounts()
                    self._dirty.clear()
        self._save_task = asyncio.create_task(periodic_save())
    
    async def stop_periodic_save(self):
        if self._save_task:
            self._save_task.cancel()


# ==================== 金额管理器 ====================
class AmountManager:
    def __init__(self, account_manager):
        self.account_manager = account_manager
    
    async def set_param(self, phone, param_name, amount, user_id):
        if amount < 0: return False, "金额不能为负数"
        acc = self.account_manager.get_account(phone)
        if not acc: return False, "账户不存在"
        valid_params = ['base_amount', 'max_amount', 'stop_loss', 'stop_win', 'stop_balance', 'resume_balance', 'dynamic_base_ratio']
        if param_name not in valid_params: 
            return False, f"无效参数"
        if param_name == 'base_amount' and amount > acc.balance:
            return False, f"基础金额不能超过当前余额 {acc.balance:.2f}KK"
        await self.account_manager.update_account(phone, bet_params={param_name: amount})
        return True, f"{param_name} 已设置为 {amount}KK"


# ==================== 策略管理器 ====================
class BettingStrategyManager:
    def __init__(self, account_manager):
        self.account_manager = account_manager
        self.strategies = {
            '保守': {'base_amount': 10000, 'max_amount': 100000, 'multiplier': 1.5, 'stop_loss': 100000, 'stop_win': 50000, 'stop_balance': 50000, 'resume_balance': 200000},
            '平衡': {'base_amount': 50000, 'max_amount': 500000, 'multiplier': 2.0, 'stop_loss': 500000, 'stop_win': 250000, 'stop_balance': 100000, 'resume_balance': 500000},
            '激进': {'base_amount': 100000, 'max_amount': 1000000, 'multiplier': 2.0, 'stop_loss': 1000000, 'stop_win': 500000, 'stop_balance': 200000, 'resume_balance': 1000000},
            '马丁格尔': {'base_amount': 10000, 'max_amount': 10000000, 'multiplier': 2.5, 'stop_loss': 5000000, 'stop_win': 1000000, 'stop_balance': 500000, 'resume_balance': 2000000},
            '斐波那契': {'base_amount': 10000, 'max_amount': 10000000, 'multiplier': 1.0, 'stop_loss': 5000000, 'stop_win': 1000000, 'stop_balance': 500000, 'resume_balance': 2000000},
        }
        self.schemes = {
            '组合1': '投注第1推荐组合',
            '组合2': '投注第2推荐组合',
            '组合1+2': '同时投注第1、2推荐组合',
            '杀主': '投注除最不可能组合外的所有组合'
        }
    
    async def set_strategy(self, phone, strategy_name, user_id):
        if strategy_name not in self.strategies: return False, "无效策略"
        cfg = self.strategies[strategy_name]
        await self.account_manager.update_account(
            phone, betting_strategy=strategy_name,
            bet_params={k: v for k, v in cfg.items() if k != 'description'}
        )
        return True, f"已设置为: {strategy_name} 策略"
    
    async def set_scheme(self, phone, scheme_name, user_id):
        if scheme_name not in self.schemes: return False, "无效方案"
        await self.account_manager.update_account(phone, betting_scheme=scheme_name)
        return True, f"投注方案已设置为: {scheme_name}"


# ==================== 预测播报器（简化版） ====================
class PredictionBroadcaster:
    def __init__(self, account_manager, model_manager, api_client, global_scheduler):
        self.account_manager = account_manager
        self.model = model_manager
        self.api = api_client
        self.global_scheduler = global_scheduler
        self.broadcast_tasks = {}
        self.global_predictions = {
            'predictions': [], 'last_open_qihao': None, 'next_qihao': None,
            'last_update': None, 'cached_double_message': None, 'cached_kill_message': None
        }
        self.last_sent_qihao = {}
        self._send_locks = {}
        self.stop_target_qihao = {}
    
    async def start_broadcast(self, phone, user_id):
        acc = self.account_manager.get_account(phone)
        if not acc: return False, "账户不存在"
        if not acc.is_logged_in: return False, "请先登录账户"
        if not acc.prediction_group_id: return False, "请先设置播报群"
        if acc.broadcast_stop_requested:
            await self.account_manager.update_account(phone, broadcast_stop_requested=False)
            self.stop_target_qihao.pop(phone, None)
        if phone in self.broadcast_tasks and not self.broadcast_tasks[phone].done():
            return True, "播报器已在运行"
        if phone in self.broadcast_tasks: 
            self.broadcast_tasks[phone].cancel()
        self.last_sent_qihao[phone] = self.global_predictions.get('next_qihao')
        task = self.global_scheduler._create_task(self._broadcast_loop(phone, acc.prediction_group_id))
        self.broadcast_tasks[phone] = task
        await self.account_manager.update_account(phone, prediction_broadcast=True)
        return True, "预测播报器启动成功"
    
    async def stop_broadcast(self, phone, user_id):
        acc = self.account_manager.get_account(phone)
        if not acc: return False, "账户不存在"
        if not acc.prediction_broadcast: return True, "播报器已停止"
        target = self.global_predictions.get('next_qihao')
        await self.account_manager.update_account(phone, broadcast_stop_requested=True)
        self.stop_target_qihao[phone] = target
        return True, "将在最后一期开奖后停止播报"
    
    async def _broadcast_loop(self, phone, group_id):
        error_count = 0
        target_qihao = None
        while True:
            try:
                acc = self.account_manager.get_account(phone)
                if not acc: break
                if acc.broadcast_stop_requested:
                    if target_qihao is None: target_qihao = self.stop_target_qihao.get(phone) or self.global_predictions.get('next_qihao')
                    if self.last_sent_qihao.get(phone) != target_qihao:
                        await self.send_prediction(phone, group_id, force_qihao=target_qihao)
                    if self.global_predictions.get('last_open_qihao') == target_qihao:
                        await self.account_manager.update_account(phone, prediction_broadcast=False, broadcast_stop_requested=False)
                        break
                elif not acc.prediction_broadcast:
                    break
                else:
                    await self.send_prediction(phone, group_id)
                error_count = 0
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                error_count += 1
                if error_count >= 5:
                    await self.account_manager.update_account(phone, prediction_broadcast=False, broadcast_stop_requested=False)
                    break
                await asyncio.sleep(10)
    
    async def update_global_predictions(self, prediction, next_qihao, latest):
        last_correct = None
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
            matched_pred['correct'] = (matched_pred['main'] == current_combo or matched_pred['candidate'] == current_combo)
            last_correct = matched_pred['correct']
            await self.model.learn(matched_pred, current_combo, current_open_qihao, current_sum)
        
        new_pred = {
            'qihao': next_qihao, 'main': prediction['main'], 'candidate': prediction['candidate'],
            'confidence': prediction['confidence'], 'time': datetime.now().isoformat(),
            'actual': None, 'sum': None, 'correct': None, 'message_id': None,
            'algo_details': prediction.get('algo_details', []), 'kill_group': prediction.get('kill')
        }
        
        self.global_predictions['predictions'].append(new_pred)
        if len(self.global_predictions['predictions']) > 15:
            self.global_predictions['predictions'] = self.global_predictions['predictions'][-15:]
        
        self.global_predictions['last_open_qihao'] = current_open_qihao
        self.global_predictions['next_qihao'] = next_qihao
        self.global_predictions['last_update'] = datetime.now().isoformat()
        self._update_cached_messages()
        
        for phone, task in list(self.broadcast_tasks.items()):
            if not task.done():
                acc = self.account_manager.get_account(phone)
                if acc and (acc.prediction_broadcast or acc.broadcast_stop_requested) and acc.prediction_group_id:
                    await self.send_prediction(phone, acc.prediction_group_id)
    
    def _update_cached_messages(self):
        lines = ["🤖PC28增强版预测 ", "-"*30, "期号    主推候选  状态  和值"]
        for p in self.global_predictions['predictions'][-15:]:
            q = p['qihao'][-4:] if len(p['qihao'])>=4 else p['qihao']
            combo_str = p['main'] + p['candidate']
            mark = "✅" if p.get('correct') is True else "❌" if p.get('correct') is False else "⏳"
            s = str(p['sum']) if p['sum'] is not None else "--"
            lines.append(f"{q:4s}   {combo_str:4s}   {mark:2s}   {s:>2s}")
        self.global_predictions['cached_double_message'] = "AI双组预测\n```" + "\n".join(lines) + "\n```"
        
        kill_lines = ["🤖AI杀组", "-"*30, "期号   杀组    状态  和值"]
        for p in self.global_predictions['predictions'][-15:]:
            q = p['qihao'][-4:] if len(p['qihao'])>=4 else p['qihao']
            kill = p.get('kill_group', '--') or '--'
            mark = "✅" if (p.get('actual') is not None and p['actual'] != kill) else "❌" if (p.get('actual') is not None and p['actual'] == kill) else "⏳"
            s = str(p['sum']) if p['sum'] is not None else "--"
            kill_lines.append(f"{q:4s}   {kill:4s}   {mark:2s}   {s:>2s}")
        self.global_predictions['cached_kill_message'] = "AI杀组预测\n```" + "\n".join(kill_lines) + "\n```"
    
    async def send_prediction(self, phone, group_id, force_qihao=None):
        lock = self._send_locks.setdefault(phone, asyncio.Lock())
        async with lock:
            target_qihao = force_qihao if force_qihao is not None else self.global_predictions.get('next_qihao')
            if self.last_sent_qihao.get(phone) == target_qihao: return None
            
            client = self.account_manager.clients.get(phone)
            if not client or not await self.account_manager.ensure_client_connected(phone): return None
            
            acc = self.account_manager.get_account(phone)
            if not acc: return None
            
            if acc.prediction_content == "double":
                message = self.global_predictions.get('cached_double_message')
                if not message: self._update_cached_messages()
                message = self.global_predictions['cached_double_message']
            else:
                message = self.global_predictions.get('cached_kill_message')
                if not message: self._update_cached_messages()
                message = self.global_predictions['cached_kill_message']
            
            try:
                msg = await client.send_message(group_id, message, parse_mode='markdown')
                self.last_sent_qihao[phone] = target_qihao
                return msg.id
            except Exception as e:
                logger.log_error(0, f"发送播报失败", e)
                return None


# ==================== 游戏调度器（简化版） ====================
class GameScheduler:
    def __init__(self, account_manager, model_manager, api_client):
        self.account_manager = account_manager
        self.model = model_manager
        self.api = api_client
        self.game_stats = {'betting_cycles':0, 'successful_bets':0, 'failed_bets':0}
    
    async def start_auto_betting(self, phone, user_id):
        acc = self.account_manager.get_account(phone)
        if not acc: return False, "账户不存在"
        if not acc.is_logged_in: return False, "请先登录账户"
        if not acc.game_group_id: return False, "请先设置游戏群"
        await self.account_manager.update_account(phone, auto_betting=True, martingale_reset=True, fibonacci_reset=True)
        return True, "自动投注已开启"
    
    async def stop_auto_betting(self, phone, user_id):
        await self.account_manager.update_account(phone, auto_betting=False)
        return True, "自动投注已关闭"
    
    async def check_bet_result(self, phone, expected_qihao, latest_result):
        acc = self.account_manager.get_account(phone)
        if not acc: return
        if not acc.last_prediction or not acc.last_bet_types: return
        
        actual_combo = latest_result.get('combo')
        if not actual_combo: return
        
        main = acc.last_prediction.get('main')
        candidate = acc.last_prediction.get('candidate')
        
        def is_match(bet_type: str, actual: str) -> bool:
            if bet_type == actual: return True
            if bet_type in ["大","小"] and actual.startswith(bet_type): return True
            if bet_type in ["单","双"] and (actual == bet_type or (len(actual)>=2 and actual[1]==bet_type)): return True
            return False
        
        is_win = any(is_match(t, actual_combo) for t in acc.last_bet_types)
        
        if is_win:
            await self.account_manager.update_account(phone,
                consecutive_wins=acc.consecutive_wins+1, consecutive_losses=0,
                martingale_reset=True, fibonacci_reset=True, total_wins=acc.total_wins+1)
        else:
            await self.account_manager.update_account(phone,
                consecutive_losses=acc.consecutive_losses+1, consecutive_wins=0)
    
    async def execute_bet(self, phone, prediction, latest):
        acc = self.account_manager.get_account(phone)
        if not acc or not acc.auto_betting: return
        
        lock = self.account_manager.account_locks.setdefault(phone, asyncio.Lock())
        async with lock:
            if acc.betting_in_progress: return
            acc.betting_in_progress = True
        
        try:
            current_qihao = latest.get('qihao')
            if acc.last_bet_period == current_qihao: return
            
            now = datetime.now()
            next_open = latest['parsed_time'] + timedelta(seconds=Config.GAME_CYCLE_SECONDS)
            close_time = next_open - timedelta(seconds=Config.CLOSE_BEFORE_SECONDS)
            if now >= close_time: return
            
            cur_bal = await self._query_balance(phone)
            if cur_bal is None or cur_bal <= 0: return
            
            # 计算投注金额
            base = int(cur_bal * acc.bet_params.dynamic_base_ratio) if acc.bet_params.dynamic_base_ratio > 0 else acc.bet_params.base_amount
            base = max(Config.MIN_BET_AMOUNT, min(base, acc.bet_params.max_amount))
            
            bet_amount = base
            if acc.betting_strategy == '马丁格尔' and acc.consecutive_losses > 0:
                bet_amount = base * (acc.bet_params.multiplier ** acc.consecutive_losses)
            elif acc.betting_strategy == '斐波那契' and acc.consecutive_losses > 0:
                fib = [1,1,2,3,5,8,13,21,34,55]
                bet_amount = base * fib[min(acc.consecutive_losses, len(fib)-1)]
            elif acc.betting_strategy == '激进':
                bet_amount = base * (1 + acc.consecutive_losses)
            
            bet_amount = min(bet_amount, acc.bet_params.max_amount)
            bet_amount = max(bet_amount, Config.MIN_BET_AMOUNT)
            
            # 投注类型
            if acc.betting_scheme == '杀主' and prediction.get('kill'):
                bet_types = [c for c in COMBOS if c != prediction['kill']]
            else:
                bet_types = [prediction['main']]
                if acc.betting_scheme == '组合1+2':
                    bet_types.append(prediction['candidate'])
                elif acc.betting_scheme == '组合2':
                    bet_types = [prediction['candidate']]
            
            bet_items = [f"{t} {bet_amount}" for t in bet_types]
            total = bet_amount * len(bet_types)
            
            if cur_bal < total: return
            
            # 发送投注
            client = self.account_manager.clients.get(phone)
            if client and acc.game_group_id:
                try:
                    await client.send_message(acc.game_group_id, " ".join(bet_items))
                    self.game_stats['successful_bets'] += 1
                    self.game_stats['betting_cycles'] += 1
                    await self.account_manager.update_account(phone,
                        last_bet_time=datetime.now().isoformat(), last_bet_amount=bet_amount,
                        last_bet_types=bet_types, total_bets=acc.total_bets+1,
                        last_prediction={'main': prediction['main'], 'candidate': prediction['candidate']},
                        last_bet_period=current_qihao)
                except Exception as e:
                    self.game_stats['failed_bets'] += 1
        finally:
            acc.betting_in_progress = False
    
    async def _query_balance(self, phone: str) -> Optional[float]:
        client = self.account_manager.clients.get(phone)
        if not client or not await self.account_manager.ensure_client_connected(phone): return None
        try:
            await client.send_message(Config.BALANCE_BOT, "/start")
            await asyncio.sleep(2)
            msgs = await client.get_messages(Config.BALANCE_BOT, limit=3)
            for msg in msgs:
                if msg.text and ('KKCOIN' in msg.text.upper() or '余额' in msg.text):
                    match = re.search(r'([\d,]+\.?\d*)', msg.text)
                    if match:
                        balance = float(match.group(1).replace(',', ''))
                        self.account_manager.update_balance_cache(phone, balance)
                        return balance
            return None
        except Exception as e:
            return None
    
    def get_stats(self):
        auto = sum(1 for a in self.account_manager.accounts.values() if a.auto_betting)
        broadcast = sum(1 for a in self.account_manager.accounts.values() if a.prediction_broadcast)
        return {'auto_betting_accounts': auto, 'broadcast_accounts': broadcast, 'game_stats': self.game_stats.copy()}


# ==================== 全局调度器（简化版） ====================
class GlobalScheduler:
    def __init__(self, account_manager, model_manager, api_client, prediction_broadcaster, game_scheduler):
        self.account_manager = account_manager
        self.model = model_manager
        self.api = api_client
        self.prediction_broadcaster = prediction_broadcaster
        self.game_scheduler = game_scheduler
        self.task = None
        self.running = False
        self.last_qihao = None
        self.check_interval = Config.SCHEDULER_CHECK_INTERVAL
        self.bet_semaphore = asyncio.Semaphore(Config.MAX_CONCURRENT_BETS)
        self.tasks = set()
        self._prediction_lock = asyncio.Lock()
        self._last_prediction_result = None
        self._last_prediction_qihao = None
    
    async def start(self):
        if self.running: return
        self.running = True
        self.task = asyncio.create_task(self._run())
        self.tasks.add(self.task)
    
    async def stop(self):
        self.running = False
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
    
    def _create_task(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task
    
    async def _run(self):
        for attempt in range(5):
            if await self.api.initialize_history(): break
            await asyncio.sleep(5)
        
        while self.running:
            try:
                latest = await self.api.get_latest_result()
                if latest and latest.get('qihao') != self.last_qihao:
                    await self._on_new_period(latest['qihao'], latest)
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                await asyncio.sleep(10)
    
    async def _on_new_period(self, qihao, latest):
        try:
            # 检查上一期结果
            for phone, acc in self.account_manager.accounts.items():
                if acc.last_bet_period and acc.last_bet_period != qihao:
                    self._create_task(self.game_scheduler.check_bet_result(phone, acc.last_bet_period, latest))
            
            # 预测
            history = await self.api.get_history(50)
            if len(history) < 3: return
            
            async with self._prediction_lock:
                if self._last_prediction_qihao == qihao and self._last_prediction_result:
                    prediction = self._last_prediction_result
                else:
                    prediction = await self.model.predict(history, latest)
                    self._last_prediction_result = prediction
                    self._last_prediction_qihao = qihao
            
            next_qihao = increment_qihao(qihao)
            await self.prediction_broadcaster.update_global_predictions(prediction, next_qihao, latest)
            
            # 投注
            for phone, acc in self.account_manager.accounts.items():
                if acc.auto_betting and acc.is_logged_in and acc.game_group_id and acc.last_bet_period != qihao:
                    await asyncio.sleep(random.uniform(0.1, 0.3))
                    self._create_task(self._execute_bet_with_semaphore(phone, prediction, latest))
            
            self.last_qihao = qihao
        except Exception as e:
            logger.log_error(0, f"处理新期号失败", e)
    
    async def _execute_bet_with_semaphore(self, phone, prediction, latest):
        async with self.bet_semaphore:
            await self.game_scheduler.execute_bet(phone, prediction, latest)


# ==================== 主Bot类（简化版） ====================
class PC28Bot:
    def __init__(self):
        self.api = PC28API()
        self.account_manager = AccountManager()
        self.model = ModelManager()
        self.strategy_manager = BettingStrategyManager(self.account_manager)
        self.amount_manager = AmountManager(self.account_manager)
        self.game_scheduler = GameScheduler(self.account_manager, self.model, self.api)
        self.global_scheduler = GlobalScheduler(
            self.account_manager, self.model, self.api,
            None, self.game_scheduler
        )
        self.prediction_broadcaster = PredictionBroadcaster(self.account_manager, self.model, self.api, self.global_scheduler)
        self.global_scheduler.prediction_broadcaster = self.prediction_broadcaster
        
        self.application = Application.builder().token(Config.BOT_TOKEN).build()
        self._register_handlers()
        logger.log_system("PC28 Bot（增强稳定性和准确率版）初始化完成")
    
    def _register_handlers(self):
        self.application.add_handler(CommandHandler("start", self.cmd_start))
        self.application.add_handler(CommandHandler("cancel", self.cmd_cancel))
        
        # 登录会话
        conv_handler = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.login_select, pattern=r'^login_select:')],
            states={
                Config.LOGIN_SELECT: [],
                Config.LOGIN_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.login_code)],
                Config.LOGIN_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.login_password)],
            },
            fallbacks=[CommandHandler('cancel', self.cmd_cancel)],
        )
        self.application.add_handler(conv_handler)
        
        # 添加账户
        add_account_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.add_account_start, pattern=r'^add_account$')],
            states={Config.ADD_ACCOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_account_input)]},
            fallbacks=[CommandHandler('cancel', self.cmd_cancel)],
        )
        self.application.add_handler(add_account_conv)
        
        # 追号设置
        chase_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.chase_start, pattern=r'^action:setchase:')],
            states={
                Config.CHASE_NUMBERS: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.chase_input_numbers)],
                Config.CHASE_PERIODS: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.chase_input_periods)],
                Config.CHASE_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.chase_input_amount)],
            },
            fallbacks=[CommandHandler('cancel', self.cmd_cancel), CallbackQueryHandler(self.chase_cancel, pattern=r'^chase_cancel:')],
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
            "🎰 *PC28 智能预测投注系统 v3.3（增强版）*\n\n"
            "✨ 多算法投票杀组 | 双Y融合 | 交叉验证 | 历史准确率反馈\n"
            "🤖 硅基流动AI辅助验证\n\n"
            "请选择操作：",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    async def add_account_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        await query.edit_message_text("📱 请输入手机号（包含国际区号，如 +861234567890）：\n\n点击 /cancel 取消")
        return Config.ADD_ACCOUNT
    
    async def add_account_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        phone = update.message.text.strip()
        ok, msg = await self.account_manager.add_account(user_id, phone)
        await update.message.reply_text(f"{'✅' if ok else '❌'} {msg}")
        await self._show_main_menu(update.message)
        return ConversationHandler.END
    
    async def login_select(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        phone = query.data.split(':')[1]
        context.user_data['login_phone'] = phone
        
        acc = self.account_manager.get_account(phone)
        if not acc:
            await query.edit_message_text("账户不存在")
            return ConversationHandler.END
        
        if acc.is_logged_in:
            await self._show_account_detail(query, query.from_user.id, phone, context)
            return ConversationHandler.END
        
        client = self.account_manager.create_client(phone)
        if not client:
            await query.edit_message_text("创建客户端失败")
            return ConversationHandler.END
        
        try:
            await client.connect()
            if await client.is_user_authorized():
                me = await client.get_me()
                await self.account_manager.update_account(phone, is_logged_in=True, display_name=f"{me.first_name or ''} {me.last_name or ''}".strip())
                await self._show_account_detail(query, query.from_user.id, phone, context)
                return ConversationHandler.END
            else:
                res = await client.send_code_request(phone)
                self.account_manager.set_login_session(phone, {'phone_code_hash': res.phone_code_hash})
                await query.edit_message_text(f"📨 验证码已发送到 `{phone}`\n\n请输入验证码：", parse_mode='Markdown')
                return Config.LOGIN_CODE
        except Exception as e:
            await query.edit_message_text(f"❌ 登录失败：{str(e)[:200]}")
            return ConversationHandler.END
    
    async def login_code(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        phone = context.user_data.get('login_phone')
        if not phone:
            await update.message.reply_text("登录会话已过期")
            return ConversationHandler.END
        
        code = update.message.text.strip()
        client = self.account_manager.clients.get(phone)
        sess = self.account_manager.get_login_session(phone)
        
        if not client or not sess:
            await update.message.reply_text("会话已过期")
            return ConversationHandler.END
        
        try:
            await client.sign_in(phone, code, phone_code_hash=sess['phone_code_hash'])
            me = await client.get_me()
            await self.account_manager.update_account(phone, is_logged_in=True, display_name=f"{me.first_name or ''} {me.last_name or ''}".strip())
            await self._show_account_detail(update.message, update.effective_user.id, phone, context)
            return ConversationHandler.END
        except SessionPasswordNeededError:
            await update.message.reply_text("🔒 此账户启用了两步验证，请输入密码：")
            return Config.LOGIN_PASSWORD
        except Exception as e:
            await update.message.reply_text(f"❌ 验证失败：{str(e)[:200]}")
            return Config.LOGIN_CODE
    
    async def login_password(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        phone = context.user_data.get('login_phone')
        if not phone:
            await update.message.reply_text("登录会话已过期")
            return ConversationHandler.END
        
        pwd = update.message.text.strip()
        client = self.account_manager.clients.get(phone)
        
        if not client:
            await update.message.reply_text("客户端丢失")
            return ConversationHandler.END
        
        try:
            await client.sign_in(password=pwd)
            me = await client.get_me()
            await self.account_manager.update_account(phone, is_logged_in=True, display_name=f"{me.first_name or ''} {me.last_name or ''}".strip())
            await self._show_account_detail(update.message, update.effective_user.id, phone, context)
            return ConversationHandler.END
        except Exception as e:
            await update.message.reply_text(f"❌ 密码验证失败：{str(e)[:200]}")
            return Config.LOGIN_PASSWORD
    
    async def chase_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        phone = query.data.split(':')[1]
        context.user_data['chase_phone'] = phone
        
        text = "🔢 *设置数字追号 - 第1步/共3步*\n\n请输入要追的数字（0-27），多个数字可用空格、逗号分隔。"
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("❌ 取消", callback_data=f"chase_cancel:{phone}")]])
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        return Config.CHASE_NUMBERS
    
    async def chase_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        phone = query.data.split(':')[1]
        context.user_data.clear()
        await self._show_account_detail(query, query.from_user.id, phone, context)
        return ConversationHandler.END
    
    async def chase_input_numbers(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip()
        numbers = []
        for p in re.split(r'[,\s、]+', text):
            if p.strip().lstrip('-').isdigit():
                num = int(p.strip())
                if 0 <= num <= 27:
                    numbers.append(num)
        numbers = list(set(numbers))
        
        if not numbers:
            phone = context.user_data.get('chase_phone')
            await update.message.reply_text("❌ 未输入有效数字，请重新输入：")
            return Config.CHASE_NUMBERS
        
        context.user_data['chase_numbers'] = numbers
        phone = context.user_data['chase_phone']
        await update.message.reply_text(f"✅ 已记录数字：{', '.join(map(str, numbers))}\n\n请输入追号期数：")
        return Config.CHASE_PERIODS
    
    async def chase_input_periods(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip()
        if not text.isdigit() or int(text) <= 0:
            await update.message.reply_text("❌ 请输入正整数期数：")
            return Config.CHASE_PERIODS
        
        periods = int(text)
        context.user_data['chase_periods'] = periods
        phone = context.user_data['chase_phone']
        await update.message.reply_text(f"✅ 已设置期数：{periods} 期\n\n请输入每注金额（KK）：")
        return Config.CHASE_AMOUNT
    
    async def chase_input_amount(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip()
        try:
            amount = int(text)
            if amount < 0: raise ValueError
        except:
            await update.message.reply_text("❌ 请输入有效金额：")
            return Config.CHASE_AMOUNT
        
        phone = context.user_data['chase_phone']
        numbers = context.user_data['chase_numbers']
        periods = context.user_data['chase_periods']
        
        await self.account_manager.update_account(phone, chase_enabled=True, chase_numbers=numbers,
            chase_periods=periods, chase_current=0, chase_amount=amount)
        
        context.user_data.clear()
        await update.message.reply_text(f"✅ 追号设置成功！\n数字：{numbers}\n期数：{periods}\n每注：{amount}KK")
        await self._show_account_detail(update.message, update.effective_user.id, phone, context)
        return ConversationHandler.END
    
    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user.id
        state = self.account_manager.get_user_state(user)
        phone = state.get('current_account')
        if not phone: return
        
        acc = self.account_manager.get_account(phone)
        if not acc: return
        
        # 处理金额输入
        input_mode = acc.input_mode
        if input_mode in ['base_amount', 'max_amount', 'stop_balance', 'stop_loss', 'stop_win', 'resume_balance']:
            try:
                amount = int(update.message.text.strip())
                ok, msg = await self.amount_manager.set_param(phone, input_mode, amount, user)
                if ok:
                    await self.account_manager.update_account(phone, input_mode=None)
                    await update.message.reply_text(f"✅ {msg}")
                    await self._show_account_detail(update.message, user, phone, context)
                else:
                    await update.message.reply_text(f"❌ {msg}")
            except:
                await update.message.reply_text("❌ 请输入整数金额")
    
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data
        user = query.from_user.id
        
        if data == "menu:main":
            await self._show_main_menu(query)
        elif data == "menu:accounts":
            await self._show_accounts_menu(query, user)
        elif data == "menu:prediction":
            await self._show_prediction_menu(query)
        elif data == "menu:status":
            await self._show_status_menu(query)
        elif data == "menu:help":
            await self._show_help_menu(query)
        elif data == "run_analysis":
            await self._process_run_analysis(query)
        elif data == "refresh_status":
            await self._show_status_menu(query)
        elif data == "add_account":
            await self.add_account_start(update, context)
        elif data.startswith("select_account:"):
            phone = data.split(":")[1]
            await self._show_account_detail(query, user, phone, context)
        elif data.startswith("action:"):
            parts = data.split(":")
            action = parts[1]
            phone = parts[2] if len(parts) > 2 else None
            await self._process_action(query, user, action, phone, context)
        elif data.startswith("amount_menu:"):
            phone = data.split(":")[1]
            await self._show_amount_menu(query, user, phone, context)
        elif data.startswith("amount_set:"):
            parts = data.split(":")
            param = parts[1]
            phone = parts[2]
            await self._amount_set(query, user, phone, param, context)
        elif data.startswith("set_strategy:"):
            parts = data.split(":")
            phone = parts[1]
            strategy = parts[2]
            await self._process_set_strategy(query, user, phone, strategy)
        elif data.startswith("set_scheme:"):
            parts = data.split(":")
            phone = parts[1]
            scheme = parts[2]
            await self._process_set_scheme(query, user, phone, scheme)
        elif data.startswith("set_group:"):
            group_id = int(data.split(":")[1])
            await self._set_group(query, user, group_id)
        elif data.startswith("set_pred_group:"):
            group_id = int(data.split(":")[1])
            await self._set_pred_group(query, user, group_id)
        elif data.startswith("toggle_content:"):
            phone = data.split(":")[1]
            await self._toggle_content(query, user, phone)
        elif data.startswith("clear_streak:"):
            phone = data.split(":")[1]
            await self._clear_streak(query, user, phone)
        else:
            pass
    
    async def _show_main_menu(self, target):
        keyboard = [
            [InlineKeyboardButton("📱 账户管理", callback_data="menu:accounts")],
            [InlineKeyboardButton("🎯 智能预测", callback_data="menu:prediction")],
            [InlineKeyboardButton("📊 系统状态", callback_data="menu:status")],
            [InlineKeyboardButton("❓ 帮助", callback_data="menu:help")],
            [InlineKeyboardButton("📖 使用手册", url=Config.MANUAL_LINK)]
        ]
        text = "🎮 *PC28 智能投注系统（增强版）*\n\n多算法投票 | 双Y融合 | 交叉验证\n\n请选择操作："
        if hasattr(target, 'edit_message_text'):
            await target.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        else:
            await target.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    
    async def _show_accounts_menu(self, target, user):
        accounts = self.account_manager.get_user_accounts(user)
        kb = [[InlineKeyboardButton("➕ 添加账户", callback_data="add_account")]]
        text = "📱 *您的账户列表*\n\n"
        for acc in accounts:
            status = "✅" if acc.is_logged_in else "❌"
            text += f"{status} {acc.get_display_name()}\n"
            kb.append([InlineKeyboardButton(f"{acc.get_display_name()}", callback_data=f"select_account:{acc.phone}")])
        kb.append([InlineKeyboardButton("🔙 返回", callback_data="menu:main")])
        
        if not accounts:
            text = "📭 您还没有添加账户"
        
        if hasattr(target, 'edit_message_text'):
            await target.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
        else:
            await target.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    
    async def _show_account_detail(self, target, user, phone, context):
        self.account_manager.set_user_state(user, 'account_selected', {'current_account': phone})
        acc = self.account_manager.get_account(phone)
        if not acc:
            await self._show_accounts_menu(target, user)
            return
        
        status = "✅ 已登录" if acc.is_logged_in else "❌ 未登录"
        if acc.auto_betting: status += " | 🤖 自动投注"
        if acc.prediction_broadcast: status += " | 📊 播报中"
        
        net = acc.total_profit - acc.total_loss
        
        text = f"📱 *账户: {acc.get_display_name()}*\n\n状态: {status}\n净盈利: {net:.0f}K\n\n选择操作:"
        
        kb = [
            [InlineKeyboardButton("🔐 登录", callback_data=f"login_select:{phone}"), InlineKeyboardButton("🚪 登出", callback_data=f"action:logout:{phone}")],
            [InlineKeyboardButton("💬 游戏群", callback_data=f"action:listgroups:{phone}"), InlineKeyboardButton("📢 播报群", callback_data=f"action:listpredgroups:{phone}")],
            [InlineKeyboardButton("🎯 投注方案", callback_data=f"action:setscheme:{phone}"), InlineKeyboardButton("📈 金额策略", callback_data=f"action:setstrategy:{phone}")],
            [InlineKeyboardButton("💰 设置金额", callback_data=f"amount_menu:{phone}"), InlineKeyboardButton("🔢 设置追号", callback_data=f"action:setchase:{phone}")],
            [InlineKeyboardButton("🤖 开启自动投注" if not acc.auto_betting else "🛑 停止自动投注", callback_data=f"action:toggle_bet:{phone}")],
            [InlineKeyboardButton("📊 开启播报" if not acc.prediction_broadcast else "🛑 停止播报", callback_data=f"action:toggle_pred:{phone}")],
            [InlineKeyboardButton("💰 查询余额", callback_data=f"action:balance:{phone}"), InlineKeyboardButton("📊 账户状态", callback_data=f"action:status:{phone}")],
            [InlineKeyboardButton("🔙 返回", callback_data="menu:accounts")]
        ]
        
        if acc.chase_enabled:
            kb.insert(4, [InlineKeyboardButton("🛑 停止追号", callback_data=f"action:stopchase:{phone}")])
        
        if hasattr(target, 'edit_message_text'):
            await target.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
        else:
            await target.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    
    async def _show_prediction_menu(self, target):
        kb = [[InlineKeyboardButton("🔮 运行预测", callback_data="run_analysis")], [InlineKeyboardButton("🔙 返回", callback_data="menu:main")]]
        text = "🎯 *预测分析菜单*\n\n多算法投票杀组 | 双Y融合 | 交叉验证 | 历史准确率反馈"
        await target.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    
    async def _show_status_menu(self, target):
        api_stats = self.api.get_statistics()
        sched_stats = self.game_scheduler.get_stats()
        total = len(self.account_manager.accounts)
        logged = sum(1 for a in self.account_manager.accounts.values() if a.is_logged_in)
        
        text = f"📊 *系统状态*\n\n*数据状态*\n• 缓存数据: {api_stats['缓存数据量']}期\n• 最新期号: {api_stats['最新期号']}\n\n*账户状态*\n• 总账户: {total}\n• 已登录: {logged}\n• 自动投注: {sched_stats['auto_betting_accounts']}\n• 预测播报: {sched_stats['broadcast_accounts']}\n\n*游戏统计*\n• 投注周期: {sched_stats['game_stats']['betting_cycles']}\n• 成功投注: {sched_stats['game_stats']['successful_bets']}"
        
        kb = [[InlineKeyboardButton("🔄 刷新", callback_data="refresh_status")], [InlineKeyboardButton("🔙 返回", callback_data="menu:main")]]
        await target.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    
    async def _show_help_menu(self, target):
        text = """📚 *帮助菜单*\n\n• 添加账户：账户管理 → 添加账户\n• 登录：选择账户 → 登录\n• 设置群组：账户详情 → 游戏群/播报群\n• 投注设置：投注方案/金额策略/设置金额/追号\n• 自动投注/播报：点击对应按钮\n• 手动投注：游戏群发送\"类型 金额\"\n\n*预测算法*\n• 多算法投票杀组（4种算法投票）\n• 双Y融合算法\n• 交叉验证\n• 历史准确率反馈\n• 硅基流动AI辅助验证"""
        kb = [[InlineKeyboardButton("🔙 返回", callback_data="menu:main")]]
        await target.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    
    async def _process_run_analysis(self, target):
        await target.edit_message_text("🔍 正在生成预测（多算法投票+交叉验证）...")
        history = await self.api.get_history(50)
        if len(history) < 3:
            await target.edit_message_text("❌ 历史数据不足")
            return
        
        latest = history[0]
        pred = await self.model.predict(history, latest)
        acc = self.model.get_accuracy_stats()
        
        text = f"🎯 *PC28预测结果*\n\n📊 最新: {latest.get('qihao')} | {latest.get('sum')}({latest.get('combo')})\n\n🏆 主推: {pred['main']}\n📌 候选: {pred['candidate']}\n🗑️ 杀组: {pred['kill']}\n📈 置信度: {pred['confidence']}%\n📊 准确率: {acc['overall']['recent']*100:.0f}%"
        
        kb = [[InlineKeyboardButton("🔄 刷新", callback_data="run_analysis")], [InlineKeyboardButton("🔙 返回", callback_data="menu:prediction")]]
        await target.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    
    async def _process_action(self, target, user, action, phone, context):
        if action == "logout":
            await self.game_scheduler.stop_auto_betting(phone, user)
            await self.prediction_broadcaster.stop_broadcast(phone, user)
            client = self.account_manager.clients.get(phone)
            if client:
                try:
                    if client.is_connected(): await client.disconnect()
                except: pass
            await self.account_manager.update_account(phone, is_logged_in=False, auto_betting=False, prediction_broadcast=False)
            await self._show_account_detail(target, user, phone, context)
        elif action == "toggle_bet":
            acc = self.account_manager.get_account(phone)
            if acc.auto_betting:
                await self.game_scheduler.stop_auto_betting(phone, user)
            else:
                await self.game_scheduler.start_auto_betting(phone, user)
            await self._show_account_detail(target, user, phone, context)
        elif action == "toggle_pred":
            acc = self.account_manager.get_account(phone)
            if acc.prediction_broadcast:
                await self.prediction_broadcaster.stop_broadcast(phone, user)
            else:
                await self.prediction_broadcaster.start_broadcast(phone, user)
            await self._show_account_detail(target, user, phone, context)
        elif action == "balance":
            bal = await self.game_scheduler._query_balance(phone)
            text = f"💰 余额: {bal:.2f} KK" if bal else "❌ 查询失败"
            await target.edit_message_text(text, parse_mode='Markdown')
        elif action == "status":
            await self._show_account_status(target, phone)
        elif action == "setstrategy":
            await self._show_strategy_selection(target, phone)
        elif action == "setscheme":
            await self._show_scheme_selection(target, phone)
        elif action == "listgroups":
            await self._list_groups(target, phone, "game")
        elif action == "listpredgroups":
            await self._list_groups(target, phone, "pred")
        elif action == "stopchase":
            await self.account_manager.update_account(phone, chase_enabled=False)
            await self._show_account_detail(target, user, phone, context)
    
    async def _show_strategy_selection(self, target, phone):
        kb = [[InlineKeyboardButton(name, callback_data=f"set_strategy:{phone}:{name}")] for name in self.strategy_manager.strategies.keys()]
        kb.append([InlineKeyboardButton("🔙 返回", callback_data=f"select_account:{phone}")])
        await target.edit_message_text("📊 *选择投注策略:*", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    
    async def _show_scheme_selection(self, target, phone):
        kb = [[InlineKeyboardButton(name, callback_data=f"set_scheme:{phone}:{name}")] for name in self.strategy_manager.schemes.keys()]
        kb.append([InlineKeyboardButton("🔙 返回", callback_data=f"select_account:{phone}")])
        await target.edit_message_text("🎯 *选择投注方案:*", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    
    async def _process_set_strategy(self, target, user, phone, strategy):
        ok, msg = await self.strategy_manager.set_strategy(phone, strategy, user)
        if ok:
            await self._show_account_detail(target, user, phone, None)
        else:
            await target.edit_message_text(f"❌ {msg}")
    
    async def _process_set_scheme(self, target, user, phone, scheme):
        ok, msg = await self.strategy_manager.set_scheme(phone, scheme, user)
        if ok:
            await self._show_account_detail(target, user, phone, None)
        else:
            await target.edit_message_text(f"❌ {msg}")
    
    async def _list_groups(self, target, phone, group_type):
        client = self.account_manager.clients.get(phone)
        if not client:
            await target.edit_message_text("❌ 客户端未连接")
            return
        try:
            dialogs = await client.get_dialogs(limit=30)
            groups = [d for d in dialogs if d.is_group or d.is_channel]
            if not groups:
                await target.edit_message_text("📭 未找到任何群组")
                return
            kb = []
            for g in groups[:10]:
                icon = "📢" if g.is_channel else "👥"
                cb = f"set_group:{g.id}" if group_type == "game" else f"set_pred_group:{g.id}"
                kb.append([InlineKeyboardButton(f"{icon} {g.name[:30]}", callback_data=cb)])
            kb.append([InlineKeyboardButton("🔙 返回", callback_data=f"select_account:{phone}")])
            await target.edit_message_text("📋 *选择群组:*", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
        except Exception as e:
            await target.edit_message_text("❌ 获取群组列表失败")
    
    async def _set_group(self, target, user, group_id):
        phone = self.account_manager.get_user_state(user).get('current_account')
        if not phone:
            await target.edit_message_text("❌ 请先选择账户")
            return
        await self.account_manager.update_account(phone, game_group_id=group_id)
        await self._show_account_detail(target, user, phone, None)
    
    async def _set_pred_group(self, target, user, group_id):
        phone = self.account_manager.get_user_state(user).get('current_account')
        if not phone:
            await target.edit_message_text("❌ 请先选择账户")
            return
        await self.account_manager.update_account(phone, prediction_group_id=group_id)
        await self._show_account_detail(target, user, phone, None)
    
    async def _toggle_content(self, target, user, phone):
        acc = self.account_manager.get_account(phone)
        if not acc: return
        new = "kill" if acc.prediction_content == "double" else "double"
        await self.account_manager.update_account(phone, prediction_content=new)
        await target.edit_message_text(f"✅ 播报内容已切换为 {'杀组' if new=='kill' else '双组'}")
        await self._show_account_detail(target, user, phone, None)
    
    async def _clear_streak(self, target, user, phone):
        await self.account_manager.update_account(phone, streak_records=[])
        await target.edit_message_text("✅ 记录已删除")
        await self._show_account_detail(target, user, phone, None)
    
    async def _show_amount_menu(self, target, user, phone, context):
        acc = self.account_manager.get_account(phone)
        if not acc: return
        
        text = f"💰 *金额设置*\n\n基础: {acc.bet_params.base_amount}KK\n最大: {acc.bet_params.max_amount}KK\n停止余额: {acc.bet_params.stop_balance}KK\n止损: {acc.bet_params.stop_loss}KK\n止盈: {acc.bet_params.stop_win}KK\n恢复: {acc.bet_params.resume_balance}KK"
        
        kb = [
            [InlineKeyboardButton("💰 基础金额", callback_data=f"amount_set:base_amount:{phone}"), InlineKeyboardButton("💎 最大金额", callback_data=f"amount_set:max_amount:{phone}")],
            [InlineKeyboardButton("🛑 停止余额", callback_data=f"amount_set:stop_balance:{phone}"), InlineKeyboardButton("⛔ 止损", callback_data=f"amount_set:stop_loss:{phone}")],
            [InlineKeyboardButton("✅ 止盈", callback_data=f"amount_set:stop_win:{phone}"), InlineKeyboardButton("🔄 恢复", callback_data=f"amount_set:resume_balance:{phone}")],
            [InlineKeyboardButton("🔙 返回", callback_data=f"select_account:{phone}")]
        ]
        await target.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    
    async def _amount_set(self, target, user, phone, param, context):
        names = {'base_amount':'基础金额','max_amount':'最大金额','stop_balance':'停止余额','stop_loss':'止损','stop_win':'止盈','resume_balance':'恢复'}
        await self.account_manager.update_account(phone, input_mode=param)
        text = f"🔢 请输入新的 {names.get(param, param)}（整数KK）："
        kb = [[InlineKeyboardButton("🔙 返回", callback_data=f"amount_menu:{phone}")]]
        await target.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    
    async def _show_account_status(self, target, phone):
        acc = self.account_manager.get_account(phone)
        if not acc: return
        
        text = f"📱 *账户状态*\n\n手机: `{acc.phone}`\n登录: {'✅' if acc.is_logged_in else '❌'}\n余额: {acc.balance:.2f}KK\n净盈利: {acc.total_profit - acc.total_loss:.0f}KK\n连赢: {acc.consecutive_wins} 连输: {acc.consecutive_losses}\n方案: {acc.betting_scheme}\n策略: {acc.betting_strategy}"
        
        kb = [[InlineKeyboardButton("🔙 返回", callback_data=f"select_account:{phone}")]]
        await target.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')


# ==================== 启动 ====================
async def post_init(application):
    bot = application.bot_data.get('bot')
    if bot:
        await bot.account_manager.reset_auto_flags_on_start()
        await bot.account_manager.verify_login_status()
        await bot.account_manager.start_periodic_save()
        await bot.global_scheduler.start()


def main():
    def handle_shutdown(signum, frame):
        print("\n🛑 正在优雅关闭...")
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
PC28 智能预测投注系统 v3.3（增强版）
多算法投票 | 双Y融合 | 交叉验证 | 历史准确率反馈
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
    bot.application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    random.seed(time.time())
    np.random.seed(int(time.time()))
    main()