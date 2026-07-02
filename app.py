"""
AI财务小助手 - Streamlit 演示程序
支持自然语言记账、财务看板与智能节约建议
"""

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

from auth import (
    create_and_store_code,
    get_send_cooldown_remaining,
    get_user_data_file,
    mask_phone,
    send_verification_sms,
    validate_phone,
    verify_code,
)

# ==================== 常量配置 ====================

# 允许的支出类别
CATEGORIES = ["餐饮", "交通", "购物", "居住", "娱乐", "其他"]

# DeepSeek API 配置
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"

# 本地解析时的关键词映射（API 失败时降级使用）
CATEGORY_KEYWORDS = {
    "餐饮": ["饭", "吃", "餐", "外卖", "早餐", "午餐", "晚餐", "咖啡", "奶茶", "食堂"],
    "交通": ["车", "打车", "公交", "地铁", "出租", "滴滴", "高铁", "火车", "机票", "加油", "停车"],
    "购物": ["买", "购", "淘宝", "京东", "超市", "商场", "衣服", "鞋"],
    "居住": ["房租", "水电", "物业", "燃气", "宽带", "租金", "房贷"],
    "娱乐": ["电影", "游戏", "KTV", "旅游", "门票", "健身", "娱乐"],
}


# ==================== 页面配置与样式 ====================

