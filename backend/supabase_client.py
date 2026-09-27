import os
from pathlib import Path
from supabase import create_client, Client
from dotenv import load_dotenv

from logging_config import get_logger

logger = get_logger(__name__)

# Load backend/.env independently of the current working directory
load_dotenv(Path(__file__).with_name(".env"))

#: 占位值：与 .env.example 保持一致，视为「未配置」
_PLACEHOLDERS = {"", "YOUR_SUPABASE_URL_HERE", "YOUR_SUPABASE_ANON_KEY_HERE"}

url: str = os.getenv("SUPABASE_URL", "").strip()
key: str = os.getenv("SUPABASE_KEY", "").strip()

# 只有配置了真实值才初始化；未配置是**正常状态**（云存储是可选项），
# 不应该每次都打一条假的失败日志。
supabase: Client | None = None
if url not in _PLACEHOLDERS and key not in _PLACEHOLDERS:
    try:
        supabase = create_client(url, key)
        logger.info("Supabase 云存储已启用")
    except Exception as exc:
        logger.warning("Supabase 初始化失败，已回退为本地存储：%s", exc)
else:
    logger.debug("未配置 Supabase，使用本地 uploads/ 目录")
