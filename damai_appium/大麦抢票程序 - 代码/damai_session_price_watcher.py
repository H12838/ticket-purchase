import argparse
import hashlib
import json
import pickle
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

TARGET_URL = "https://m.damai.cn/shows/item.html?from=def&itemId=1035761361090&sqm=dianying.h5.unknown.value&spm=a2o71.home.list.ditem_11"
NOTICE_XPATH = "//div[contains(@class,'health-info-button') and normalize-space()='确认并知悉']"

DATE_RE = re.compile(r"(?:20\d{2}[./-]\d{1,2}[./-]\d{1,2}|\d{1,2}月\d{1,2}日|\d{1,2}[./-]\d{1,2})")
TIME_RE = re.compile(r"(?:[01]?\d|2[0-3])[:：][0-5]\d")
WEEK_RE = re.compile(r"周[一二三四五六日天]")
PRICE_RE = re.compile(r"(?:[¥￥]\s*\d{1,6}(?:\.\d{1,2})?|\d{1,6}(?:\.\d{1,2})?\s*元)")
SESSION_HINT_RE = re.compile(r"场次|开场|开演|演出时间|日期|时间|加场|预售")
PRICE_HINT_RE = re.compile(r"票档|价格|票价|看台|内场|VIP|预售|早鸟|普通票|套票|学生票|限量")
NOTICE_HINT_RE = re.compile(r"实名|购票须知|限购|退票|知悉|规则|证件|入场")
SKIP_TEXT_RE = re.compile(r"^(确认并知悉|立即购买|提交订单|登录|注册|首页|详情|更多)$")

SESSION_ATTR_HINTS = ("session", "date", "time", "show", "performance", "calendar")
PRICE_ATTR_HINTS = ("price", "ticket", "sku", "seat", "tier")
NOTICE_ATTR_HINTS = ("notice", "rule", "health")


def now_str() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def clean_text(text: str) -> str:
    text = (text or "").replace("\u00a0", " ")
    return re.sub(r"\s+", " ", text).strip()


