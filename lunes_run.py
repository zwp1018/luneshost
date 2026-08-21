import os
import time
import requests
from seleniumbase import SB

SERVER_URL = os.getenv("LUNES_SERVER_URL")
LUNES_EMAIL = os.getenv("LUNES_EMAIL")
LUNES_PASSWORD = os.getenv("LUNES_PASSWORD")

def send_tg_notification(message, photo_path=None):
    """发送结果和截图至 Telegram"""
    token = os.getenv("TG_BOT_TOKEN")
    chat_id = os.getenv("TG_CHAT_ID")
    if not token or not chat_id:
        print("未配置 TG 机器人变量，跳过发送 TG 推送。")
        return

    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
        requests.post(url, json=payload)
    except Exception as e:
        print(f"发送 TG 消息异常: {e}")

    if photo_path and os.path.exists(photo_path):
        try:
            url = f"https://api.telegram.org/bot{token}/sendPhoto"
            with open(photo_path, "rb") as f:
                requests.post(url, data={"chat_id": chat_id, "caption": "Lunes Host 实时画面"}, files={"photo": f})
        except Exception as e:
            print(f"发送 TG 截图异常: {e}")

def run():
    if not SERVER_URL or not LUNES_EMAIL or not LUNES_PASSWORD:
        print("错误: 缺少环境变量配置")
        return

    # ⚠️ 注意：如果你使用的是带 WARP 的 actions 配置，请把下面这行改为：
    # with SB(uc=True, xvfb=True) as sb:
    with SB(uc=True, xvfb=True, proxy="socks5://127.0.0.1:40000") as sb:
        
        success = False
        for i in range(3):
            try:
                # 💡 优化 1：直接访问你的目标控制台地址，而不是写死的外层系统
                print(f"正在访问目标面板地址 (第 {i+1}/3 次尝试)...")
                sb.uc_open_with_reconnect(SERVER_URL, reconnect_time=8)
                sb.sleep(8)
                
                page_source_lower = sb.get_page_source().lower()
                if "nxdomain" in page_source_lower or "can’t be reached" in page_source_lower:
                    print("⚠️ DNS 阻断，等待重试...")
                    sb.sleep(5)
                    continue
                    
                success = True
                break
            except Exception as e:
                print(f"访问页面发生异常: {e}")
                sb.sleep(5)

        if not success:
            sb.save_screenshot("lunes_debug_screenshot.png")
            send_tg_notification("❌ <b>Lunes 访问失败</b>\n网络阻断或节点离线。", "lunes_debug_screenshot.png")
            return

        # 尝试物理点击过盾
        try:
            sb.uc_gui_click_captcha()
            sb.sleep(8)
        except:
            pass

        # 💡 优化 2：精准识别翼龙面板 (Pterodactyl) 的未登录状态
        current_url = sb.get_current_url()
        # 翼龙的账号输入框通常是 name="user" 或者 type="text"
        user_selector = "input[name='user'], input[name='username'], input[type='email'], input[type='text']"
        
        if "login" in current_url or sb.is_element_visible(user_selector):
            print("检测到处于未登录状态，开始填充翼龙面板表单...")
            try:
                sb.wait_for_element_visible(user_selector, timeout=15)
                sb.update_text(user_selector, LUNES_EMAIL)
                
                sb.update_text("input[type='password']", LUNES_PASSWORD)
                sb.sleep(1)

                # 💡 优化 3：模拟按下回车键提交表单，规避按钮点击不到的问题
                print("正在敲击回车键提交登录...")
                sb.type("input[type='password']", "\n")
                
                # 给服务器充足的跳转时间
                sb.sleep(20) 
            except Exception as e:
                print(f"❌ 填表异常: {e}")
                sb.save_screenshot("lunes_debug_screenshot.png")
                send_tg_notification(f"❌ <b>Lunes 填表异常</b>\n{e}", "lunes_debug_screenshot.png")
                return

        # 最终验证并保活打卡
        print("正在进行最终状态验证...")
        sb.uc_open_with_reconnect(SERVER_URL, reconnect_time=5)
        sb.sleep(15)
        sb.save_screenshot("lunes_debug_screenshot.png")

        if "login" in sb.get_current_url() or sb.is_element_visible(user_selector):
            msg = "❌ <b>Lunes 登录失效！</b>\n仍然退回到了未登录状态，请检查账号密码是否正确。"
        else:
            msg = "✅ <b>Lunes 每日保活打卡成功！</b>\n成功进入控制面板内部。"
            
        print(msg)
        send_tg_notification(msg, "lunes_debug_screenshot.png")

if __name__ == "__main__":
    run()
