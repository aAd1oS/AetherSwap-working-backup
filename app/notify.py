import imaplib
import email
from html.parser import HTMLParser
import threading
import time
from email.header import decode_header
from urllib.parse import quote, urlparse
from typing import Callable, Optional
import requests


def send_pushplus(token: str, title: str, content: str, template: str = "html") -> bool:
    if not token or not token.strip():
        return False
    try:
        r = requests.post(
            "https://www.pushplus.plus/send",
            json={"token": token.strip(), "title": title, "content": content, "template": template},
            timeout=10,
        )
        return r.status_code == 200
    except Exception:
        return False


class _NotificationTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag.lower() in {"br", "p", "div", "li"}:
            self.parts.append("\n")
        if tag.lower() == "a":
            href = dict(attrs).get("href")
            self.links.append((href or "").strip())

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self.links:
            href = self.links.pop()
            if href:
                self.parts.append(f" ({href})")
        elif tag.lower() in {"p", "div", "li"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _notification_plain_text(content: str) -> str:
    parser = _NotificationTextParser()
    parser.feed(str(content or ""))
    lines = [line.strip() for line in "".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line)


def _is_allowed_lark_webhook(webhook: str) -> bool:
    try:
        parsed = urlparse(webhook.strip())
    except Exception:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname in {"open.larksuite.com", "open.feishu.cn"}
        and parsed.path.startswith("/open-apis/bot/v2/hook/")
        and bool(parsed.path.removeprefix("/open-apis/bot/v2/hook/").strip("/"))
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
    )


def send_lark(webhook: str, title: str, content: str) -> bool:
    webhook = (webhook or "").strip()
    if not _is_allowed_lark_webhook(webhook):
        return False
    text = _notification_plain_text(content)
    message = f"AetherSwap\n{title.strip()}"
    if text:
        message += f"\n\n{text}"
    try:
        response = requests.post(
            webhook,
            json={"msg_type": "text", "content": {"text": message}},
            timeout=10,
        )
        if response.status_code != 200:
            return False
        payload = response.json()
        return payload.get("code") == 0
    except Exception:
        return False


def send_configured_notification(notify_cfg: dict, title: str, content: str) -> tuple[bool, str]:
    """Send through Lark first, then fall back to PushPlus if needed."""
    notify_cfg = notify_cfg or {}
    lark_webhook = (notify_cfg.get("lark_webhook") or "").strip()
    if lark_webhook and send_lark(lark_webhook, title, content):
        return True, "Lark"
    pushplus_token = (notify_cfg.get("pushplus_token") or "").strip()
    if pushplus_token and send_pushplus(pushplus_token, title, content):
        return True, "PushPlus"
    return False, ""


def notify_buff_request_protection(event: str, reason: str) -> bool:
    """Send one BUFF protection state transition through Lark only."""
    from app.config_loader import load_app_config_validated

    cfg = load_app_config_validated()
    webhook = str((cfg.get("notify") or {}).get("lark_webhook") or "").strip()
    if not webhook:
        return False
    pipeline_cfg = cfg.get("pipeline") or {}
    normal_cap = int(pipeline_cfg.get("iflow_top_n", 30) or 30)
    recovery_cap = int(pipeline_cfg.get("buff_protection_recovery_candidate_cap", 30) or 30)
    recovery_cap = max(recovery_cap, 1)
    effective_recovery_cap = min(max(normal_cap, 1), recovery_cap)
    titles = {
        "triggered": "BUFF 请求保护已触发",
        "manual_recovered": "BUFF 请求保护已解除",
        "pause_recovered": "BUFF 临时请求保护已解除",
        "recovery_finished": "BUFF 恢复观察期已结束",
    }
    title = titles.get(event, "BUFF 请求保护状态变化")
    content = (
        f"事件：{title}<br/>"
        f"原因：{reason}<br/>"
        f"正常每轮数量：{normal_cap}<br/>"
        f"保护恢复期每轮数量：{effective_recovery_cap}"
    )
    return send_lark(webhook, title, content)


