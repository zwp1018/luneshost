#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import re
import platform
import logging
import requests
from datetime import datetime
from pathlib import Path
from typing import Optional

from seleniumbase import SB
from seleniumbase.common.exceptions import TimeoutException

# ================== 核心配置 ==================
BETADASH_LOGIN_URL = "https://betadash.lunes.host/login"
PROXY = "socks5://127.0.0.1:40000"

OUTPUT_DIR = Path("output/screenshots")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("lunes-keep")

# ================== 辅助函数 ==================
def is_linux() -> bool:
    return platform.system().lower() == "linux"

def mask_url(url: str) -> str:
    return re.sub(r'/servers/\d+', '/servers/***', url)

def mask_email(email: str) -> str:
    if '@' not in email:
        return email[:1] + "***"
    local, domain = email.split('@', 1)
    masked_local = local[:1] + "***" if local else "***"
    if '.' in domain:
        parts = domain.split('.')
        tld = parts[-1]
        first_char = domain[0]
        masked_domain = f"{first_char}***.{tld}" if len(parts) > 1 else f"{first_char}***"
    else:
        masked_domain = domain[:1] + "***"
    return f"{masked_local}@{masked_domain}"

def setup_display():
    if is_linux() and not os.environ.get("DISPLAY"):
        try:
            from pyvirtualdisplay import Display
            display = Display(visible=False, size=(1920, 1080))
            display.start()
            os.environ["DISPLAY"] = display.new_display_var
            logger.info("虚拟显示已启动")
            return display
        except Exception as e:
            logger.error(f"虚拟显示启动失败: {e}")
            sys.exit(1)
    return None

def screenshot_path(name: str) -> str:
    return str(OUTPUT_DIR / f"{datetime.now().strftime('%H%M%S')}-{name}.png")

def safe_screenshot(sb, path: str):
    try:
        sb.save_screenshot(path)
        logger.info(f"📸 截图 → {Path(path).name}")
    except Exception as e:
        logger.warning(f"截图失败: {e}")

def notify_telegram(email: str, ok: bool, msg: str = "", screenshot_file: str = None):
    try:
        # 使用你原有的变量名
        token = os.environ.get("TG_BOT_TOKEN")
        chat_id = os.environ.get("TG_CHAT_ID")
        if not token or not chat_id:
            logger.info("未配置 TG 机器人变量，跳过推送。")
            return

        status = "✅ 保活成功" if ok else "❌ 保活失败"
        lines = [status, "", f"账号：{email}"]
        if msg:
            lines.append(f"信息：{msg}")
        lines.append(f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")
        lines.append("Lunes Host Auto Keep Alive")
        text = "\n".join(lines)

        if screenshot_file and Path(screenshot_file).exists():
            with open(screenshot_file, "rb") as f:
                requests.post(
                    f"https://api.telegram.org/bot{token}/sendPhoto",
                    data={"chat_id": chat_id, "caption": text},
                    files={"photo": f},
                    timeout=60
                )
        else:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
                timeout=30
            )
    except Exception as e:
        logger.warning(f"Telegram 通知失败: {e}")

def check_and_exit_on_rate_limit(sb, email: str) -> None:
    try:
        page_source = sb.get_page_source()
        if "Too Many Requests" in page_source or "20 per 1 hour" in page_source:
            logger.error("⛔ 检测到速率限制: Too Many Requests / 20 per 1 hour")
            sp = screenshot_path("rate-limit")
            safe_screenshot(sb, sp)
            notify_telegram(email=email, ok=False, msg="IP 已被限制，脚本已停止", screenshot_file=sp)
            sys.exit(1)
    except Exception:
        pass

def get_credentials() -> tuple[str, str]:
    # 完美继承你的原变量
    email = os.environ.get("LUNES_EMAIL", "").strip()
    password = os.environ.get("LUNES_PASSWORD", "").strip()
    if not email or not password:
        logger.error("未设置 LUNES_EMAIL 或 LUNES_PASSWORD 环境变量")
        sys.exit(1)
    logger.info(f"读取到账号: {mask_email(email)}")
    return email, password

