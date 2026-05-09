# -*- coding: UTF-8 -*-
"""
__Author__ = "BlueCestbon"
__Version__ = "2.0.0"
__Description__ = "大麦app抢票自动化 - 优化版"
__Created__ = 2025/09/13 19:27
"""

import time
import os
import re
import shutil
from pathlib import Path
from typing import Optional

from appium import webdriver
from appium.webdriver.webdriver import WebDriver
from appium.options.common.base import AppiumOptions
from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

from config import Config


class DamaiBot:
    def __init__(self):
        self.config = Config.load_config()
        self.driver: Optional[WebDriver] = None
        self.wait: Optional[WebDriverWait] = None
        try:
            self._setup_driver()
        except Exception as e:
            # 初始化失败时保留对象，交给 run_with_retry 继续重试
            print(f"初始化驱动失败: {e}")

    @staticmethod
    def _resolve_android_sdk_path() -> Optional[str]:
        """解析本机 Android SDK 路径并返回字符串路径。"""
        env_candidates = [os.getenv("ANDROID_SDK_ROOT"), os.getenv("ANDROID_HOME")]
        for candidate in env_candidates:
            if not candidate:
                continue
            candidate_path = Path(candidate)
            if not candidate_path.exists():
                continue

            # 允许将 ANDROID_HOME / ANDROID_SDK_ROOT 设置为 .../platform-tools。
            if candidate_path.name.lower() == "platform-tools":
                adb_file = candidate_path / "adb.exe"
                if adb_file.exists():
                    return str(candidate_path.parent)

            adb_path = candidate_path / "platform-tools" / "adb.exe"
            if adb_path.exists():
                return str(candidate_path)

        adb_path = shutil.which("adb")
        if adb_path:
            adb_file = Path(adb_path)
            if adb_file.exists():
                # 常见结构: <sdk_root>/platform-tools/adb.exe
                if adb_file.parent.name.lower() == "platform-tools":
                    return str(adb_file.parent.parent)
                return str(adb_file.parent)

        local_app_data = os.getenv("LOCALAPPDATA")
        user_profile = os.getenv("USERPROFILE")
        path_candidates = [
            Path(local_app_data) / "Android" / "Sdk" if local_app_data else None,
            Path(user_profile) / "AppData" / "Local" / "Android" / "Sdk" if user_profile else None,
            Path("C:/Android/Sdk"),
            Path("D:/Android/Sdk"),
        ]

        for candidate in path_candidates:
            if candidate is None:
                continue
            adb_path = candidate / "platform-tools" / "adb.exe"
            if candidate.exists() and adb_path.exists():
                return str(candidate)
        return None

    def _ensure_android_sdk_env(self):
        """确保 Appium 所需的 ANDROID_HOME / ANDROID_SDK_ROOT 可用。"""
        sdk_path = self._resolve_android_sdk_path()
        if not sdk_path:
            raise RuntimeError(
                "未找到 Android SDK，请安装 Android SDK，或设置 ANDROID_HOME / ANDROID_SDK_ROOT。"
            )

        os.environ["ANDROID_SDK_ROOT"] = sdk_path
        os.environ["ANDROID_HOME"] = sdk_path

    def _get_driver(self) -> WebDriver:
        if self.driver is None:
            raise RuntimeError("驱动尚未初始化")
        return self.driver

    def _get_wait(self) -> WebDriverWait:
        if self.wait is None:
            raise RuntimeError("显式等待器尚未初始化")
        return self.wait

    def _setup_driver(self):
        """初始化驱动配置"""
        self._ensure_android_sdk_env()

        base_capabilities = {
            "platformName": "Android",  # 操作系统
            "platformVersion": "16",  # 系统版本
            "deviceName": "emulator-5554",  # 设备名称
            "appPackage": "cn.damai",  # app 包名
            "appActivity": ".launcher.splash.SplashMainActivity",  # app 启动 Activity
            "unicodeKeyboard": True,  # 支持 Unicode 输入
            "resetKeyboard": True,  # 隐藏键盘
            "noReset": True,  # 不重置 app
            "newCommandTimeout": 6000,  # 超时时间
            "automationName": "UiAutomator2",  # 使用 uiautomator2
            "skipServerInstallation": False,  # 跳过服务器安装
            "skipDeviceInitialization": True,  # Android 14+ 上避免 io.appium.settings FGS(location) 启动限制
            "ignoreHiddenApiPolicyError": True,  # 忽略隐藏 API 策略错误
            "disableWindowAnimation": True,  # 禁用窗口动画
            # 优化性能配置
            "mjpegServerFramerate": 1,  # 降低截图帧率
            "shouldTerminateApp": False,
            "adbExecTimeout": 45000,
            "androidInstallTimeout": 120000,
            "uiautomator2ServerInstallTimeout": 120000,
            "uiautomator2ServerLaunchTimeout": 120000,
        }

        setup_delays = [1.0, 2.0, 3.0]
        max_setup_attempts = 3
        for setup_attempt in range(max_setup_attempts):
            capabilities = dict(base_capabilities)
            # Settings app 启动失败时，下一轮走更保守的初始化策略
            if setup_attempt > 0:
                capabilities["skipServerInstallation"] = True
                capabilities["skipDeviceInitialization"] = True
                capabilities["adbExecTimeout"] = 60000

            device_app_info = AppiumOptions()
            device_app_info.load_capabilities(capabilities)
            try:
                self.driver = webdriver.Remote(self.config.server_url, options=device_app_info)
                break
            except WebDriverException as e:
                self.driver = None
                message = str(e)
                is_last = setup_attempt == max_setup_attempts - 1

                if "ANDROID_HOME" in message or "ANDROID_SDK_ROOT" in message:
                    raise RuntimeError(
                        "Appium Server 未读取到 Android SDK 环境变量。请先设置 ANDROID_HOME / ANDROID_SDK_ROOT 后重新启动 Appium。"
                    ) from e

                if "FOREGROUND_SERVICE_LOCATION" in message or "Starting FGS with type location" in message:
                    if is_last:
                        raise RuntimeError(
                            "Appium Settings 因 Android 前台定位服务限制启动失败。已建议默认跳过设备初始化；请确认重启 Appium 后重试。"
                        ) from e
                    wait_seconds = setup_delays[setup_attempt]
                    print(
                        f"检测到 Android 前台定位服务限制，第 {setup_attempt + 2} 次重试前等待 {wait_seconds:.1f} 秒..."
                    )
                    time.sleep(wait_seconds)
                    continue

                if "Appium Settings app is not running" in message:
                    if is_last:
                        raise RuntimeError(
                            "Appium Settings app 启动超时。请确认设备已解锁、adb 可用，并重启 Appium 后重试。"
                        ) from e
                    wait_seconds = setup_delays[setup_attempt]
                    print(f"Appium Settings app 启动失败，第 {setup_attempt + 2} 次重试前等待 {wait_seconds:.1f} 秒...")
                    time.sleep(wait_seconds)
                    continue

                if is_last:
                    raise
                wait_seconds = setup_delays[setup_attempt]
                print(f"驱动初始化异常，第 {setup_attempt + 2} 次重试前等待 {wait_seconds:.1f} 秒: {e}")
                time.sleep(wait_seconds)

        if self.driver is None:
            raise RuntimeError("驱动初始化失败")

        driver = self._get_driver()

        # 更激进的性能优化设置
        driver.update_settings({
            "waitForIdleTimeout": 0,  # 空闲时间，0 表示不等待，让 UIAutomator2 不等页面“空闲”再返回
            "actionAcknowledgmentTimeout": 0,  # 禁止等待动作确认
            "keyInjectionDelay": 0,  # 禁止输入延迟
            "waitForSelectorTimeout": 300,  # 从500减少到300ms
            "ignoreUnimportantViews": False,  # 保持false避免元素丢失
            "allowInvisibleElements": True,
            "enableNotificationListener": False,  # 禁用通知监听
        })

        # 极短的显式等待，抢票场景下速度优先
        self.wait = WebDriverWait(driver, 2)  # 从5秒减少到2秒

    def ultra_fast_click(self, by, value, timeout=1.5):
        """超快速点击 - 适合抢票场景"""
        driver = self._get_driver()
        try:
            # 直接查找并点击，不等待可点击状态
            el = WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((by, value))
            )
            # 使用坐标点击更快
            rect = el.rect
            x = rect['x'] + rect['width'] // 2
            y = rect['y'] + rect['height'] // 2
            driver.execute_script("mobile: clickGesture", {
                "x": x,
                "y": y,
                "duration": 50  # 极短点击时间
            })
            return True
        except TimeoutException:
            return False

    def batch_click(self, elements_info, delay=0.1):
        """批量点击操作"""
        for by, value in elements_info:
            if self.ultra_fast_click(by, value):
                if delay > 0:
                    time.sleep(delay)
            else:
                print(f"点击失败: {value}")

    def ultra_batch_click(self, elements_info, timeout=2):
        """超快批量点击 - 带等待机制"""
        driver = self._get_driver()
        coordinates = []
        # 批量收集坐标，带超时等待
        for by, value in elements_info:
            try:
                # 等待元素出现
                el = WebDriverWait(driver, timeout).until(
                    EC.presence_of_element_located((by, value))
                )
                rect = el.rect
                x = rect['x'] + rect['width'] // 2
                y = rect['y'] + rect['height'] // 2
                coordinates.append((x, y, value))
            except TimeoutException:
                print(f"超时未找到用户: {value}")
            except Exception as e:
                print(f"查找用户失败 {value}: {e}")
        print(f"成功找到 {len(coordinates)} 个用户")
        # 快速连续点击
        for i, (x, y, value) in enumerate(coordinates):
            driver.execute_script("mobile: clickGesture", {
                "x": x,
                "y": y,
                "duration": 30
            })
            if i < len(coordinates) - 1:
                time.sleep(0.01)
            print(f"点击用户: {value}")

    def smart_wait_and_click(self, by, value, backup_selectors=None, timeout=1.5):
        """智能等待和点击 - 支持备用选择器"""
        driver = self._get_driver()
        selectors = [(by, value)]
        if backup_selectors:
            selectors.extend(backup_selectors)

        for selector_by, selector_value in selectors:
            for _ in range(2):
                try:
                    try:
                        el = WebDriverWait(driver, timeout).until(
                            EC.element_to_be_clickable((selector_by, selector_value))
                        )
                    except TimeoutException:
                        el = WebDriverWait(driver, timeout).until(
                            EC.presence_of_element_located((selector_by, selector_value))
                        )

                    rect = el.rect
                    x = rect['x'] + rect['width'] // 2
                    y = rect['y'] + rect['height'] // 2
                    driver.execute_script("mobile: clickGesture", {"x": x, "y": y, "duration": 50})
                    return True
                except TimeoutException:
                    break
                except Exception:
                    time.sleep(0.06)
        return False

    def _tap_element_center(self, element, duration=50):
        """点击元素中心点，统一点击行为。"""
        driver = self._get_driver()
        rect = element.rect
        x = rect['x'] + rect['width'] // 2
        y = rect['y'] + rect['height'] // 2
        driver.execute_script("mobile: clickGesture", {
            "x": x,
            "y": y,
            "duration": duration,
        })

    def _log_visible_texts(self, stage, limit=20):
        """打印当前页面可见文本样本，便于定位选择器失效。"""
        driver = self._get_driver()
        try:
            elements = driver.find_elements(By.XPATH, '//*[@text!=""]')
            samples = []
            for element in elements:
                raw_text = element.get_attribute("text")
                text = raw_text.strip() if isinstance(raw_text, str) else ""
                if not text or text in samples:
                    continue
                if len(text) > 28:
                    text = f"{text[:28]}..."
                samples.append(text)
                if len(samples) >= limit:
                    break
            if samples:
                print(f"{stage}可见文本样本: {' | '.join(samples)}")
            else:
                print(f"{stage}可见文本样本: 无")
        except Exception as e:
            print(f"{stage}可见文本采样失败: {e}")

    def _wait_city_section_ready(self, timeout=6):
        """等待城市区域加载，降低首轮点击失败率。"""
        driver = self._get_driver()
        ready_selectors = [
            (By.ID, "cn.damai:id/tv_tour_city"),
            (By.ID, "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl"),
            (By.ID, "cn.damai:id/project_detail_perform_flowlayout"),
        ]

        end_time = time.time() + timeout
        while time.time() < end_time:
            for selector_by, selector_value in ready_selectors:
                if driver.find_elements(selector_by, selector_value):
                    return True
            time.sleep(0.2)
        return False

    def _select_city_with_fallback(self):
        """城市选择增强版：预热等待 + 多轮备援 + 滚动查找。"""
        driver = self._get_driver()
        raw_city = str(self.config.city).strip()
        city_variants = [raw_city]
        if raw_city.endswith("市"):
            city_variants.append(raw_city[:-1])
        else:
            city_variants.append(f"{raw_city}市")

        dedup_variants = []
        for city in city_variants:
            if city and city not in dedup_variants:
                dedup_variants.append(city)

        selectors = []
        for city in dedup_variants:
            selectors.extend([
                (AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().text("{city}")'),
                (AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().textContains("{city}")'),
                (By.XPATH, f'//*[@text="{city}"]'),
                (By.XPATH, f'//*[contains(@text,"{city}")]'),
            ])

        if not selectors:
            return False

        if not self._wait_city_section_ready(timeout=6):
            print("城市区域预热超时，直接进入备援点击")

        for round_index in range(3):
            timeout = 1.2 + 0.8 * round_index
            primary_by, primary_value = selectors[0]
            if self.smart_wait_and_click(primary_by, primary_value, selectors[1:], timeout=timeout):
                return True

            # 滚动查找城市，常见于城市列表未在首屏
            for city in dedup_variants:
                try:
                    city_element = driver.find_element(
                        AppiumBy.ANDROID_UIAUTOMATOR,
                        f'new UiScrollable(new UiSelector().scrollable(true)).scrollTextIntoView("{city}")'
                    )
                    driver.execute_script('mobile: clickGesture', {'elementId': city_element.id})
                    return True
                except Exception:
                    continue

            # 轻量滑动触发列表刷新，避免首轮卡死
            try:
                driver.swipe(540, 1500, 540, 900, 150)
            except Exception:
                pass
            time.sleep(0.15 + round_index * 0.1)

        return False

    def _click_booking_button_with_fallback(self):
        """预约按钮增强版：ID/文本多路匹配 + 底部按钮兜底。"""
        driver = self._get_driver()
        book_selectors = [
            (By.ID, "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl"),
            (By.ID, "cn.damai:id/btn_buy_view"),
            (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textMatches(".*预约.*|.*预订.*|.*购票.*|.*购买.*|.*立即.*|.*选座.*")'),
            (By.XPATH, '//*[contains(@text,"预约") or contains(@text,"预订") or contains(@text,"购票") or contains(@text,"购买") or contains(@text,"立即") or contains(@text,"选座")]'),
        ]
        if self.smart_wait_and_click(*book_selectors[0], book_selectors[1:], timeout=2.2):
            return True

        buy_words = ("预约", "预订", "购票", "购买", "立即", "选座", "开抢")
        try:
            clickable_nodes = driver.find_elements(By.XPATH, '//*[@clickable="true" and @text!=""]')
            page_height = driver.get_window_size().get("height", 0)
            for node in clickable_nodes:
                raw_text = node.get_attribute("text")
                text = raw_text.strip() if isinstance(raw_text, str) else ""
                if not text:
                    continue
                if not any(word in text for word in buy_words):
                    continue

                rect = node.rect
                if page_height and rect.get("y", 0) < int(page_height * 0.45):
                    continue

                try:
                    self._tap_element_center(node, duration=50)
                    return True
                except Exception:
                    continue
        except Exception:
            pass

        self._log_visible_texts("预约按钮失败后")
        return False

    def _build_price_keywords(self):
        """构建票价匹配关键词，兼容文本和纯数字场景。"""
        raw_price = str(getattr(self.config, "price", "")).strip()
        keywords = []
        if raw_price:
            keywords.append(raw_price)
            normalized = raw_price.replace("（", "(").replace("）", ")")
            if normalized not in keywords:
                keywords.append(normalized)

            numbers = re.findall(r"\d+", raw_price)
            for number in numbers:
                for variant in (number, f"{number}元"):
                    if variant not in keywords:
                        keywords.append(variant)

        dedup_keywords = []
        for keyword in keywords:
            if keyword and keyword not in dedup_keywords:
                dedup_keywords.append(keyword)
        return dedup_keywords

    def _select_price_with_fallback(self):
        """票价选择增强版：文本匹配优先，容器 index 次之，全局匹配兜底。"""
        driver = self._get_driver()

        price_keywords = self._build_price_keywords()
        price_selectors = []
        for keyword in price_keywords:
            escaped_keyword = keyword.replace('"', '\\"')
            price_selectors.extend([
                (AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().text("{escaped_keyword}")'),
                (AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().textContains("{escaped_keyword}")'),
                (By.XPATH, f'//*[@text="{keyword}"]'),
                (By.XPATH, f'//*[contains(@text,"{keyword}")]'),
            ])

        if price_selectors:
            primary_by, primary_value = price_selectors[0]
            if self.smart_wait_and_click(primary_by, primary_value, price_selectors[1:], timeout=1.8):
                return True

        container_ids = [
            "cn.damai:id/project_detail_perform_price_flowlayout",
            "cn.damai:id/trade_project_detail_perform_price_flowlayout",
            "cn.damai:id/project_detail_perform_flowlayout",
        ]

        price_container = None
        for container_id in container_ids:
            elements = driver.find_elements(By.ID, container_id)
            if elements:
                price_container = elements[0]
                break

        if price_container is not None:
            candidates = price_container.find_elements(By.XPATH, './/*[@clickable="true"]')
            filtered_candidates = []
            for candidate in candidates:
                raw_text = candidate.get_attribute("text")
                text = raw_text.strip() if isinstance(raw_text, str) else ""
                raw_content_desc = candidate.get_attribute("contentDescription")
                raw_content_desc_alt = candidate.get_attribute("content-desc")
                content_desc_val = raw_content_desc if isinstance(raw_content_desc, str) else ""
                if not content_desc_val and isinstance(raw_content_desc_alt, str):
                    content_desc_val = raw_content_desc_alt
                content_desc = content_desc_val.strip()
                combined = f"{text} {content_desc}"
                if any(bad in combined for bad in ("缺货", "无票", "售罄")):
                    continue

                rect = candidate.rect
                if rect.get("width", 0) < 60 or rect.get("height", 0) < 40:
                    continue
                filtered_candidates.append(candidate)

            usable_candidates = filtered_candidates if filtered_candidates else candidates
            if usable_candidates:
                target_index = int(getattr(self.config, "price_index", 0) or 0)
                target_index = max(0, min(target_index, len(usable_candidates) - 1))
                try:
                    self._tap_element_center(usable_candidates[target_index], duration=50)
                    return True
                except Exception:
                    pass

                for candidate in usable_candidates[:5]:
                    try:
                        self._tap_element_center(candidate, duration=50)
                        return True
                    except Exception:
                        continue

        generic_price_selectors = [
            (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textMatches(".*\\d+元.*|.*看台.*|.*内场.*|.*票.*")'),
            (By.XPATH, '//*[contains(@text,"元") or contains(@text,"看台") or contains(@text,"内场") or contains(@text,"票")]'),
        ]
        if self.smart_wait_and_click(*generic_price_selectors[0], generic_price_selectors[1:], timeout=1.4):
            return True

        self._log_visible_texts("票价选择失败后")
        return False

    def run_ticket_grabbing(self):
        """执行抢票主流程"""
        try:
            if self.driver is None or self.wait is None:
                self._setup_driver()

            driver = self._get_driver()
            wait = self._get_wait()

            print("开始抢票流程...")
            start_time = time.time()

            # 1. 城市选择 - 准备多个备选方案
            print("选择城市...")
            if not self._select_city_with_fallback():
                print("城市选择失败")
                return False

            # 2. 点击预约按钮 - 多种可能的按钮文本
            print("点击预约按钮...")
            if not self._click_booking_button_with_fallback():
                print("预约按钮点击失败")
                return False

            # 3. 票价选择 - 优化查找逻辑
            print("选择票价...")
            if not self._select_price_with_fallback():
                print("票价选择失败")
                return False

            # 4. 数量选择
            print("选择数量...")
            if driver.find_elements(by=By.ID, value='layout_num'):
                clicks_needed = len(self.config.users) - 1
                if clicks_needed > 0:
                    try:
                        plus_button = driver.find_element(By.ID, 'img_jia')
                        for i in range(clicks_needed):
                            rect = plus_button.rect
                            x = rect['x'] + rect['width'] // 2
                            y = rect['y'] + rect['height'] // 2
                            driver.execute_script("mobile: clickGesture", {
                                "x": x,
                                "y": y,
                                "duration": 50
                            })
                            time.sleep(0.02)
                    except Exception as e:
                        print(f"快速点击加号失败: {e}")

            # if self.driver.find_elements(by=By.ID, value='layout_num') and self.config.users is not None:
            #     for i in range(len(self.config.users) - 1):
            #         self.driver.find_element(by=By.ID, value='img_jia').click()

            # 5. 确定购买
            print("确定购买...")
            if not self.ultra_fast_click(By.ID, "btn_buy_view"):
                # 备用按钮文本
                self.ultra_fast_click(AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textMatches(".*确定.*|.*购买.*")')

            # 6. 批量选择用户
            print("选择用户...")
            user_clicks = [(AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().text("{user}")') for user in
                           self.config.users]
            # self.batch_click(user_clicks, delay=0.05)  # 极短延迟
            self.ultra_batch_click(user_clicks)

            # 7. 提交订单
            print("提交订单...")
            submit_selectors = [
                (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("立即提交")'),
                (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textMatches(".*提交.*|.*确认.*")'),
                (By.XPATH, '//*[contains(@text,"提交")]')
            ]
            self.smart_wait_and_click(*submit_selectors[0], submit_selectors[1:])

            end_time = time.time()
            print(f"抢票流程完成，耗时: {end_time - start_time:.2f}秒")
            return True

        except Exception as e:
            print(f"抢票过程发生错误: {e}")
            return False
        finally:
            time.sleep(1)  # 给最后的操作一点时间
            if self.driver is not None:
                self.driver.quit()
                self.driver = None
                self.wait = None

    def run_with_retry(self, max_retries=3):
        """带重试机制的抢票"""
        retry_schedule = [1.0, 1.8, 3.0, 5.0]
        for attempt in range(max_retries):
            print(f"第 {attempt + 1} 次尝试...")

            if self.driver is None or self.wait is None:
                try:
                    self._setup_driver()
                except Exception as e:
                    print(f"第 {attempt + 1} 次驱动初始化失败: {e}")
                    if attempt < max_retries - 1:
                        wait_seconds = retry_schedule[min(attempt, len(retry_schedule) - 1)]
                        print(f"{wait_seconds:.1f}秒后重试...")
                        time.sleep(wait_seconds)
                    continue

            if self.run_ticket_grabbing():
                print("抢票成功！")
                return True
            else:
                print(f"第 {attempt + 1} 次尝试失败")
                if attempt < max_retries - 1:
                    wait_seconds = retry_schedule[min(attempt, len(retry_schedule) - 1)]
                    print(f"{wait_seconds:.1f}秒后重试...")
                    time.sleep(wait_seconds)

        print("所有尝试均失败")
        return False


# 使用示例
if __name__ == "__main__":
    bot = DamaiBot()
    bot.run_with_retry(max_retries=3)
