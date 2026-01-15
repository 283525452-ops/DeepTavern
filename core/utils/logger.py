# core/utils/logger.py
import logging
import os
from datetime import datetime
import colorlog

# 确保日志目录存在
LOG_DIR = "logs"
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

def setup_logger(name="DeepTavern"):
    """配置全局日志"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # 避免重复添加 Handler
    if logger.handlers:
        return logger

    # 1. 文件处理器 (记录所有细节)
    log_filename = datetime.now().strftime("%Y-%m-%d_run.log")
    file_handler = logging.FileHandler(os.path.join(LOG_DIR, log_filename), encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_format = logging.Formatter('%(asctime)s - [%(levelname)s] - %(name)s - %(message)s')
    file_handler.setFormatter(file_format)

    # 2. 控制台处理器 (带颜色，只看重要信息)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    color_formatter = colorlog.ColoredFormatter(
        '%(log_color)s%(asctime)s - [%(levelname)s] - %(message)s',
        datefmt='%H:%M:%S',
        log_colors={
            'DEBUG': 'cyan',
            'INFO': 'green',
            'WARNING': 'yellow',
            'ERROR': 'red',
            'CRITICAL': 'bold_red',
        }
    )
    console_handler.setFormatter(color_formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

# 初始化一个全局实例
logger = setup_logger()