# ================== Cloudflare 处理 ==================
def is_cloudflare_interstitial(sb) -> bool:
    try:
        has_login_form = sb.execute_script('''
            return !!(document.querySelector('input#email')
                   || document.querySelector('input[name="email"]')
                   || document.querySelector('form[action*="login"]'));
        ''')
        if has_login_form: return False

        has_dashboard = sb.execute_script('''
            return !!(document.querySelector('a.server-card')
                   || document.querySelector('.dashboard')
                   || document.querySelector('.sidebar'));
        ''')
        if has_dashboard: return False

        page_source = sb.get_page_source()
        title = sb.get_title().lower() if sb.get_title() else ""

        strong_indicators = ["Just a moment", "Verify you are human", "Checking your browser"]
        for indicator in strong_indicators:
            if indicator in page_source:
                return True

        if "just a moment" in title or "attention required" in title: return True
        return False
    except:
        return False

def bypass_cloudflare_interstitial(sb, email: str, max_attempts: int = 3) -> bool:
    logger.info("检测到 Cloudflare 整页挑战，尝试绕过...")
    for attempt in range(max_attempts):
        logger.info(f"CF 绕过尝试 {attempt + 1}/{max_attempts}")
        try:
            sb.uc_gui_click_captcha()
            time.sleep(6)
            check_and_exit_on_rate_limit(sb, email)
            if not is_cloudflare_interstitial(sb):
                logger.info("✅ Cloudflare 挑战已通过")
                return True
        except:
            pass
        time.sleep(3)
    return False

