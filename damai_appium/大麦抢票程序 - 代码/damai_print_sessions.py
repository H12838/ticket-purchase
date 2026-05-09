import os
import re
import time
import pickle
from typing import List, Iterable

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

TARGET_URL = "https://m.damai.cn/shows/item.html?from=def&itemId=1035663663342&sqm=dianying.h5.unknown.value&spm=a2o71.search.list.ditem_0"
COOKIE_FILE = "cookies.pkl"


class DamaiPageInspector:
    def __init__(self) -> None:
        options = Options()
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-notifications")

        mobile_emulation = {
            "deviceMetrics": {
                "width": 412,
                "height": 915,
                "pixelRatio": 2.625,
                "mobile": True,
                "touch": True,
            },
            "clientHints": {
                "platform": "Android",
                "mobile": True,
                "platformVersion": "13",
                "model": "Pixel 7",
            },
        }
        options.add_experimental_option("mobileEmulation", mobile_emulation)

        self.driver = webdriver.Chrome(options=options)
        self.wait = WebDriverWait(self.driver, 12)

    def open_page(self) -> None:
        self.driver.get(TARGET_URL)
        time.sleep(2)
        self._try_load_cookies()
        self.driver.get(TARGET_URL)
        time.sleep(2)
        self.driver.refresh()
        time.sleep(2)

    def _try_load_cookies(self) -> None:
        if not os.path.exists(COOKIE_FILE):
            return
        try:
            cookies = pickle.load(open(COOKIE_FILE, "rb"))
            for cookie in cookies:
                cookie_dict = {
                    "domain": cookie.get("domain") or ".damai.cn",
                    "name": cookie.get("name"),
                    "value": cookie.get("value"),
                }
                try:
                    self.driver.add_cookie(cookie_dict)
                except Exception:
                    pass
            print("已尝试加载 cookies.pkl")
        except Exception as exc:
            print(f"加载 cookies.pkl 失败: {exc}")

    def confirm_notice(self) -> bool:
        selectors = [
            (By.XPATH, "//div[contains(@class,'health-info-button') and normalize-space()='确认并知悉']"),
            (By.XPATH, "//*[normalize-space()='确认并知悉']"),
            (By.XPATH, "//button[normalize-space()='确认并知悉']"),
        ]

        for by, selector in selectors:
            try:
                button = WebDriverWait(self.driver, 4).until(
                    EC.element_to_be_clickable((by, selector))
                )
                try:
                    button.click()
                except Exception:
                    self.driver.execute_script("arguments[0].click();", button)
                print("已处理“确认并知悉”弹窗")
                time.sleep(1)
                return True
            except Exception:
                continue
        return False

    def expand_page(self) -> None:
        for y in (500, 1000, 1600, 2200):
            self.driver.execute_script(f"window.scrollTo(0, {y});")
            time.sleep(0.8)

    def wait_for_manual_login_if_needed(self) -> None:
        current = self.driver.current_url
        title = self.driver.title
        if "login" in current.lower() or "登录" in title:
            input("检测到可能需要登录。请手动完成登录后，回到这里按回车继续...\n")
            time.sleep(1)
            self.driver.get(TARGET_URL)
            time.sleep(2)

    def collect_visible_texts(self) -> List[str]:
        elements = self.driver.find_elements(By.XPATH, "//*[self::div or self::span or self::button or self::li or self::p]")
        texts: List[str] = []
        seen = set()
        for el in elements:
            try:
                text = el.text.strip()
                if not text or not el.is_displayed():
                    continue
                text = re.sub(r"\s+", " ", text)
                if len(text) > 60:
                    continue
                if text in seen:
                    continue
                seen.add(text)
                texts.append(text)
            except Exception:
                pass
        return texts

    @staticmethod
    def _match_any(text: str, keywords: Iterable[str]) -> bool:
        return any(k in text for k in keywords)

    def print_candidates(self) -> None:
        texts = self.collect_visible_texts()

        session_keywords = ["场", "月", "日", "周", ":", "19:30", "20:00", "演出"]
        price_keywords = ["¥", "￥", "元", "票档", "看台", "内场", "VIP"]

        sessions = []
        prices = []
        other = []

        for text in texts:
            if self._match_any(text, price_keywords):
                prices.append(text)
            elif self._match_any(text, session_keywords):
                sessions.append(text)
            elif "购票须知" in text or "实名" in text or "限购" in text:
                other.append(text)

        sessions = self._dedupe_preserve(sessions)
        prices = self._dedupe_preserve(prices)
        other = self._dedupe_preserve(other)

        print("\n" + "=" * 50)
        print("页面标题:", self.driver.title)
        print("当前网址:", self.driver.current_url)
        print("=" * 50)

        print("\n【可能的场次 / 日期 / 时间】")
        if sessions:
            for item in sessions:
                print(item)
        else:
            print("未识别到明显的场次文字")

        print("\n【可能的票档 / 价格】")
        if prices:
            for item in prices:
                print(item)
        else:
            print("未识别到明显的票档文字")

        print("\n【其他可能有用的信息】")
        if other:
            for item in other[:20]:
                print(item)
        else:
            print("未抓到额外提示文字")

        print("\n" + "=" * 50)
        print("如果结果不完整，请在浏览器里手动向下滚动或点开场次区域后，回到终端按回车再抓一次。")
        print("=" * 50 + "\n")

    @staticmethod
    def _dedupe_preserve(items: List[str]) -> List[str]:
        seen = set()
        result = []
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            result.append(item)
        return result

    def run(self) -> None:
        self.open_page()
        self.wait_for_manual_login_if_needed()
        self.confirm_notice()
        self.expand_page()
        self.print_candidates()

        while True:
            answer = input("输入 y 重新抓取一次，其他任意键退出: ").strip().lower()
            if answer != "y":
                break
            self.confirm_notice()
            self.expand_page()
            self.print_candidates()

    def close(self) -> None:
        self.driver.quit()


if __name__ == "__main__":
    inspector = DamaiPageInspector()
    try:
        inspector.run()
    finally:
        inspector.close()