class DamaiWatcher:
    def __init__(
        self,
        url: str,
        outdir: str,
        interval: float,
        always_save: bool,
        headless: bool,
        cookies_path: str,
    ):
        self.url = url
        self.outdir = Path(outdir)
        self.interval = interval
        self.always_save = always_save
        self.cookies_path = Path(cookies_path)
        ensure_dir(self.outdir)
        ensure_dir(self.outdir / "snapshots")
        self.driver = self._build_driver(headless=headless)
        self.last_page_hash = None
        self.last_extract_hash = None
        self.history_log = self.outdir / "history.jsonl"

    def _build_driver(self, headless: bool) -> webdriver.Chrome:
        options = Options()
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
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--lang=zh-CN")
        options.add_argument("--window-size=412,915")
        if headless:
            options.add_argument("--headless=new")
        return webdriver.Chrome(options=options)

    def open_page(self) -> None:
        self.driver.get(self.url)
        self._try_load_cookies()
        self.driver.get(self.url)
        try:
            WebDriverWait(self.driver, 15).until(
                EC.presence_of_all_elements_located((By.XPATH, "//*"))
            )
        except TimeoutException:
            pass

    def _try_load_cookies(self) -> None:
        if not self.cookies_path.exists():
            return
        try:
            cookies = pickle.load(open(self.cookies_path, "rb"))
            for cookie in cookies:
                cookie_dict = {
                    "domain": cookie.get("domain") or ".damai.cn",
                    "name": cookie.get("name"),
                    "value": cookie.get("value"),
                }
                try:
                    self.driver.add_cookie(cookie_dict)
                except Exception:
                    continue
        except Exception as e:
            print(f"[WARN] 加载 cookies 失败: {e}")

    def wait_for_user_ready(self) -> None:
        print("浏览器已打开。")
        print("你可以现在手动做这些事：")
        print("1. 登录账号（如果需要）")
        print("2. 手动关闭任何提示弹窗")
        print("3. 手动滚动到场次/票档区域")
        print("4. 然后回终端按回车开始监听")
        input("准备好后按回车开始监听... ")

    def try_confirm_notice(self) -> bool:
        try:
            btn = WebDriverWait(self.driver, 3).until(
                EC.element_to_be_clickable((By.XPATH, NOTICE_XPATH))
            )
            btn.click()
            print("[INFO] 已尝试关闭‘确认并知悉’弹窗")
            return True
        except Exception:
            return False

    def get_visible_text(self) -> str:
        try:
            body = self.driver.find_element(By.TAG_NAME, "body")
            return clean_text(body.text)
        except Exception:
            return ""

    def get_page_source(self) -> str:
        try:
            return self.driver.page_source or ""
        except Exception:
            return ""

    def get_outer_html(self) -> str:
        try:
            return self.driver.execute_script("return document.documentElement.outerHTML;") or ""
        except Exception:
            return ""

    def get_iframe_htmls(self) -> Dict[str, str]:
        result: Dict[str, str] = {}
        try:
            iframes = self.driver.find_elements(By.TAG_NAME, "iframe")
        except Exception:
            return result

        for idx in range(len(iframes)):
            try:
                iframes = self.driver.find_elements(By.TAG_NAME, "iframe")
                self.driver.switch_to.frame(iframes[idx])
                result[f"iframe_{idx}.html"] = self.driver.execute_script(
                    "return document.documentElement.outerHTML;"
                ) or ""
            except Exception as e:
                result[f"iframe_{idx}.txt"] = f"[ERROR] 无法读取 iframe {idx}: {e}"
            finally:
                try:
                    self.driver.switch_to.default_content()
                except Exception:
                    pass
        return result

    def _iter_candidate_texts(self, raw_text: str) -> List[str]:
        parts = [clean_text(x) for x in re.split(r"[\n\r]+", raw_text or "")]
        parts = [x for x in parts if x]
        whole = clean_text(raw_text)
        if whole:
            parts.append(whole)
        seen = set()
        out = []
        for text in parts:
            if text in seen:
                continue
            seen.add(text)
            out.append(text)
        return out

    def _element_attr_text(self, el) -> str:
        chunks = []
        for attr in ("class", "id", "role", "data-spm", "data-spm-anchor-id"):
            try:
                value = el.get_attribute(attr)
            except Exception:
                value = ""
            if value:
                chunks.append(str(value).lower())
        return " ".join(chunks)

    def _score_session_text(self, text: str, attr_text: str) -> int:
        score = 0
        if DATE_RE.search(text):
            score += 3
        if TIME_RE.search(text):
            score += 3
        if WEEK_RE.search(text):
            score += 1
        if SESSION_HINT_RE.search(text):
            score += 2
        if any(h in attr_text for h in SESSION_ATTR_HINTS):
            score += 2
        if 4 <= len(text) <= 40:
            score += 1
        if PRICE_RE.search(text):
            score -= 1
        if text.count(" ") > 8 or len(text) > 80:
            score -= 2
        return score

    def _score_price_text(self, text: str, attr_text: str) -> int:
        score = 0
        if PRICE_RE.search(text):
            score += 4
        if PRICE_HINT_RE.search(text):
            score += 2
        if any(h in attr_text for h in PRICE_ATTR_HINTS):
            score += 2
        if 2 <= len(text) <= 30:
            score += 1
        if DATE_RE.search(text) and not PRICE_RE.search(text):
            score -= 1
        if text.count(" ") > 6 or len(text) > 60:
            score -= 2
        return score

    def _score_notice_text(self, text: str, attr_text: str) -> int:
        score = 0
        if NOTICE_HINT_RE.search(text):
            score += 3
        if any(h in attr_text for h in NOTICE_ATTR_HINTS):
            score += 2
        if 4 <= len(text) <= 80:
            score += 1
        return score

    def _collect_scored_from_current_context(self, source_name: str) -> Dict[str, List[Dict[str, object]]]:
        scored = {"sessions": [], "prices": [], "notices": []}
        try:
            WebDriverWait(self.driver, 5).until(
                EC.presence_of_all_elements_located((By.XPATH, "//*"))
            )
        except TimeoutException:
            pass

        selectors = [
            (By.XPATH, "//*[self::div or self::span or self::button or self::li or self::p or self::a or self::label]"),
            (By.CSS_SELECTOR, "[class*='price'], [class*='session'], [class*='date'], [class*='time'], [class*='notice'], [class*='sku'], [class*='ticket']"),
        ]

        elements = []
        seen_ids = set()
        for by, value in selectors:
            try:
                found = self.driver.find_elements(by, value)
            except Exception:
                continue
            for el in found:
                try:
                    eid = el.id
                except Exception:
                    eid = None
                if eid and eid in seen_ids:
                    continue
                if eid:
                    seen_ids.add(eid)
                elements.append(el)

        seen_entries = set()

        for el in elements:
            try:
                if not el.is_displayed():
                    continue
                attr_text = self._element_attr_text(el)
                raw_text = el.text or ""
                parent_text = ""
                try:
                    parent = el.find_element(By.XPATH, "./..")
                    if parent.is_displayed():
                        parent_text = parent.text or ""
                except Exception:
                    parent_text = ""

                candidates: List[Tuple[str, str]] = []
                for text in self._iter_candidate_texts(raw_text):
                    candidates.append((text, "node"))
                for text in self._iter_candidate_texts(parent_text):
                    if text and text != clean_text(raw_text):
                        candidates.append((text, "parent"))

                for text, origin in candidates:
                    if not text or len(text) < 2 or len(text) > 120:
                        continue
                    if SKIP_TEXT_RE.match(text):
                        continue
                    entry_key = (source_name, origin, text)
                    if entry_key in seen_entries:
                        continue
                    seen_entries.add(entry_key)

                    s_score = self._score_session_text(text, attr_text)
                    p_score = self._score_price_text(text, attr_text)
                    n_score = self._score_notice_text(text, attr_text)

                    if s_score >= 5:
                        scored["sessions"].append({
                            "text": text,
                            "score": s_score,
                            "source": f"{source_name}:{origin}",
                        })
                    if p_score >= 5:
                        scored["prices"].append({
                            "text": text,
                            "score": p_score,
                            "source": f"{source_name}:{origin}",
                        })
                    if n_score >= 4:
                        scored["notices"].append({
                            "text": text,
                            "score": n_score,
                            "source": f"{source_name}:{origin}",
                        })
            except Exception:
                continue

        return scored

    def _dedupe_and_prune(self, items: List[Dict[str, object]], max_len: int) -> List[Dict[str, object]]:
        exact_seen = set()
        deduped: List[Dict[str, object]] = []
        for item in sorted(items, key=lambda x: (-int(x["score"]), len(str(x["text"])), str(x["text"]))):
            text = str(item["text"])
            if text in exact_seen:
                continue
            exact_seen.add(text)
            deduped.append(item)

        pruned: List[Dict[str, object]] = []
        for item in deduped:
            text = str(item["text"])
            dominated = False
            for other in deduped:
                other_text = str(other["text"])
                if other_text == text:
                    continue
                if text in other_text and int(other["score"]) >= int(item["score"]) and len(other_text) <= max_len:
                    dominated = True
                    break
            if not dominated:
                pruned.append(item)
        return pruned

    def extract_candidates(self) -> Tuple[Dict[str, List[str]], Dict[str, List[Dict[str, object]]]]:
        merged = {"sessions": [], "prices": [], "notices": []}

        main_scored = self._collect_scored_from_current_context("main")
        for key in merged:
            merged[key].extend(main_scored[key])

        try:
            iframes = self.driver.find_elements(By.TAG_NAME, "iframe")
        except Exception:
            iframes = []

        for idx in range(len(iframes)):
            try:
                self.driver.switch_to.default_content()
                iframes = self.driver.find_elements(By.TAG_NAME, "iframe")
                self.driver.switch_to.frame(iframes[idx])
                iframe_scored = self._collect_scored_from_current_context(f"iframe_{idx}")
                for key in merged:
                    merged[key].extend(iframe_scored[key])
            except Exception:
                pass
            finally:
                try:
                    self.driver.switch_to.default_content()
                except Exception:
                    pass

        scored = {
            "sessions": self._dedupe_and_prune(merged["sessions"], 80),
            "prices": self._dedupe_and_prune(merged["prices"], 60),
            "notices": self._dedupe_and_prune(merged["notices"], 120),
        }
        plain = {key: [str(item["text"]) for item in scored[key]] for key in scored}
        return plain, scored

    def snapshot_payload(self) -> Dict[str, object]:
        title = self.driver.title
        current_url = self.driver.current_url
        visible_text = self.get_visible_text()
        page_source = self.get_page_source()
        outer_html = self.get_outer_html()
        iframe_htmls = self.get_iframe_htmls()
        extracted, extracted_scored = self.extract_candidates()

        page_hash_basis = "\n".join([current_url, title, outer_html, visible_text])
        extract_hash_basis = json.dumps(extracted, ensure_ascii=False, sort_keys=True)

        return {
            "captured_at": datetime.now().isoformat(timespec="seconds"),
            "title": title,
            "current_url": current_url,
            "visible_text": visible_text,
            "page_source": page_source,
            "outer_html": outer_html,
            "iframe_htmls": iframe_htmls,
            "extracted": extracted,
            "extracted_scored": extracted_scored,
            "page_hash": sha256_text(page_hash_basis),
            "extract_hash": sha256_text(extract_hash_basis),
        }

    @staticmethod
    def diff_lists(old: List[str], new: List[str]) -> Dict[str, List[str]]:
        old_set = set(old)
        new_set = set(new)
        return {
            "added": sorted(new_set - old_set),
            "removed": sorted(old_set - new_set),
        }

    def save_snapshot(self, payload: Dict[str, object], reason: str, previous_extracted: Dict[str, List[str]] = None) -> Path:
        snap_dir = self.outdir / "snapshots" / f"{now_str()}_{reason}"
        ensure_dir(snap_dir)

        write_text(snap_dir / "page_source.html", str(payload["page_source"]))
        write_text(snap_dir / "outer_html.html", str(payload["outer_html"]))
        write_text(snap_dir / "visible_text.txt", str(payload["visible_text"]))
        write_json(snap_dir / "extracted.json", payload["extracted"])
        write_json(snap_dir / "extracted_scored.json", payload["extracted_scored"])
        for name, content in payload["iframe_htmls"].items():
            write_text(snap_dir / name, str(content))

        try:
            self.driver.save_screenshot(str(snap_dir / "screenshot.png"))
        except Exception as e:
            write_text(snap_dir / "screenshot_error.txt", str(e))

        diff = None
        if previous_extracted is not None:
            diff = {
                "sessions": self.diff_lists(previous_extracted.get("sessions", []), payload["extracted"].get("sessions", [])),
                "prices": self.diff_lists(previous_extracted.get("prices", []), payload["extracted"].get("prices", [])),
                "notices": self.diff_lists(previous_extracted.get("notices", []), payload["extracted"].get("notices", [])),
            }
            write_json(snap_dir / "extracted_diff.json", diff)

        meta = {
            "captured_at": payload["captured_at"],
            "title": payload["title"],
            "current_url": payload["current_url"],
            "reason": reason,
            "page_hash": payload["page_hash"],
            "extract_hash": payload["extract_hash"],
            "diff": diff,
        }
        write_json(snap_dir / "meta.json", meta)

        with self.history_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(meta, ensure_ascii=False) + "\n")

        return snap_dir

    def print_summary(self, extracted: Dict[str, List[str]], scored: Dict[str, List[Dict[str, object]]], diff: Dict[str, Dict[str, List[str]]] = None) -> None:
        print("\n" + "=" * 60)
        print("【当前提取结果】")
        print("场次 / 日期 / 时间:")
        for item in scored.get("sessions", [])[:30]:
            print(f"  - {item['text']}  (score={item['score']}, {item['source']})")
        print("票档 / 价格:")
        for item in scored.get("prices", [])[:30]:
            print(f"  - {item['text']}  (score={item['score']}, {item['source']})")
        print("购票须知 / 限购等文字:")
        for item in scored.get("notices", [])[:30]:
            print(f"  - {item['text']}  (score={item['score']}, {item['source']})")
        if diff:
            print("\n【相较上一次的变化】")
            for group_name, changes in diff.items():
                if changes["added"] or changes["removed"]:
                    print(f"{group_name}:")
                    for x in changes["added"]:
                        print(f"  + {x}")
                    for x in changes["removed"]:
                        print(f"  - {x}")
        print("=" * 60 + "\n")

    def run(self) -> None:
        self.open_page()
        self.wait_for_user_ready()
        self.try_confirm_notice()

        previous_extracted = {"sessions": [], "prices": [], "notices": []}
        print(f"[INFO] 开始监听，轮询间隔: {self.interval} 秒")
        print(f"[INFO] 输出目录: {self.outdir.resolve()}")
        print("[INFO] 在终端按 Ctrl+C 可停止监听")

        while True:
            payload = self.snapshot_payload()
            page_changed = payload["page_hash"] != self.last_page_hash
            extract_changed = payload["extract_hash"] != self.last_extract_hash

            diff = {
                "sessions": self.diff_lists(previous_extracted.get("sessions", []), payload["extracted"].get("sessions", [])),
                "prices": self.diff_lists(previous_extracted.get("prices", []), payload["extracted"].get("prices", [])),
                "notices": self.diff_lists(previous_extracted.get("notices", []), payload["extracted"].get("notices", [])),
            }

            reason_parts = []
            if page_changed:
                reason_parts.append("page")
            if extract_changed:
                reason_parts.append("extract")
            reason = "_".join(reason_parts) if reason_parts else "nochange"

            if self.always_save or page_changed or extract_changed:
                snap_dir = self.save_snapshot(payload, reason, previous_extracted)
                print(f"[SAVE] {payload['captured_at']} -> {snap_dir.name}")
                self.print_summary(payload["extracted"], payload["extracted_scored"], diff)
            else:
                print(f"[NO CHANGE] {payload['captured_at']}")

            self.last_page_hash = payload["page_hash"]
            self.last_extract_hash = payload["extract_hash"]
            previous_extracted = payload["extracted"]
            time.sleep(self.interval)

    def close(self) -> None:
        try:
            self.driver.quit()
        except Exception:
            pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="监听大麦页面代码，并记录场次/票档变化（精筛版）")
    parser.add_argument("--url", default=TARGET_URL, help="要监听的页面 URL")
    parser.add_argument("--interval", type=float, default=5.0, help="轮询间隔（秒）")
    parser.add_argument("--outdir", default="damai_session_price_logs", help="输出目录")
    parser.add_argument("--always-save", action="store_true", help="每次轮询都保存一份快照")
    parser.add_argument("--headless", action="store_true", help="使用无头模式")
    parser.add_argument("--cookies", default="cookies.pkl", help="cookies.pkl 路径")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    watcher = DamaiWatcher(
        url=args.url,
        outdir=args.outdir,
        interval=args.interval,
        always_save=args.always_save,
        headless=args.headless,
        cookies_path=args.cookies,
    )
    try:
        watcher.run()
    except KeyboardInterrupt:
        print("\n[INFO] 已停止监听")
        return 0
    except WebDriverException as e:
        print(f"[ERROR] WebDriver 运行失败: {e}")
        return 1
    finally:
        watcher.close()


if __name__ == "__main__":
    sys.exit(main())