_last_manual_notify_time: dict = {}
_last_manual_notify_lock = threading.Lock()  
def notify_manual_intervention_required(platform: str, reason: str) -> bool:
    """
    Sends a configured notification when manual intervention is required (e.g., login expired).
    Includes rate-limiting per platform (max 1 notify per 4 hours).
    """
    from app.config_loader import load_app_config_validated
    now = time.time()
    with _last_manual_notify_lock:
        last_time = _last_manual_notify_time.get(platform, 0)
        if now - last_time < 14400:
            return False
        _last_manual_notify_time[platform] = now
    cfg = load_app_config_validated()
    notify_cfg = cfg.get("notify") or {}
    if not (notify_cfg.get("lark_webhook") or notify_cfg.get("pushplus_token")):
        with _last_manual_notify_lock:
            _last_manual_notify_time[platform] = last_time
        return False
    title = f"[自动挂刀报警] {platform} 平台需要手动介入"
    content = (
        f"<b>报警平台:</b> {platform}<br/><br/>"
        f"<b>原因说明:</b><br/>"
        f"<span style='color: red;'>{reason}</span><br/><br/>"
        f"为了避免被限制或错失交易，请尽快前往图形界面或浏览器完成手动登录与验证操作。"
    )
    success, _channel = send_configured_notification(notify_cfg, title, content)
    if not success:
        with _last_manual_notify_lock:
            _last_manual_notify_time[platform] = last_time
    return success
def build_payment_notify_content(
    name: str,
    price: float,
    pay_url: str,
    pay_type: str,
    acc: float,
    sell_ratio: Optional[float] = None,
    num: int = 1,
    value_ratio: Optional[float] = None,
    steam_market_hash_name: Optional[str] = None,
    steam_app_id: int = 730,
    steam_link: Optional[str] = None,
) -> str:
    ratio_str = ""
    if value_ratio is not None:
        ratio_str = f"<br/>与最高折扣对比: {value_ratio:.4f}"
    elif sell_ratio is not None:
        ratio_str = f"<br/>最优寄售: {sell_ratio:.2%}"
    total = price * num
    pay_label = "支付宝" if (pay_type or "").lower() in ("alipay", "支付宝") else "微信"
    lines = [
        f"<b>物品</b>: {name}<br/>",
        f"<b>购买单价</b>: {price:.2f} 元<br/>",
        f"<b>数量</b>: {num}<br/>",
        f"<b>总价</b>: {total:.2f} 元{ratio_str}<br/>",
        f"<b>当前累计</b>: {acc + total:.2f} 元<br/>",
        f"<b>付款方式</b>: {pay_label}<br/>",
        f"<a href=\"{pay_url}\">点击付款链接</a>",
    ]
    if steam_link and (steam_link := steam_link.strip()):
        lines.append(f"<br/><a href=\"{steam_link}\">Steam 市场链接</a>")
    elif steam_market_hash_name and (steam_market_hash_name := steam_market_hash_name.strip()):
        market_url = f"https://steamcommunity.com/market/listings/{steam_app_id}/{quote(steam_market_hash_name, safe='')}"
        lines.append(f"<br/><a href=\"{market_url}\">Steam 市场链接</a>")
    return "".join(lines)
