# -*- coding: utf-8 -*-
# @Author : yangsongh
# @File : utils_lib.py
# @Version : 1.0.9

import os
import re
import sys
import json
import time
import logging
import traceback
import threading

from datetime import datetime
from types import TracebackType
from colorlog import ColoredFormatter
from typing import Callable, Optional, Type, Union
from logging import DEBUG, INFO, WARNING, ERROR, Handler, LogRecord


class CustomHandler(Handler):
    """自定义日志处理器"""

    def __init__(self, callback: Callable[[str], None]):
        super().__init__()
        self.callback = callback

    def emit(self, record: LogRecord):
        """发送日志"""
        try:
            if self.callback:
                # 使用格式化器格式化消息（保留颜色代码）
                msg = self.format(record)
                self.callback(msg)
        except Exception:
            self.handleError(record)


class LoggerManager:
    _logger_lock = threading.Lock()
    _web_console_callback: Optional[Callable[[str], None]] = None

    def __init__(self, logger_name: Union[str, None] = 'default', file_name: Union[str, None] = None,
                 file_format_str: str = '%(asctime)s %(levelname)1.1s %(filename)s:%(lineno)d %(message)s',
                 console_format_str: str = '%(asctime)s %(filename)s:%(lineno)d-%(funcName)s %(log_color)s%(message)s',
                 console_handler_level=DEBUG, file_handler_level=INFO, no_file_handler=False,
                 custom_callback: Optional[Callable[[str], None]] = None, propagate: bool = False):
        self.logger = logging.getLogger(logger_name)
        self.logger.propagate = propagate  # 是否允许传播到 root
        self.logger.setLevel(DEBUG)  # 控制日志最低输出等级
        self.logger.handlers.clear()  # 防止重复输出

        # 文件处理器
        if not no_file_handler:
            file_formatter = logging.Formatter(file_format_str)
            file_handler = logging.FileHandler(self.get_log_file_path(
                file_name), mode='a', encoding='utf-8')
            file_handler.setFormatter(file_formatter)
            file_handler.setLevel(file_handler_level)  # 文件日志输出等级
            self.logger.addHandler(file_handler)

        # 控制台处理器（带颜色）
        console_formatter = ColoredFormatter(
            console_format_str,
            log_colors={
                'DEBUG': 'blue',
                'INFO': 'green',
                'WARNING': 'yellow',
                'ERROR': 'red'
            }
        )
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(console_formatter)
        console_handler.setLevel(console_handler_level)  # 控制台日志输出等级
        self.logger.addHandler(console_handler)

        # 自定义处理器
        if custom_callback:
            self.add_custom_callback(custom_callback)

    def add_custom_callback(self, callback: Callable[[str], None]):
        """动态添加自定义回调"""
        # 检查是否已存在 CustomHandler
        for handler in self.logger.handlers:
            if isinstance(handler, CustomHandler):
                # 已存在，更新回调
                handler.callback = callback
                return

        # 添加新的处理器
        custom_handler = CustomHandler(callback)
        # 使用现有的控制台格式化器
        for handler in self.logger.handlers:
            if not isinstance(handler, logging.FileHandler) and not isinstance(handler, CustomHandler):
                custom_handler.setFormatter(handler.formatter)
                custom_handler.setLevel(handler.level)
                break

        self.logger.addHandler(custom_handler)

    @staticmethod
    def get_log_file_path(file_name: Union[str, None] = None) -> str:
        """获取日志文件路径"""
        logs_dir = 'logs'
        os.makedirs(logs_dir, exist_ok=True)

        current_date = datetime.now().strftime('%Y-%m-%d')
        if file_name:
            log_file_path = os.path.join(
                logs_dir, f'{current_date}_{file_name}.log')
        else:
            log_file_path = os.path.join(logs_dir, f'{current_date}.log')
        return log_file_path

    def info(self, message: object) -> None:
        with self._logger_lock:
            self.logger.info(message, stacklevel=2)

    def warning(self, message: object) -> None:
        with self._logger_lock:
            self.logger.warning(message, stacklevel=2)

    def error(self, message: object, exc_info=None) -> None:
        with self._logger_lock:
            self.logger.error(message, exc_info=exc_info, stacklevel=2)

    def debug(self, message: object) -> None:
        with self._logger_lock:
            self.logger.debug(message, stacklevel=2)


