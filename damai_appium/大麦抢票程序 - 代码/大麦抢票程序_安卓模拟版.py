import os
import time
import pickle
import json
import re
import shutil
from pathlib import Path
from typing import Optional

from selenium import webdriver as selenium_webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import WebDriverException

try:
    from appium import webdriver as appium_webdriver
    from appium.options.common.base import AppiumOptions
except Exception:
    appium_webdriver = None
    AppiumOptions = None

# 根据 damai_session_price_watcher.py 中的分析，修正为当前正确的登录与抢票移动端链接
login_url = 'https://passport.damai.cn/login?ru=https%3A%2F%2Fm.damai.cn%2F'
target_url = "https://m.damai.cn/shows/item.html?from=def&itemId=1035663663342&sqm=dianying.h5.unknown.value&spm=a2o71.search.list.ditem_0"

class Damai:
    def __init__(self):
        self.status = 0
        self.options: Optional[Options] = None
        self.driver_mode = os.getenv('DAMAI_ANDROID_MODE', 'appium_emulator').strip().lower()
        self.appium_server_url = os.getenv('DAMAI_APPIUM_SERVER', 'http://127.0.0.1:4723')
        self.active_driver_backend = 'unknown'
        self.android_user_agent = (
            "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.6367.179 Mobile Safari/537.36"
        )

        # =============== 【核心优化】基于日志数据的自动化点击目标 ===============
        # 已根据您的快照（修改时间3:15以前）提取了真实想看演唱会的准确文本！
        # 修改为真实演唱会的 ID: 1035663663342

        # 目标场次特征词，快照中存在的有效文本例如: "2026-05-01", "周五 19:30", "05.01"
        self.target_session = "2026-05-02 周六 19:30"

        # 目标票价特征词，快照中存在的有效文本例如: "1480元（内场）", "1280元（看台）", "480元（看台）"
        self.target_price = "680"
        self.target_ticket_count = 3
        self.target_attendee_count = 3
        # =======================================================================

        self.driver = self._create_driver()
        self.enhance_android_emulation()

    @staticmethod
    def _resolve_android_sdk_path() -> Optional[str]:
        """解析本机 Android SDK 路径。"""
        env_candidates = [os.getenv('ANDROID_SDK_ROOT'), os.getenv('ANDROID_HOME')]
        for candidate in env_candidates:
            if not candidate:
                continue
            candidate_path = Path(candidate)
            if not candidate_path.exists():
                continue

            # 兼容将 ANDROID_HOME / ANDROID_SDK_ROOT 设为 .../platform-tools 的情况。
            if candidate_path.name.lower() == 'platform-tools':
                adb_file = candidate_path / 'adb.exe'
                if adb_file.exists():
                    return str(candidate_path.parent)

            adb_path = candidate_path / 'platform-tools' / 'adb.exe'
            if adb_path.exists():
                return str(candidate_path)

        adb_path = shutil.which('adb')
        if adb_path:
            adb_file = Path(adb_path)
            if adb_file.exists():
                if adb_file.parent.name.lower() == 'platform-tools':
                    return str(adb_file.parent.parent)
                return str(adb_file.parent)

        local_app_data = os.getenv('LOCALAPPDATA')
        user_profile = os.getenv('USERPROFILE')
        path_candidates = [
            Path(local_app_data) / 'Android' / 'Sdk' if local_app_data else None,
            Path(user_profile) / 'AppData' / 'Local' / 'Android' / 'Sdk' if user_profile else None,
            Path('C:/Android/Sdk'),
            Path('D:/Android/Sdk'),
        ]

        for candidate in path_candidates:
            if candidate is None:
                continue
            adb_exe = candidate / 'platform-tools' / 'adb.exe'
            if candidate.exists() and adb_exe.exists():
                return str(candidate)
        return None

    def _ensure_android_sdk_env(self):
        """确保 Appium 所需的 ANDROID_HOME / ANDROID_SDK_ROOT 可用。"""
        sdk_path = self._resolve_android_sdk_path()
        if not sdk_path:
            raise RuntimeError('未找到 Android SDK，请安装 Android SDK，或设置 ANDROID_HOME / ANDROID_SDK_ROOT。')

        os.environ['ANDROID_SDK_ROOT'] = sdk_path
        os.environ['ANDROID_HOME'] = sdk_path

    def _build_chrome_android_driver(self):
        """保留原 Selenium 安卓模拟作为兼容回退。"""
        self.options = Options()
        self.options.add_experimental_option('excludeSwitches', ['enable-automation'])
        self.options.add_argument('--disable-blink-features=AutomationControlled')

        # 同步 watcher 中精准的安卓模拟参数，防止被盾
        mobile_emulation = {
            'deviceMetrics': {
                'width': 412,
                'height': 915,
                'pixelRatio': 2.625,
                'mobile': True,
                'touch': True
            },
            'clientHints': {
                'platform': 'Android',
                'mobile': True,
                'platformVersion': '13',
                'model': 'Pixel 7'
            },
            'userAgent': self.android_user_agent
        }
        self.options.add_experimental_option('mobileEmulation', mobile_emulation)
        self.options.add_argument("--lang=zh-CN")
        self.options.add_argument("--window-size=412,915")
        self.options.add_argument(f"--user-agent={self.android_user_agent}")
        self.options.add_argument("--touch-events=enabled")
        self.options.add_argument("--force-device-scale-factor=2.625")
        self.options.add_argument("--high-dpi-support=1")
        return selenium_webdriver.Chrome(options=self.options)

    def _build_appium_android_driver(self):
        """使用 v2 的 Appium 安卓模拟逻辑启动真安卓模拟器中的 Chrome。"""
        if appium_webdriver is None or AppiumOptions is None:
            print('当前环境未安装 appium-python-client，无法启用 Appium 安卓模拟。')
            return None

        self._ensure_android_sdk_env()

        capabilities = {
            'platformName': 'Android',
            'platformVersion': os.getenv('DAMAI_ANDROID_VERSION', '16'),
            'deviceName': os.getenv('DAMAI_DEVICE_NAME', 'emulator-5554'),
            'browserName': 'Chrome',
            'automationName': 'UiAutomator2',
            'noReset': True,
            'newCommandTimeout': 6000,
            'ignoreHiddenApiPolicyError': True,
            'disableWindowAnimation': True,
            'adbExecTimeout': 20000,
        }

        device_info = AppiumOptions()
        device_info.load_capabilities(capabilities)
        try:
            driver = appium_webdriver.Remote(self.appium_server_url, options=device_info)
        except WebDriverException as e:
            message = str(e)
            if 'ANDROID_HOME' in message or 'ANDROID_SDK_ROOT' in message:
                raise RuntimeError('Appium 无法读取 Android SDK 环境变量，请确认 SDK 已安装且路径有效。') from e
            raise

        try:
            driver.update_settings({
                'waitForIdleTimeout': 0,
                'actionAcknowledgmentTimeout': 0,
                'keyInjectionDelay': 0,
                'waitForSelectorTimeout': 300,
                'ignoreUnimportantViews': False,
                'allowInvisibleElements': True,
                'enableNotificationListener': False,
            })
        except Exception:
            pass

        return driver

    def _create_driver(self):
        """优先使用 Appium 安卓模拟，失败时自动回退至 Selenium 模拟。"""
        prefer_appium = self.driver_mode in {'appium', 'appium_emulator', 'android_appium'}

        if prefer_appium:
            try:
                print(f'优先使用 Appium 安卓模拟: {self.appium_server_url}')
                driver = self._build_appium_android_driver()
                if driver is not None:
                    self.active_driver_backend = 'appium_emulator'
                    return driver
            except Exception as e:
                print(f'Appium 初始化失败，回退到 Chrome 安卓模拟: {e}')

        self.active_driver_backend = 'chrome_emulation'
        print('使用 Selenium Chrome 安卓模拟继续执行。')
        return self._build_chrome_android_driver()

    def enhance_android_emulation(self):
        """
        通过 CDP 补齐部分移动端指纹，让安卓模拟更接近真机环境。
        """
        if not hasattr(self.driver, 'execute_cdp_cmd'):
            return

        try:
            self.driver.execute_cdp_cmd("Network.enable", {})
            self.driver.execute_cdp_cmd(
                "Network.setUserAgentOverride",
                {
                    "userAgent": self.android_user_agent,
                    "platform": "Android",
                    "acceptLanguage": "zh-CN,zh;q=0.9,en;q=0.8"
                }
            )
        except Exception:
            pass

        try:
            self.driver.execute_cdp_cmd("Emulation.setLocaleOverride", {"locale": "zh-CN"})
            self.driver.execute_cdp_cmd("Emulation.setTimezoneOverride", {"timezoneId": "Asia/Shanghai"})
            self.driver.execute_cdp_cmd("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 5})
        except Exception:
            pass

        try:
            self.driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {
                    "source": """
Object.defineProperty(navigator, 'platform', {get: () => 'Linux armv8l'});
Object.defineProperty(navigator, 'maxTouchPoints', {get: () => 5});
Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
"""
                }
            )
        except Exception:
            pass

    def set_cookies(self):
        self.driver.get(login_url)
        print('登录账号（请在弹出的浏览器中手动进行手机验证码或账号登录）')
        
        # 监听是否成功跳转回主界面或退出登录界面
        while self.driver.title.find('大麦登录') != -1 or self.driver.title.find('登录') != -1:
            time.sleep(1)
            
        pickle.dump(self.driver.get_cookies(), open('cookies.pkl', 'wb'))
        print('cookie保存成功')
        self.driver.get(target_url)

    def get_cookie(self):
        try:
            cookies = pickle.load(open('cookies.pkl', 'rb'))
            for cookie in cookies:
                cookie_dict = {
                    'domain': '.damai.cn',
                    'name': cookie.get('name'),
                    'value': cookie.get('value')
                }
                try:
                    self.driver.add_cookie(cookie_dict)
                except:
                    continue
            print('加载cookie信息成功')
        except Exception as e:
            print('获取或加载cookie失败:', e)

    def login(self):
        # 检查有没有缓存的文件
        if not os.path.exists('cookies.pkl'):
            self.set_cookies()
        else:
            self.driver.get(target_url)  # 先访问同域网页再注入cookie
            self.get_cookie()

    def confirm_notice(self):
        # 结合 watcher 中的精准 XPATH，快速找到由手机端弹出的“确认并知悉”窗口
        try:
            WebDriverWait(self.driver, 4).until(
                EC.presence_of_all_elements_located((By.XPATH, "//*[contains(@class,'health') or contains(text(), '知悉') or contains(text(), '我知道') or contains(text(), '同意')]"))
            )
            NOTICE_XPATH = "//*[contains(text(), '确认并知悉') or contains(text(), '我知道') or contains(text(), '同意') or contains(text(), '确定')]"
            btn = self.driver.find_element(By.XPATH, NOTICE_XPATH)
            if btn and btn.is_displayed():
                try:
                    btn.click()
                except:
                    self.driver.execute_script('arguments[0].click();', btn)
                print('已自动确认购票须知弹窗')
                return True
        except Exception:
             pass
        return False

    def enter_concert(self):
        print(f'正在启动抢票环境（{self.active_driver_backend}）, 进入大麦网...')
        self.login()
        self.driver.refresh()
        time.sleep(1)
        self.confirm_notice()
        self.status = 2
        print('环境初始化与登录成功，开始监听抢票链路!')

    def choose_ticket(self):
        if self.status == 2:
            print('=' * 30)
            print('进入详情页，尝试拉起选座/票价滑框...')
            
            while self.driver.title.find('确认订单') == -1 and self.driver.title.find('订单') == -1:
                # 尝试点击商品详情页的购买触发按钮，它通常是一个固定在底部的栏
                try:
                    # 将模拟购票和到点真实购票时可能出现的文本都囊括，以确保第一时间拉起弹窗，增加模糊匹配词避免版本更新侦测不到
                    buy_xpath = "//*[contains(@class, 'buy-btn') or contains(@class, 'buy-link') or contains(text(), '立即购买') or contains(text(), '特惠购票') or contains(text(), '立即预订') or contains(text(), '选座购买') or contains(text(), '购票') or contains(text(), '购买') or contains(text(), '预订') or contains(text(), '选座')]"
                    buy_btns = self.driver.find_elements(By.XPATH, buy_xpath)
                    for btn in buy_btns:
                        if btn.is_displayed():
                            try:
                                self.driver.execute_script('arguments[0].click();', btn)
                                time.sleep(0.1)  # 提速，从0.5改短更快响应
                            except:
                                pass
                except Exception:
                    pass

                # 进行智能自动化场次与票价点击
                self.smart_choice_seats()

                # 如果此时跳转到了订单页或已经出现“提交”，增加模糊判断词语
                if self.isElementExist('//*[text()="立即提交" or contains(text(), "去付款") or contains(text(), "去支付")]'):
                    print('已进入订单结算页面，开始处理下单...')
                    self.check_order()
                    break

    def smart_choice_seats(self):
        """
        核心智能点击逻辑。
        结合 damai_session_price_logs 抽取的界面文本规律工作。
        """
        try:
            # 获取选票浮层内可能含有的所有带有文字的区块元素
            elements = self.driver.find_elements(By.XPATH, "//*[self::div or self::span or self::li]")
            clicked_session = False
            clicked_price = False
            
            for el in elements:
                if not el.is_displayed():
                    continue
                    
                text = el.text.strip() if el.text else ""
                class_attr = el.get_attribute("class") or ""
                if not text or len(text) < 2:
                    continue
                    
                # 屏蔽无法点击的状态: 通常带有缺货、无票或者 class 带 disabled (如 sku-item-disabled) 
                if '缺货' in text or '无票' in text or 'disabled' in class_attr.lower():
                    continue
                    
                # 【匹配并点击目标场次】
                if not clicked_session and self.target_session and self.target_session in text:
                    try:
                        self.driver.execute_script('arguments[0].click();', el)
                        print(f"[*] 智能匹配: 自动点击场次 -> {text}")
                        clicked_session = True
                        time.sleep(0.2)
                    except:
                        pass
                        
                # 【匹配并点击目标票价】
                if not clicked_price and self.target_price and self.target_price in text:
                    try:
                        self.driver.execute_script('arguments[0].click();', el)
                        print(f"[*] 智能匹配: 自动点击票价 -> {text}")
                        clicked_price = True
                        time.sleep(0.2)
                    except:
                        pass

            # 按当前需求将数量固定为 3 张
            self.ensure_ticket_quantity(self.target_ticket_count)

            # 场次和票价都点击(或跳过)后尝试寻找并点击确认按钮
            # 弹窗底部普遍用 button 或是含特定控制类名称的 div
            confirm_btns = self.driver.find_elements(By.XPATH, '//div[contains(@class,"bui-btn") or contains(@class,"button")] | //button | //*[contains(text(),"确定") or contains(text(),"购买")]')
            for btn in confirm_btns:
                btn_text = btn.text.strip() if btn.text else ""
                if "确定" in btn_text or "购买" in btn_text or "下一步" in btn_text or "确认" in btn_text or "提交" in btn_text or "选座" in btn_text:
                    if btn.is_displayed():
                        try:
                            self.driver.execute_script('arguments[0].click();', btn)
                            break
                        except:
                            pass
        except Exception:
             # 回退为原本的疯狂点击逻辑（兜底方案）
             try:
                if self.isElementExist('//button[@type="button"]'):
                    btns = self.driver.find_elements(By.XPATH, '//button[@type="button"]')
                    for b in btns:
                        if b.is_displayed():
                            b.click()
             except:
                pass

    def ensure_ticket_quantity(self, target_count):
        """
        在选票弹窗中将数量调整到目标值。
        """
        try:
            target_count = int(target_count)
        except Exception:
            return

        def _read_current_count():
            try:
                qty_elements = self.driver.find_elements(
                    By.XPATH,
                    "//*[contains(text(), '张') and not(.//*[contains(text(), '张')])]"
                )
                for el in qty_elements:
                    if not el.is_displayed():
                        continue
                    text = (el.text or "").strip().replace(" ", "")
                    m = re.match(r'^([0-9]+)张$', text)
                    if m:
                        return int(m.group(1))
            except Exception:
                pass
            return None

        plus_xpath = (
            "//*[text()='+' or text()='＋' "
            "or contains(@class, 'plus') or contains(@class, 'add')]"
        )
        minus_xpath = (
            "//*[text()='-' or text()='－' "
            "or contains(@class, 'minus') or contains(@class, 'sub')]"
        )

        for _ in range(8):
            current_count = _read_current_count()
            if current_count is None:
                return

            if current_count == target_count:
                print(f'票数已调整为 {target_count} 张')
                return

            click_xpath = plus_xpath if current_count < target_count else minus_xpath
            controls = self.driver.find_elements(By.XPATH, click_xpath)

            clicked = False
            for control in controls:
                if not control.is_displayed():
                    continue
                try:
                    self.driver.execute_script('arguments[0].click();', control)
                    clicked = True
                    break
                except Exception:
                    continue

            if not clicked:
                return
            time.sleep(0.12)

    def ensure_attendee_selection(self, target_count):
        """
        在付款页将实名观演人勾选到目标人数。
        """
        try:
            target_count = int(target_count)
        except Exception:
            return 0

        row_base_xpath = (
            "//*[contains(@class, 'bui-ultron-viewer-list')]"
            "//*[contains(@class, 'bui-ultron-viewer-item')]"
        )
        unselected_row_xpath = (
            row_base_xpath
            + "[.//*[name()='svg' and contains(@class, 'bui-svg-icon') "
            + "and contains(translate(@color, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '#ddd')]]"
        )
        selected_row_xpath = (
            row_base_xpath
            + "[.//*[name()='svg' and contains(@class, 'bui-svg-icon') "
            + "and not(contains(translate(@color, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '#ddd'))]]"
        )

        try:
            WebDriverWait(self.driver, 5).until(
                EC.presence_of_all_elements_located((By.XPATH, row_base_xpath))
            )
        except Exception:
            pass

        def _visible_rows(xpath):
            return [row for row in self.driver.find_elements(By.XPATH, xpath) if row.is_displayed()]

        def _click_row(row):
            try:
                self.driver.execute_script('arguments[0].click();', row)
                return True
            except Exception:
                try:
                    row.click()
                    return True
                except Exception:
                    try:
                        icon = row.find_element(By.XPATH, ".//*[name()='svg' and contains(@class, 'bui-svg-icon')]")
                        self.driver.execute_script('arguments[0].click();', icon)
                        return True
                    except Exception:
                        return False

        for _ in range(10):
            selected_rows = _visible_rows(selected_row_xpath)
            unselected_rows = _visible_rows(unselected_row_xpath)

            selected_count = len(selected_rows)
            if selected_count == target_count:
                print(f'已勾选 {selected_count} 位观演人')
                return selected_count

            if selected_count < target_count and unselected_rows:
                need = min(target_count - selected_count, len(unselected_rows))
                for row in unselected_rows[:need]:
                    _click_row(row)
                    time.sleep(0.1)
            elif selected_count > target_count and selected_rows:
                need = selected_count - target_count
                for row in selected_rows[-need:]:
                    _click_row(row)
                    time.sleep(0.1)
            else:
                break

            time.sleep(0.15)

        final_selected = len(_visible_rows(selected_row_xpath))
        print(f'观演人勾选数: {final_selected}/{target_count}')
        return final_selected

    def get_required_attendee_count(self, default_count):
        """
        从“仅需选择X位”文案中提取当前订单要求的观演人数。
        """
        try:
            tip_xpath = (
                "//*[contains(@class, 'bui-ultron-viewer-header_title-tip') "
                "or contains(text(), '仅需选择') or contains(text(), '僅需選擇')]"
            )
            tips = self.driver.find_elements(By.XPATH, tip_xpath)
            for tip in tips:
                if not tip.is_displayed():
                    continue
                text = (tip.text or "").strip().replace(" ", "")
                match = re.search(r'([0-9]+)位', text)
                if match:
                    return int(match.group(1))
        except Exception:
            pass
        return int(default_count)

    def click_order_submit_once(self):
        """
        仅点击订单页提交按钮，不触发支付页的“立即支付”。
        """
        submit_xpath = (
            "//*[contains(@class, 'pay-submit-btn') "
            "or text()='立即提交' "
            "or contains(text(), '去付款') "
            "or contains(text(), '去支付')]"
        )

        buttons = self.driver.find_elements(By.XPATH, submit_xpath)
        for btn in buttons:
            if not btn.is_displayed():
                continue

            txt = (btn.text or "").strip()
            # 进入支付收银台后，不自动点击任何支付按钮
            if '立即支付' in txt or '确认支付' in txt:
                continue

            try:
                self.driver.execute_script('arguments[0].click();', btn)
                print(f'已点击订单按钮: {txt or "(无文本按钮)"}')
                return True
            except Exception:
                continue
        return False

    def check_order(self):
        try:
            required_count = self.get_required_attendee_count(self.target_attendee_count)
            selected_count = self.ensure_attendee_selection(required_count)
            if selected_count < required_count:
                print(f'观演人未选满：{selected_count}/{required_count}，暂不提交订单。')
                return

            # 只负责点击订单提交，不会点击支付收银台按钮
            if self.click_order_submit_once():
                time.sleep(0.8)
                print('若已进入支付界面，脚本将停止自动点击，请手动完成支付。')
            else:
                print('未找到可点击的订单提交按钮。')

            self.status = 3
            print('订单页处理完成。')
        except Exception as e:
            print('提交订单失败或遭遇异常:', e)

    def isElementExist(self, element_xpath):
        try:
            self.driver.find_element(By.XPATH, element_xpath)
            return True
        except:
            return False

    def finish(self):
        print("任务结束，清理并释放资源。")
        self.driver.quit()

if __name__ == '__main__':
    try:
        damai = Damai()
        damai.enter_concert()  
        damai.choose_ticket()  
        
        # 挂起程序方便用户观察结果，而不是立刻关闭窗口
        print("\n进入观望模式。如果提交成功，您可以在页面上手动完成支付操作。")
        time.sleep(300) 
        
    except Exception as e:
        print('程序因异常中断:', e)
    finally:
        # 注释掉 finish 可以保留浏览器给用户手动后续机会
        # damai.finish()
        pass