def setup_page():
    """配置页面基础信息与全局样式"""
    st.set_page_config(
        page_title="AI财务小助手",
        page_icon="💰",
        layout="wide",
    )

    # 柔和蓝白配色 + 卡片风格
    st.markdown(
        """
        <style>
        /* 全局背景 */
        .stApp {
            background: linear-gradient(180deg, #f0f7ff 0%, #ffffff 100%);
        }

        /* 顶部标题区域 */
        .header-container {
            background: linear-gradient(135deg, #4a90d9 0%, #6eb5ff 100%);
            padding: 1.2rem 2rem;
            border-radius: 12px;
            margin-bottom: 1.5rem;
            box-shadow: 0 4px 15px rgba(74, 144, 217, 0.25);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .header-title {
            color: white;
            font-size: 2rem;
            font-weight: 700;
            margin: 0;
        }
        .header-datetime {
            color: rgba(255, 255, 255, 0.95);
            font-size: 1rem;
            text-align: right;
        }

        /* 侧边栏样式 */
        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, #e8f4fd 0%, #f5faff 100%);
        }
        [data-testid="stSidebar"] .stRadio label {
            font-size: 1.05rem;
        }

        /* 卡片容器 */
        div[data-testid="stVerticalBlockBorderWrapper"] {
            background: white;
            border-radius: 12px !important;
            box-shadow: 0 2px 12px rgba(74, 144, 217, 0.1);
            padding: 0.5rem;
        }

        /* 指标卡数字颜色 */
        [data-testid="stMetricValue"] {
            color: #2c6fad;
        }

        /* 按钮主色 */
        .stButton > button[kind="primary"] {
            background: linear-gradient(135deg, #4a90d9, #6eb5ff);
            border: none;
        }

        /* 登录页卡片 */
        .login-wrapper {
            max-width: 420px;
            margin: 2rem auto 0 auto;
        }
        .login-header {
            text-align: center;
            margin-bottom: 1.5rem;
        }
        .login-header h1 {
            color: #2c6fad;
            font-size: 1.8rem;
            margin-bottom: 0.3rem;
        }
        .login-header p {
            color: #6b8aad;
            font-size: 0.95rem;
        }
        .login-icon {
            font-size: 3rem;
            margin-bottom: 0.5rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_header(show_user: bool = False):
    """渲染页面顶部标题区域：左侧大标题，右侧当前日期时间与用户信息"""
    now = datetime.now()
    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    datetime_str = f"{now.strftime('%Y年%m月%d日')} {weekday_names[now.weekday()]} {now.strftime('%H:%M:%S')}"

    user_info = ""
    if show_user and st.session_state.get("user_phone"):
        user_info = (
            f"<div style='font-size:0.9rem;margin-top:0.3rem;'>"
            f"👤 {mask_phone(st.session_state.user_phone)}</div>"
        )

    st.markdown(
        f"""
        <div class="header-container">
            <div class="header-title">💰 AI财务小助手</div>
            <div class="header-datetime">
                {datetime_str}
                {user_info}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ==================== 登录与会话 ====================

def init_session_state():
    """初始化登录相关 session 状态"""
    defaults = {
        "logged_in": False,
        "user_phone": "",
        "login_phone_input": "",
        "demo_sms_code": "",
        "sms_message": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def logout():
    """退出登录并清除会话"""
    st.session_state.logged_in = False
    st.session_state.user_phone = ""
    st.session_state.demo_sms_code = ""
    st.session_state.sms_message = ""


def page_login():
    """手机号 + 短信验证码登录页"""
    st.markdown(
        """
        <div class="login-wrapper">
            <div class="login-header">
                <div class="login-icon">💰</div>
                <h1>AI财务小助手</h1>
                <p>手机号登录，安全守护您的财务数据</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    _left, center, _right = st.columns([1, 1.2, 1])
    with center:
        with st.container(border=True):
            phone = st.text_input(
                "手机号",
                placeholder="请输入11位手机号",
                max_chars=11,
                key="login_phone_input",
            )

            code_col, send_col = st.columns([2, 1])
            with code_col:
                sms_code = st.text_input(
                    "验证码",
                    placeholder="请输入6位验证码",
                    max_chars=6,
                )
            with send_col:
                st.markdown("<div style='height: 1.6rem;'></div>", unsafe_allow_html=True)
                cooldown = get_send_cooldown_remaining(phone.strip()) if phone else 0
                send_label = f"⏳ {cooldown}s" if cooldown > 0 else "📲 获取验证码"
                send_disabled = cooldown > 0
                if st.button(send_label, use_container_width=True, disabled=send_disabled):
                    if not validate_phone(phone):
                        st.error("请输入正确的11位手机号")
                    else:
                        code, wait_seconds = create_and_store_code(phone.strip())
                        if wait_seconds > 0:
                            st.warning(f"请 {wait_seconds} 秒后再试")
                        else:
                            success, message, is_demo = send_verification_sms(
                                phone.strip(), code
                            )
                            if success:
                                st.session_state.sms_message = message
                                if is_demo:
                                    st.session_state.demo_sms_code = code
                                st.success(
                                    "验证码已发送"
                                    if not is_demo
                                    else f"📱 演示模式：验证码 **{code}**（正式环境将发送至手机）"
                                )
                            else:
                                st.error(message)

            # 展示最近一次发送提示
            if st.session_state.get("sms_message") and st.session_state.get("demo_sms_code"):
                st.caption(
                    f"当前演示验证码：{st.session_state.demo_sms_code}（5分钟内有效）"
                )

            if st.button("🔐 登录", type="primary", use_container_width=True):
                if not validate_phone(phone):
                    st.error("请输入正确的11位手机号")
                elif not sms_code or len(sms_code.strip()) != 6:
                    st.error("请输入6位验证码")
                else:
                    ok, msg = verify_code(phone.strip(), sms_code.strip())
                    if ok:
                        st.session_state.logged_in = True
                        st.session_state.user_phone = phone.strip()
                        st.session_state.demo_sms_code = ""
                        st.session_state.sms_message = ""
                        st.rerun()
                    else:
                        st.error(msg)

            st.caption("首次登录将自动创建账户，每位用户数据独立存储")


# ==================== 数据读写 ====================

def get_current_data_file() -> Path:
    """获取当前登录用户的 CSV 文件路径"""
    phone = st.session_state.get("user_phone", "")
    return get_user_data_file(phone)

def load_records() -> pd.DataFrame:
    """从当前用户 CSV 加载记账记录，启动时自动读取"""
    data_file = get_current_data_file()
    columns = ["日期", "金额", "类别", "描述"]
    if data_file.exists():
        try:
            df = pd.read_csv(data_file, encoding="utf-8-sig")
            # 确保列完整
            for col in columns:
                if col not in df.columns:
                    df[col] = ""
            return df[columns]
        except Exception:
            pass
    return pd.DataFrame(columns=columns)


def save_records(df: pd.DataFrame):
    """将记账记录保存到当前用户 CSV"""
    df.to_csv(get_current_data_file(), index=False, encoding="utf-8-sig")


def add_records(records: list[dict], fallback_description: str = ""):
    """批量新增记账记录并持久化"""
    if not records:
        return load_records()

    df = load_records()
    new_rows = []
    for record in records:
        new_rows.append(
            {
                "日期": record["date"],
                "金额": record["amount"],
                "类别": record["category"],
                "描述": record.get("description") or fallback_description,
            }
        )
    df = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)
    save_records(df)
    return df


# ==================== AI 解析与降级 ====================

def get_api_key() -> str:
    """获取 DeepSeek API Key（优先环境变量，其次侧边栏输入）"""
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key and "deepseek_api_key" in st.session_state:
        key = st.session_state.deepseek_api_key
    return key


def normalize_expense(item: dict, default_description: str = "") -> dict | None:
    """校验并规范化单条支出记录"""
    try:
        amount = float(item.get("amount", 0))
    except (TypeError, ValueError):
        return None

    category = item.get("category", "其他")
    date_str = item.get("date", datetime.now().strftime("%Y-%m-%d"))
    description = item.get("description") or default_description

    if category not in CATEGORIES:
        category = "其他"
    if amount <= 0:
        return None

    return {
        "amount": amount,
        "category": category,
        "date": date_str,
        "description": description,
    }


def parse_with_deepseek(text: str, api_key: str) -> list[dict] | None:
    """
    调用 DeepSeek API 解析自然语言记账内容（支持一次识别多笔）
    返回格式：[{"amount": float, "category": str, "date": str, "description": str}, ...]
    """
    system_prompt = (
        "你是一个财务记账助手。用户会用自然语言描述一笔或多笔支出，"
        "请识别每一笔独立支出，严格返回 JSON 数组，不要包含任何其他文字或 markdown："
        '[{"amount": 数字, "category": "类别", "date": "YYYY-MM-DD", "description": "该笔简短描述"}, ...]。'
        f"类别只能是以下之一：{', '.join(CATEGORIES)}。"
        "如果只描述一笔，也返回只有一个元素的数组。"
        "如果用户没有明确日期，使用今天的日期。"
        "amount 必须是正数。"
        "description 用一句话概括该笔支出。"
    )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        "temperature": 0.1,
    }

    try:
        response = requests.post(
            DEEPSEEK_API_URL,
            headers=headers,
            json=payload,
            timeout=20,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"].strip()

        # 去除可能的 markdown 代码块包裹
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)

        parsed = json.loads(content)
        # 兼容 AI 偶尔返回单个对象的情况
        if isinstance(parsed, dict):
            parsed = [parsed]

        results = []
        for item in parsed:
            normalized = normalize_expense(item, default_description=text.strip())
            if normalized:
                results.append(normalized)

        return results if results else None
    except Exception:
        return None


def detect_date(text: str) -> str:
    """从文本中提取日期，默认返回今天"""
    date_str = datetime.now().strftime("%Y-%m-%d")
    date_match = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", text)
    if date_match:
        y, m, d = date_match.groups()
        date_str = f"{y}-{int(m):02d}-{int(d):02d}"
    elif "昨天" in text:
        date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    elif "前天" in text:
        date_str = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    return date_str


def extract_amount(segment: str) -> float:
    """从文本片段中提取金额"""
    amount_patterns = [
        r"(\d+(?:\.\d+)?)\s*元",
        r"[￥¥]\s*(\d+(?:\.\d+)?)",
        r"花了\s*(\d+(?:\.\d+)?)",
        r"(\d+(?:\.\d+)?)\s*块",
    ]
    for pattern in amount_patterns:
        matches = re.findall(pattern, segment)
        if matches:
            return float(matches[-1])

    nums = re.findall(r"\d+(?:\.\d+)?", segment)
    return float(nums[-1]) if nums else 0.0


def match_category(segment: str) -> str:
    """根据关键词匹配支出类别"""
    for cat, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in segment for kw in keywords):
            return cat
    return "其他"


def split_expense_segments(text: str) -> list[str]:
    """将一段文本拆分为多笔支出片段"""
    segments = re.split(r"[，,；;\n、]|(?:还有|然后|以及|另外|再|又)", text)
    return [seg.strip() for seg in segments if seg.strip()]


def parse_with_regex(text: str) -> list[dict]:
    """
    本地降级解析：支持一次识别多笔，按分隔符拆分后逐段提取金额和类别
    """
    date_str = detect_date(text)
    segments = split_expense_segments(text)

    # 只有一段时，按单笔处理
    if len(segments) <= 1:
        amount = extract_amount(text)
        return [
            {
                "amount": amount,
                "category": match_category(text),
                "date": date_str,
                "description": text.strip(),
            }
        ]

    results = []
    for segment in segments:
        amount = extract_amount(segment)
        if amount <= 0:
            continue
        results.append(
            {
                "amount": amount,
                "category": match_category(segment),
                "date": date_str,
                "description": segment,
            }
        )

    # 拆分后未识别到有效金额时，退回整段文本解析
    if not results:
        amount = extract_amount(text)
        return [
            {
                "amount": amount,
                "category": match_category(text),
                "date": date_str,
                "description": text.strip(),
            }
        ]

    return results


def parse_expense(text: str) -> tuple[list[dict], bool]:
    """
    解析支出描述（支持多笔）
    返回 (解析结果列表, 是否使用了本地降级模式)
    """
    api_key = get_api_key()
    if api_key:
        results = parse_with_deepseek(text, api_key)
        if results:
            return results, False

    # API 失败或无 Key 时降级
    return parse_with_regex(text), True


# ==================== 记账页面 ====================

def page_record():
    """📝 记账页面"""
    with st.container(border=True):
        st.subheader("📝 自然语言记账")
        st.caption("支持一次输入多笔支出，AI 会自动识别每笔的金额、类别和日期")

        # 大输入框
        user_input = st.text_area(
            "支出描述",
            placeholder="例如：今天打车上班花了25.5元，中午外卖35元，晚上看电影80块",
            height=120,
            label_visibility="collapsed",
        )

        # 记录按钮
        if st.button("💾 记录这笔", type="primary", use_container_width=True):
            if not user_input or not user_input.strip():
                st.warning("请先输入支出描述哦～")
            else:
                results, used_fallback = parse_expense(user_input.strip())
                valid_results = [r for r in results if r["amount"] > 0]

                if not valid_results:
                    st.error("未能识别有效金额，请检查输入格式（如：花了25.5元）")
                else:
                    add_records(valid_results, fallback_description=user_input.strip())
                    total_amount = sum(r["amount"] for r in valid_results)

                    if len(valid_results) == 1:
                        r = valid_results[0]
                        st.success(
                            f"✅ 记录成功！{r['date']} | "
                            f"¥{r['amount']:.2f} | {r['category']}"
                        )
                    else:
                        st.success(
                            f"✅ 成功记录 {len(valid_results)} 笔，合计 ¥{total_amount:.2f}"
                        )
                        for r in valid_results:
                            desc = r.get("description", "")
                            st.markdown(
                                f"- {r['date']} · **{r['category']}** · "
                                f"¥{r['amount']:.2f} · {desc}"
                            )

                    if used_fallback:
                        st.info("ℹ️ 已使用本地解析模式")

    # 今日统计
    df = load_records()
    today = datetime.now().strftime("%Y-%m-%d")
    today_df = df[df["日期"] == today] if not df.empty else df

    count = len(today_df)
    total = today_df["金额"].sum() if count > 0 else 0.0

    st.markdown("---")
    with st.container(border=True):
        st.markdown(
            f"### 📅 今日已记 **{count}** 笔，共 **¥{total:.2f}** 元"
        )


# ==================== 财务看板页面 ====================

def get_month_data(df: pd.DataFrame) -> pd.DataFrame:
    """筛选本月数据"""
    if df.empty:
        return df
    now = datetime.now()
    month_start = now.replace(day=1).strftime("%Y-%m-%d")
    return df[df["日期"] >= month_start].copy()


def get_savings_advice(df: pd.DataFrame) -> str:
    """根据分类占比生成 AI 智能节约建议"""
    if df.empty:
        return ""

    total = df["金额"].sum()
    if total <= 0:
        return "✅ 消费结构合理，继续保持良好的记账习惯！"

    category_totals = df.groupby("类别")["金额"].sum()
    transport_ratio = category_totals.get("交通", 0) / total
    food_ratio = category_totals.get("餐饮", 0) / total

    if transport_ratio > 0.30:
        transport_amount = category_totals.get("交通", 0)
        save_estimate = transport_amount * 0.25  # 估算可节省 25%
        return (
            f"🚗 交通支出占比较高（{transport_ratio * 100:.1f}%），"
            f"建议多使用地铁或拼车，每月可节省约 **¥{save_estimate:.0f}** 元。"
        )
    if food_ratio > 0.40:
        return (
            f"🍜 餐饮支出偏高（{food_ratio * 100:.1f}%），"
            "建议减少外卖，每周自己做饭 2-3 次。"
        )
    return "✅ 消费结构合理，继续保持良好的记账习惯！"


def page_dashboard():
    """📊 财务看板页面"""
    df = load_records()

    if df.empty:
        st.info("还没有记账记录，先去记一笔吧～")
        return

    month_df = get_month_data(df)

    if month_df.empty:
        st.info("本月还没有记账记录，先去记一笔吧～")
        return

    # 确保金额为数值类型
    month_df["金额"] = pd.to_numeric(month_df["金额"], errors="coerce").fillna(0)

    # ---------- 顶部三个指标卡 ----------
    total_expense = month_df["金额"].sum()
    total_count = len(month_df)
    days_in_month = datetime.now().day
    daily_avg = total_expense / days_in_month if days_in_month > 0 else 0

    col1, col2, col3 = st.columns(3)
    with col1:
        with st.container(border=True):
            st.metric("本月总支出", f"¥{total_expense:,.2f}")
    with col2:
        with st.container(border=True):
            st.metric("本月总笔数", f"{total_count} 笔")
    with col3:
        with st.container(border=True):
            st.metric("日均消费", f"¥{daily_avg:,.2f}")

    st.markdown("")

    # ---------- 中间两个图表 ----------
    chart_col1, chart_col2 = st.columns(2)

    with chart_col1:
        with st.container(border=True):
            st.subheader("各分类占比")
            category_sum = month_df.groupby("类别")["金额"].sum().reset_index()
            if category_sum["金额"].sum() > 0:
                fig_pie = px.pie(
                    category_sum,
                    values="金额",
                    names="类别",
                    hole=0.4,
                    color_discrete_sequence=px.colors.sequential.Blues_r,
                )
                fig_pie.update_traces(
                    textposition="inside",
                    textinfo="percent+label",
                    hovertemplate="%{label}<br>¥%{value:.2f}<br>占比 %{percent}<extra></extra>",
                )
                fig_pie.update_layout(
                    margin=dict(t=20, b=20, l=20, r=20),
                    showlegend=False,
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                )
                st.plotly_chart(fig_pie, use_container_width=True)
            else:
                st.caption("暂无分类数据")

    with chart_col2:
        with st.container(border=True):
            st.subheader("近7天每日支出趋势")
            # 构造近 7 天日期序列
            today = datetime.now().date()
            date_range = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]

            # 全量数据按日汇总（不限本月，以便跨月也能看到趋势）
            df["金额"] = pd.to_numeric(df["金额"], errors="coerce").fillna(0)
            daily_sum = df.groupby("日期")["金额"].sum()

            trend_data = pd.DataFrame(
                {
                    "日期": date_range,
                    "支出": [daily_sum.get(d, 0) for d in date_range],
                }
            )
            trend_data["日期显示"] = pd.to_datetime(trend_data["日期"]).dt.strftime("%m/%d")

            fig_line = go.Figure()
            fig_line.add_trace(
                go.Scatter(
                    x=trend_data["日期显示"],
                    y=trend_data["支出"],
                    mode="lines+markers",
                    line=dict(color="#4a90d9", width=3),
                    marker=dict(size=8, color="#6eb5ff"),
                    fill="tozeroy",
                    fillcolor="rgba(74, 144, 217, 0.15)",
                    hovertemplate="%{x}<br>¥%{y:.2f}<extra></extra>",
                )
            )
            fig_line.update_layout(
                margin=dict(t=20, b=20, l=20, r=20),
                xaxis_title="日期",
                yaxis_title="支出（元）",
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                yaxis=dict(gridcolor="rgba(74,144,217,0.15)"),
            )
            st.plotly_chart(fig_line, use_container_width=True)

    # ---------- 底部 AI 节约建议 ----------
    advice = get_savings_advice(month_df)
    st.info(advice)


# ==================== 主程序入口 ====================

def main():
    """应用主入口"""
    setup_page()
    init_session_state()

    # 未登录时显示登录页
    if not st.session_state.logged_in:
        page_login()
        return

    render_header(show_user=True)

    # 侧边栏导航
    with st.sidebar:
        st.markdown("### 👤 账户")
        st.markdown(f"**{mask_phone(st.session_state.user_phone)}**")
        if st.button("🚪 退出登录", use_container_width=True):
            logout()
            st.rerun()

        st.markdown("---")
        st.markdown("### 🧭 导航菜单")
        menu = st.radio(
            "选择功能",
            ["📝 记账", "📊 财务看板"],
            label_visibility="collapsed",
        )
        st.markdown("---")
        st.markdown("#### 🔑 API 设置")
        st.text_input(
            "DeepSeek API Key",
            type="password",
            key="deepseek_api_key",
            placeholder="sk-...（可选，留空使用本地解析）",
            help="也可通过环境变量 DEEPSEEK_API_KEY 配置",
        )
        st.caption("未配置 API Key 时将自动使用本地正则解析")

    # 根据菜单渲染对应页面
    if menu == "📝 记账":
        page_record()
    else:
        page_dashboard()


if __name__ == "__main__":
    main()