class ConfigManager:
    def __init__(self, logger: LoggerManager, cfg_file='config.jsonc', deft_cfgs: dict = {}):
        self.logger = logger
        self.cfg_file = cfg_file
        self.deft_cfgs: dict = deft_cfgs
        self.cfgs = self.deft_cfgs.copy()
        self.cfg_lock = threading.RLock()

    # 内部工具：安全移除JSONC注释（区分字符串内外，不破坏路径/文本）
    def _remove_json_comments(self, text: str) -> str:
        result = []
        in_str = False
        in_block_comment = False
        i = 0
        n = len(text)
        while i < n:
            char = text[i]
            next_char = text[i+1] if i + 1 < n else ''

            if in_block_comment:
                if char == '*' and next_char == '/':
                    in_block_comment = False
                    i += 1
            elif in_str:
                if char == '\\':
                    result.append(char)
                    result.append(next_char)
                    i += 1
                elif char == '"':
                    in_str = False
                result.append(char)
            else:
                if char == '/' and next_char == '*':
                    in_block_comment = True
                    i += 1
                elif char == '/' and next_char == '/':
                    # 单行注释，跳到换行
                    while i < n and text[i] not in ('\n', '\r'):
                        i += 1
                    continue
                elif char == '"':
                    in_str = True
                    result.append(char)
                else:
                    result.append(char)
            i += 1
        return ''.join(result)

    def load_configs(self) -> bool:
        """加载配置文件"""
        with self.cfg_lock:
            try:
                # 如果配置文件不存在或为空，恢复默认配置
                if not os.path.exists(self.cfg_file) or os.path.getsize(self.cfg_file) == 0:
                    self.logger.warning(f'{self.cfg_file} 不存在或为空，恢复默认配置')
                    return self.restore_default_config()

                # 读取原始文本
                with open(self.cfg_file, 'r', encoding='utf-8') as f:
                    raw_content = f.read()

                # 1. 安全删除注释
                no_comment = self._remove_json_comments(raw_content)
                # 2. 清除非法ASCII控制字符（解决 Invalid control character）
                content_clean = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F\r]', '', no_comment)
                # 3. 全局修复裸反斜杠 \ → \\，再修正重复转义
                content_clean = content_clean.replace("\\", "\\\\")
                content_clean = re.sub(r'\\\\(["\\/bfnrt])', r'\\\1', content_clean)
                # 4. 剩下多余的连续双斜杠统一修正为单层转义斜杠
                content_clean = re.sub(r'\\\\', r'\\', content_clean)
                # 5. 合并多余空行
                content_clean = re.sub(r'\n+', '\n', content_clean).strip()

                # 解析标准JSON
                self.cfgs = json.loads(content_clean)

                # 解析结果为空
                if not self.cfgs:
                    self.logger.warning(f'配置文件 {self.cfg_file} 解析为空，请检查文件')
                    return False

                return True

            except (json.JSONDecodeError, Exception) as e:
                self.logger.error(f'加载配置文件失败: {repr(e)}，恢复默认配置')
                return self.restore_default_config()

    def save_config(self) -> bool:
        """保存配置文件（输出标准无注释JSON）"""
        with self.cfg_lock:
            try:
                with open(self.cfg_file, 'w', encoding='utf-8') as f:
                    json.dump(self.cfgs, f, ensure_ascii=False, indent=4)
            except Exception as e:
                self.logger.error(f"保存配置失败: {e}")
                return False
            return True

    def restore_default_config(self, key_name='') -> bool:
        """恢复默认配置"""
        with self.cfg_lock:
            if key_name:
                self.cfgs[key_name] = self.deft_cfgs[key_name]
            else:
                self.cfgs = self.deft_cfgs.copy()  # 防止引用覆盖默认字典
            return self.save_config()