def wait_for_turnstile_success(sb, timeout: int = 30) -> bool:
    logger.info("等待 Turnstile 验证...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            success = sb.execute_script('''
                var resp = document.querySelector('input[name="cf-turnstile-response"]');
                if (resp && resp.value && resp.value.length > 20) return true;
                var grecap = document.querySelector('textarea[name="g-recaptcha-response"]');
                if (grecap && grecap.value && grecap.value.length > 20) return true;
                var iframe = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
                if (iframe && iframe.getAttribute("data-state") === "solved") return true;
                return false;
            ''')
            if success:
                logger.info("✅ Turnstile 验证成功")
                return True
        except:
            pass
        time.sleep(1)
    logger.warning("⏰ Turnstile 验证超时")
    return False

# ================== 登录流程 ==================
def clear_browser_state(sb):
    try:
        sb.execute_script('try { window.localStorage.clear(); window.sessionStorage.clear(); } catch(e) {}')
        sb.delete_all_cookies()
    except:
        pass
    logger.info("🧹 浏览器状态已清理")

def handle_initial_page(sb, email: str) -> Optional[str]:
    clear_browser_state(sb)
    logger.info("访问登录页...")
    sb.uc_open_with_reconnect(BETADASH_LOGIN_URL, reconnect_time=8)
    time.sleep(4)
    check_and_exit_on_rate_limit(sb, email)

    sp = screenshot_path("01-initial")
    safe_screenshot(sb, sp)
    current_url = sb.get_current_url()

    if is_cloudflare_interstitial(sb):
        logger.info("检测到 Cloudflare 整页挑战")
        if not bypass_cloudflare_interstitial(sb, email):
            sp = screenshot_path("02-cf-failed")
            safe_screenshot(sb, sp)
            return None
        time.sleep(3)
        if "/login" not in sb.get_current_url():
            return "already_logged"

    logger.info("等待登录表单...")
    for wait_round in range(3):
        try:
            sb.wait_for_element_visible('input#email', timeout=10)
            logger.info("✅ 找到登录表单")
            return "need_login"
        except TimeoutException:
            check_and_exit_on_rate_limit(sb, email)
            if is_cloudflare_interstitial(sb):
                bypass_cloudflare_interstitial(sb, email, max_attempts=2)
            time.sleep(3)
    
    safe_screenshot(sb, screenshot_path("02-no-form"))
    return None

def fill_and_submit(sb, email: str, password: str) -> bool:
    logger.info("填写登录信息...")
    try:
        sb.clear('input#email')
    except: pass
    sb.type('input#email', email)
    time.sleep(0.5)

    try:
        sb.clear('input#password')
    except: pass
    sb.type('input#password', password)
    time.sleep(0.5)

    safe_screenshot(sb, screenshot_path("03-form-filled"))

    logger.info("处理 Turnstile 验证码...")
    if wait_for_turnstile_success(sb, timeout=5):
        logger.info("Turnstile 已自动完成")
    else:
        logger.info("尝试点击 Turnstile...")
        for _ in range(3):
            try: sb.uc_gui_click_captcha()
            except: pass
            time.sleep(2)
            if wait_for_turnstile_success(sb, timeout=10): break

    safe_screenshot(sb, screenshot_path("04-before-submit"))

    logger.info("提交登录...")
    submitted = False
    for selector in ['button.submit-btn', 'button[type="submit"]']:
        try:
            sb.click(selector)
            submitted = True
            break
        except: continue

    if not submitted:
        try:
            sb.execute_script('document.querySelector("form").submit()')
            submitted = True
        except: return False

    time.sleep(6)
    check_and_exit_on_rate_limit(sb, email)

    if "/login" in sb.get_current_url():
        safe_screenshot(sb, screenshot_path("05-login-failed"))
        logger.error("登录失败 - 仍在登录页")
        return False

    logger.info("✅ 登录成功！")
    return True

def navigate_to_server(sb, email: str) -> tuple[bool, str, Optional[str]]:
    time.sleep(3)
    sp_dash = screenshot_path("06-dashboard")
    safe_screenshot(sb, sp_dash)
    check_and_exit_on_rate_limit(sb, email)

    current_url = sb.get_current_url()
    if "/servers/" in current_url:
        return True, "Auto", sp_dash

    found_selector = None
    alt_selectors = ['a.server-card', 'a[href*="/servers/"]', '.server-card']

    for sel in alt_selectors:
        try:
            sb.wait_for_element_visible(sel, timeout=5)
            found_selector = sel
            break
        except: continue

    if not found_selector:
        # 如果自动找卡片失败，回退尝试直接访问用户配置的 LUNES_SERVER_URL
        fallback_url = os.environ.get("LUNES_SERVER_URL")
        if fallback_url:
            logger.info(f"未找到服务器卡片，尝试直达 SERVER_URL: {fallback_url}")
            sb.uc_open_with_reconnect(fallback_url, reconnect_time=5)
            time.sleep(5)
            sp = screenshot_path("07-fallback-nav")
            safe_screenshot(sb, sp)
            if "/servers/" in sb.get_current_url() or sb.is_element_visible("input[name='user']"):
                return True, "Fallback", sp
            
        sp = screenshot_path("07-no-server")
        safe_screenshot(sb, sp)
        return False, "NO_SERVER", sp

    logger.info("进入服务器详情页...")
    try:
        sb.click(found_selector)
    except:
        try:
            sb.execute_script(f"document.querySelector('{found_selector}').click();")
        except:
            return False, "CLICK_ERROR", sp_dash

    time.sleep(5)
    sp_detail = screenshot_path("08-server-detail")
    safe_screenshot(sb, sp_detail)

    if "/servers/" in sb.get_current_url():
        logger.info("✅ 成功进入服务器详情页")
        return True, "OK", sp_detail
    return False, "NAV_ERROR", sp_detail

# ================== 主登录函数 ==================
def main():
    email, password = get_credentials()
    display = setup_display()

    try:
        sb_kwargs = dict(
            uc=True,
            test=True,
            locale="en",
            headed=not is_linux(),
            user_data_dir=None,
            chromium_arg="--disable-blink-features=AutomationControlled",
            proxy=PROXY  # 锁定使用你的 40000 WARP 代理
        )

        result = {"success": False, "message": "", "screenshot": None}
        
        with SB(**sb_kwargs) as sb:
            init_status = handle_initial_page(sb, email)
            if init_status is None:
                result["message"] = "Cloudflare 绕过失败或未找到登录表单"
                result["screenshot"] = screenshot_path("02-cf-failed")
            else:
                if init_status == "need_login":
                    login_ok = fill_and_submit(sb, email, password)
                    if not login_ok:
                        result["message"] = "登录失败"
                        result["screenshot"] = screenshot_path("05-login-failed")
                        
                if init_status == "already_logged" or login_ok:
                    nav_ok, code, screenshot = navigate_to_server(sb, email)
                    if nav_ok:
                        result.update(success=True, message="保活成功！成功进入面板。", screenshot=screenshot)
                    else:
                        result.update(success=False, message=f"未能进入节点面板: {code}", screenshot=screenshot)

        notify_telegram(
            email=email,
            ok=result["success"],
            msg=result["message"],
            screenshot_file=result["screenshot"],
        )

        if result["success"]:
            sys.exit(0)
        else:
            sys.exit(1)

    finally:
        if display:
            display.stop()

if __name__ == "__main__":
    main()
