import asyncio
import json
import os
import sys
import aiofiles
import portalocker

HOST = None
PORT = None
_save_lock = asyncio.Lock()
def configure_host_port(host, port):
    global HOST, PORT
    HOST = host
    PORT = port
def get_host():
    return HOST or "127.0.0.1"  # 提供默认值
def get_port():
    return PORT or 3456

def in_docker():
    def check_cgroup():
        try:
            with open('/proc/1/cgroup', 'rt',encoding='utf-8') as ifh:
                for line in ifh:
                    if 'docker' in line or 'container' in line:
                        return True
        except FileNotFoundError:
            pass
        return False

    def check_dockerenv():
        try:
            with open('/.dockerenv', 'rt',encoding='utf-8') as ifh:
                # 文件存在即表示是在Docker容器中
                return True
        except FileNotFoundError:
            return False

    def check_proc_self_status():
        try:
            with open('/proc/self/status', 'rt',encoding='utf-8') as ifh:
                for line in ifh:
                    if line.startswith('Context') and 'container=docker' in line:
                        return True
        except FileNotFoundError:
            pass
        return False
    
    return any([check_cgroup(), check_dockerenv(), check_proc_self_status()])

def get_base_path():
    """判断当前是开发环境还是打包环境，返回基础路径"""
    if getattr(sys, 'frozen', False):
        # 打包后，资源在 sys._MEIPASS 指向的临时目录
        return sys._MEIPASS
    else:
        # 开发环境使用当前工作目录
        return os.path.abspath(".")
base_path = get_base_path()
CONFIG_BASE_PATH = os.path.join(base_path, 'config')
os.makedirs(CONFIG_BASE_PATH, exist_ok=True)
SETTINGS_FILE = os.path.join(CONFIG_BASE_PATH, 'settings.json')
SETTINGS_TEMPLATE_FILE = os.path.join(CONFIG_BASE_PATH, 'settings_template.json')
with open(SETTINGS_TEMPLATE_FILE, 'r', encoding='utf-8') as f:
    default_settings = json.load(f)

async def load_settings():
    async with _save_lock:  # 等待所有写入操作完成
        try:
            async with aiofiles.open(SETTINGS_FILE, mode='r', encoding='utf-8') as f:
                contents = await f.read()
                settings = json.loads(contents)
            # 补充缺失的字段（包括嵌套字段）
            def merge_defaults(default, target):
                for key, value in default.items():
                    if key not in target:
                        target[key] = value
                    elif isinstance(value, dict):
                        merge_defaults(value, target[key])
            merge_defaults(default_settings, settings)
            # 设置 isdocker 字段
            if in_docker():
                settings['isdocker'] = True
            return settings
        except FileNotFoundError:
            # 首次运行，创建配置文件
            settings = default_settings.copy()
            if in_docker():
                settings['isdocker'] = True
            await save_settings(settings)
            return settings


async def save_settings(settings):
    async with _save_lock:
        loop = asyncio.get_event_loop()
        async with aiofiles.open(SETTINGS_FILE, mode='w', encoding='utf-8') as af:
            # 获取底层文件描述符
            raw_file = af._file.buffer.raw  # 访问aiofiles内部原始文件对象
            fd = raw_file.fileno()
            
            # 使用文件描述符加锁
            await loop.run_in_executor(
                None, 
                lambda: portalocker.lock(raw_file, portalocker.LOCK_EX)
            )
            try:
                await af.write(json.dumps(settings, ensure_ascii=False, indent=2))
            finally:
                await loop.run_in_executor(
                    None, 
                    lambda: portalocker.unlock(raw_file)
                )