class Utils:
    @staticmethod
    def is_package_mode() -> bool:
        """是否是打包模式 (支持检测PyInstaller和Nuitka)"""
        return hasattr(sys, 'frozen') or globals().get('__compiled__', False)

    @staticmethod
    def get_bundle_dir() -> str:
        """获取程序数据文件夹"""
        # PyInstaller打包模式
        if hasattr(sys, 'frozen'):
            bundle_dir = sys._MEIPASS  # type: ignore
        # Nuitka打包模式
        elif globals().get('__compiled__', None):
            bundle_dir = ''
        # Python脚本模式
        else:
            bundle_dir = os.getcwd()
        return bundle_dir

    @staticmethod
    def get_program_path() -> str:
        """获取程序路径"""
        return os.path.abspath(sys.argv[0])

    @staticmethod
    def get_program_dir() -> str:
        """获取程序所在文件夹"""
        return os.path.dirname(Utils.get_program_path())

    @staticmethod
    def setup_except_hook(logger: LoggerManager) -> None:
        """安装故障处理器"""
        def handle_exception(exc_type: Type[BaseException], exc_value: BaseException, exc_traceback: TracebackType):
            """处理未捕获的异常"""
            stack_summary = []
            for frame in traceback.extract_tb(exc_traceback):
                colno = getattr(frame, "colno", None)
                stack_summary.append(
                    f'文件 {frame.filename}, 行号 {frame.lineno}, 列号 {colno}, 帧名 {frame.name}\n'
                )
                if frame.line:
                    stack_summary.append(f'  {frame.line}')
                else:
                    stack_summary.append('(无代码)')

            stack_lines = []
            for i, frame in enumerate(stack_summary):
                stack_lines.append(f"  [{i+1}] {frame}")
            formatted_stack = "\n".join(stack_lines)
            report = (
                f'• 异常类型: {exc_type.__name__}\n'
                f'• 错误信息: {str(exc_value)}\n'
                f'• 全部堆栈:\n{formatted_stack}'
            )
            err_msg = f'程序发生未知错误, 已崩溃\n{report}'

            logger.error(err_msg, exc_info=(
                exc_type, exc_value, exc_traceback))

        sys.excepthook = handle_exception

    @staticmethod
    def debug_memory_usage(logger: LoggerManager) -> None:
        """日志记录内存占用"""
        try:
            import psutil  # type: ignore
        except ImportError:
            logger.error('未安装psutil库, 无法记录内存占用')
            return
        mem = psutil.virtual_memory()
        process = psutil.Process()
        logger.debug(
            f'总内存: {round(mem.total/1048576, 2)} MB, '
            f'可用: {round(mem.available/1048576, 2)} MB, '
            f'程序占用: {round(process.memory_info().rss/1048576, 2)} MB'
        )

    @staticmethod
    def timing_debug_memory_usage(logger: LoggerManager, freq: int = 300) -> None:
        """定时输出内存使用信息到日志 (默认间隔: 5分钟)"""
        def _loop():
            while True:
                Utils.debug_memory_usage(logger)
                time.sleep(freq)

        thread = threading.Thread(
            target=_loop, name='timing_debug_memory_usage', daemon=True)
        thread.start()

    @staticmethod
    def sync_work_dir(assets_dir='assets') -> bool:
        """同步工作目录"""
        if Utils.is_package_mode():
            work_dir = Utils.get_program_dir()
        else:
            work_dir = os.path.join(os.getcwd(), assets_dir)

        if os.path.exists(work_dir):
            os.chdir(work_dir)
            return True
        return False

    @staticmethod
    def is_already_running(mutex_name) -> bool:
        """检查程序是否已经在运行"""
        from ctypes import windll
        windll.kernel32.CreateMutexW(None, False, mutex_name)
        if windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            return True
        return False

    @staticmethod
    def qt_setup_high_dpi(logger: LoggerManager) -> bool:
        """Qt适配高DPI (必须在QApplication注册前执行)"""
        try:
            from PyQt5.QtWidgets import QApplication # type: ignore
            from PyQt5.QtCore import Qt, QCoreApplication  # type: ignore

            os.putenv('QT_ENABLE_HIGHDPI_SCALING', '1')
            QApplication.setHighDpiScaleFactorRoundingPolicy(
                Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
            QCoreApplication.setAttribute(
                Qt.ApplicationAttribute.AA_EnableHighDpiScaling)
            QCoreApplication.setAttribute(
                Qt.ApplicationAttribute.AA_UseHighDpiPixmaps)
            logger.debug('已启用自动DPI模式')
            
        except ImportError:
            logger.error(f'未安装PyQt5')
            return False
        except Exception as e:
            logger.error(f'启动自动DPI失败: {repr(e)}')
            return False
        return True