def wait_email_command(
    cfg: dict,
    timeout_seconds: int = 300,
    is_stop_requested: Optional[Callable[[], bool]] = None,
    log_fn: Optional[Callable[[str, str], None]] = None,
) -> str:
    n = cfg.get("notify") or {}
    user = (n.get("email_user") or "").strip()
    passwd = (n.get("email_pass") or "").strip()
    server = (n.get("imap_server") or "imap.qq.com").strip()
    target_sender = (n.get("target_sender") or "").strip()
    subject_success = (n.get("subject_success") or "已确认成功付款").strip()
    subject_fail = (n.get("subject_fail") or "已确认付款失败").strip()
    allowed_sender = (n.get("allowed_sender") or "").strip()
    if not user or not passwd:
        return "timeout"
    start = time.time()
    timeout = min(timeout_seconds, 86400)
    if log_fn:
        log_fn(f"监听邮箱指令 发件人含「{target_sender}」 成功标题「{subject_success}」 失败标题「{subject_fail}」", "info")
    while time.time() - start < timeout:
        if is_stop_requested and is_stop_requested():
            return "timeout"
        mail = None
        try:
            mail = imaplib.IMAP4_SSL(server)
            mail.login(user, passwd)
            mail.select("inbox")
            status, messages = mail.search(None, "UNSEEN")
            email_ids = (messages or [b""])[0].split()
            if not email_ids:
                time.sleep(3)
                continue
            latest_id = email_ids[-1]
            _, msg_data = mail.fetch(latest_id, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            sender_header = msg.get("From", "")
            sender_name, sender_addr = email.utils.parseaddr(sender_header)
            full_sender = f"{sender_name} {sender_addr}"
            subject_parts = decode_header(msg.get("Subject", ""))
            parts = []
            for part, encoding in subject_parts:
                if isinstance(part, bytes):
                    enc = (encoding or "utf-8").strip().lower()
                    if enc in ("unknown-8bit", "unknown", ""):
                        enc = "utf-8"
                    try:
                        parts.append(part.decode(enc, errors="replace"))
                    except LookupError:
                        parts.append(part.decode("utf-8", errors="replace"))
                else:
                    parts.append(str(part))
            subject_str = "".join(parts)
            if log_fn:
                log_fn(f"收到邮件 发件人={full_sender[:50]} 标题={subject_str[:40]}", "info")
            is_sender_ok = target_sender.lower() in full_sender.lower() if target_sender else True
            if allowed_sender and sender_addr:
                is_sender_ok = is_sender_ok and (allowed_sender.lower() in (sender_addr or "").lower())
            if not is_sender_ok:
                mail.store(latest_id, "+FLAGS", "\\Seen")
                mail.expunge()
                time.sleep(3)
                continue
            if subject_success and subject_success in subject_str:
                mail.store(latest_id, "+FLAGS", "\\Deleted")
                mail.expunge()
                if log_fn:
                    log_fn("邮件指令: 已确认成功付款", "info")
                return "success"
            if subject_fail and subject_fail in subject_str:
                mail.store(latest_id, "+FLAGS", "\\Deleted")
                mail.expunge()
                if log_fn:
                    log_fn("邮件指令: 已确认付款失败", "info")
                return "fail"
            mail.store(latest_id, "+FLAGS", "\\Seen")
            mail.expunge()
        except Exception as e:
            if log_fn:
                log_fn(f"邮箱连接/解析异常: {e}", "warn")
        finally:
            if mail is not None:
                try:
                    mail.logout()
                except Exception:
                    pass
            mail = None
        time.sleep(3)
    if log_fn:
        log_fn("邮箱等待超时", "warn")
    return "timeout"
def compute_holdings_stats(holdings: list, resell_ratio: float = 0.85) -> tuple:
    ratio = max(0.01, min(1, float(resell_ratio) or 0.85))
    total_price = sum(float(t.get("price", 0) or 0) for t in holdings)
    total_mp = sum(float(t.get("market_price", 0) or 0) for t in holdings if t.get("market_price") is not None)
    has_cmp = any(t.get("current_market_price") is not None for t in holdings)
    total_cmp = sum(float(t.get("current_market_price", 0) or 0) for t in holdings) if has_cmp else None
    change_total_mp = 0.0
    change_total_cmp = 0.0
    for t in holdings:
        if t.get("market_price") is None or t.get("current_market_price") is None:
            continue
        mp = float(t.get("market_price") or 0)
        cmp = float(t.get("current_market_price") or 0)
        if mp <= 0:
            continue
        change_total_mp += mp
        change_total_cmp += cmp
    pl = (change_total_cmp - change_total_mp) if change_total_mp > 0 else None
    pl_pct = (pl / change_total_mp * 100) if pl is not None and change_total_mp > 0 else None
    return total_price, total_mp, total_cmp, pl, pl_pct, ratio
def build_holdings_report_content(
    holdings: list,
    resell_ratio: float = 0.85,
) -> str:
    total_price, total_mp, total_cmp, pl, pl_pct, ratio = compute_holdings_stats(holdings, resell_ratio)
    total_after_tax = total_cmp / 1.15 if total_cmp and total_cmp > 0 else None
    discount_ratio = (total_price / total_after_tax) if total_after_tax and total_after_tax > 0 and total_price > 0 else None
    cash_profit = (total_after_tax * ratio - total_price) if total_after_tax is not None and total_price >= 0 else None
    self_use_profit = (total_after_tax - total_price) if total_after_tax is not None else None
    lines = [
        f"总购入价 {total_price:.2f}",
        f"总购入市场价 {total_mp:.2f}",
        f"总现市场价 {total_cmp:.2f}" if total_cmp is not None else "总现市场价 —",
        f"总税后价格 {total_after_tax:.2f}" if total_after_tax is not None else "总税后价格 —",
        f"实际折扣比率 {discount_ratio:.4f}" if discount_ratio is not None else "实际折扣比率 —",
        f"总变现收益 {'+' if cash_profit and cash_profit >= 0 else ''}{cash_profit:.2f}" if cash_profit is not None else "总变现收益 —",
        f"总自用收益 {'+' if self_use_profit and self_use_profit >= 0 else ''}{self_use_profit:.2f}" if self_use_profit is not None else "总自用收益 —",
    ]
    if pl is not None and pl_pct is not None:
        lines.append(f"总市场变动 {'+' if pl >= 0 else ''}{pl:.2f} ({'+' if pl_pct >= 0 else ''}{pl_pct:.2f}%)")
    else:
        lines.append("总市场变动 —")
    return "<br/>".join(lines)
