"""
用户认证模块：手机号登录 + 短信验证码
支持演示模式（页面显示验证码）及阿里云短信（配置环境变量后启用）
"""

import hashlib
import hmac
import json
import os
import random
import re
import string
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import requests

# 数据根目录
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
USERS_DIR = DATA_DIR / "users"
CODES_FILE = DATA_DIR / "verification_codes.json"

# 验证码有效期（秒）
CODE_EXPIRE_SECONDS = 300
# 同一手机号发送间隔（秒）
SEND_COOLDOWN_SECONDS = 60
# 验证码长度
CODE_LENGTH = 6


def ensure_data_dirs():
    """确保数据目录存在"""
    USERS_DIR.mkdir(parents=True, exist_ok=True)


def validate_phone(phone: str) -> bool:
    """校验中国大陆手机号格式"""
    return bool(re.fullmatch(r"1[3-9]\d{9}", phone.strip()))


def mask_phone(phone: str) -> str:
    """手机号脱敏显示：138****8000"""
    phone = phone.strip()
    if len(phone) != 11:
        return phone
    return f"{phone[:3]}****{phone[-4:]}"


def get_user_data_file(phone: str) -> Path:
    """获取指定用户的 CSV 数据文件路径"""
    ensure_data_dirs()
    user_dir = USERS_DIR / phone
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir / "finance_records.csv"


def _load_codes() -> dict:
    """读取验证码缓存文件"""
    ensure_data_dirs()
    if not CODES_FILE.exists():
        return {}
    try:
        with open(CODES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_codes(codes: dict):
    """保存验证码缓存文件"""
    ensure_data_dirs()
    with open(CODES_FILE, "w", encoding="utf-8") as f:
        json.dump(codes, f, ensure_ascii=False, indent=2)


def _cleanup_expired_codes(codes: dict) -> dict:
    """清理已过期的验证码记录"""
    now = datetime.now()
    valid = {}
    for phone, info in codes.items():
        expires_at = datetime.fromisoformat(info["expires_at"])
        if expires_at > now:
            valid[phone] = info
    return valid


def generate_verification_code() -> str:
    """生成 6 位数字验证码"""
    return "".join(random.choices(string.digits, k=CODE_LENGTH))


def get_send_cooldown_remaining(phone: str) -> int:
    """获取距离可再次发送验证码的剩余秒数"""
    codes = _cleanup_expired_codes(_load_codes())
    info = codes.get(phone)
    if not info:
        return 0

    sent_at = datetime.fromisoformat(info["sent_at"])
    elapsed = (datetime.now() - sent_at).total_seconds()
    remaining = int(SEND_COOLDOWN_SECONDS - elapsed)
    return max(0, remaining)


def create_and_store_code(phone: str) -> tuple[str, int]:
    """
    生成并存储验证码
    返回 (验证码, 需等待秒数)；若仍在冷却期则 code 为空字符串
    """
    codes = _cleanup_expired_codes(_load_codes())
    remaining = get_send_cooldown_remaining(phone)
    if remaining > 0:
        return "", remaining

    code = generate_verification_code()
    now = datetime.now()
    codes[phone] = {
        "code": code,
        "sent_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=CODE_EXPIRE_SECONDS)).isoformat(),
    }
    _save_codes(codes)
    return code, 0


def verify_code(phone: str, code: str) -> tuple[bool, str]:
    """校验短信验证码，返回 (是否成功, 提示信息)"""
    codes = _cleanup_expired_codes(_load_codes())
    info = codes.get(phone)

    if not info:
        return False, "请先获取验证码"

    expires_at = datetime.fromisoformat(info["expires_at"])
    if datetime.now() > expires_at:
        return False, "验证码已过期，请重新获取"

    if code.strip() != info["code"]:
        return False, "验证码错误，请重新输入"

    # 验证成功后删除验证码，防止重复使用
    del codes[phone]
    _save_codes(codes)
    return True, "登录成功"


def is_demo_sms_mode() -> bool:
    """未配置短信服务时，使用演示模式（页面展示验证码）"""
    return os.environ.get("SMS_DEMO_MODE", "true").lower() != "false" and not _has_aliyun_sms_config()


def _has_aliyun_sms_config() -> bool:
    """检查是否配置了阿里云短信"""
    required = [
        "ALIYUN_ACCESS_KEY_ID",
        "ALIYUN_ACCESS_KEY_SECRET",
        "ALIYUN_SMS_SIGN_NAME",
        "ALIYUN_SMS_TEMPLATE_CODE",
    ]
    return all(os.environ.get(k) for k in required)


def _percent_encode(value: str) -> str:
    """阿里云 API 专用 URL 编码"""
    return quote(str(value), safe="~")


def _sign_aliyun(params: dict, access_key_secret: str) -> str:
    """计算阿里云 RPC 签名"""
    sorted_params = sorted(params.items())
    query = "&".join(f"{_percent_encode(k)}={_percent_encode(v)}" for k, v in sorted_params)
    string_to_sign = f"GET&%2F&{_percent_encode(query)}"
    key = f"{access_key_secret}&".encode("utf-8")
    signature = hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha1).digest()
    import base64

    return base64.b64encode(signature).decode("utf-8")


def send_sms_aliyun(phone: str, code: str) -> tuple[bool, str]:
    """通过阿里云短信服务发送验证码"""
    access_key_id = os.environ["ALIYUN_ACCESS_KEY_ID"]
    access_key_secret = os.environ["ALIYUN_ACCESS_KEY_SECRET"]
    sign_name = os.environ["ALIYUN_SMS_SIGN_NAME"]
    template_code = os.environ["ALIYUN_SMS_TEMPLATE_CODE"]

    params = {
        "AccessKeyId": access_key_id,
        "Action": "SendSms",
        "Format": "JSON",
        "PhoneNumbers": phone,
        "SignName": sign_name,
        "SignatureMethod": "HMAC-SHA1",
        "SignatureNonce": uuid.uuid4().hex,
        "SignatureVersion": "1.0",
        "TemplateCode": template_code,
        "TemplateParam": json.dumps({"code": code}, ensure_ascii=False),
        "Timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "Version": "2017-05-25",
    }
    params["Signature"] = _sign_aliyun(params, access_key_secret)

    try:
        response = requests.get(
            "https://dysmsapi.aliyuncs.com/",
            params=params,
            timeout=10,
        )
        result = response.json()
        if result.get("Code") == "OK":
            return True, "验证码已发送至您的手机"
        return False, result.get("Message", "短信发送失败")
    except Exception as exc:
        return False, f"短信发送异常：{exc}"


def send_verification_sms(phone: str, code: str) -> tuple[bool, str, bool]:
    """
    发送验证码短信
    返回 (是否成功, 提示信息, 是否演示模式)
    """
    if is_demo_sms_mode():
        return True, f"演示模式验证码：{code}", True

    success, message = send_sms_aliyun(phone, code)
    return success, message, False
