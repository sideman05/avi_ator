#!/usr/bin/env python3
"""
Aviator Round Monitor - fixed version.

What this script does:
- Opens the Betpawa Aviator page.
- Optionally logs in.
- Searches the main page and nested iframes for the cashout value.
- Detects the end of a round when the cashout value falls from a positive number to 0
  and remains 0 for a configurable number of checks.

Important:
Only use automation where it is allowed by the website's terms and local rules.
"""

import argparse
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Tuple, List, Dict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.firefox.service import Service as FirefoxService
from selenium.webdriver.support.ui import WebDriverWait


def _load_local_timezone():
    preferred_zone = os.environ.get("AVIATOR_TIME_ZONE", "Africa/Dar_es_Salaam")
    fallback_zone = "Africa/Dar_es_Salaam"

    for zone_name in (preferred_zone, fallback_zone):
        try:
            return ZoneInfo(zone_name)
        except ZoneInfoNotFoundError:
            continue

    return None


LOCAL_TIME_ZONE = _load_local_timezone()


def local_now():
    if LOCAL_TIME_ZONE is not None:
        return datetime.now(LOCAL_TIME_ZONE)
    return datetime.now().astimezone()


@dataclass
class TheorySignal:
    theory: str
    trigger_odd: float
    trigger_index: int
    play_steps: List[int]
    reason: str
    weight: int
    created_at: str = field(default_factory=lambda: local_now().strftime("%Y-%m-%d %H:%M:%S"))


class AviatorPredictionEngine:
    """
    Combines 3 theories:
    1. Purple Train Theory
    2. Double Digits Role
    3. Ping Pong Theory

    History order:
        oldest -> newest

    Example:
        self.odds_history = [1.20, 2.30, 4.50, 3.22, 12.40]
    """

    def __init__(self, max_history_size: int = 100):
        self.odds_history: List[float] = []
        self.active_signals: List[TheorySignal] = []
        self.max_history_size = max_history_size

        # You can adjust these weights later after testing.
        self.weights = {
            "purple_train": 35,
            "double_digits": 30,
            "ping_pong": 40,
        }

    # -----------------------------
    # Basic odd classification
    # -----------------------------

    def classify_odd(self, odd: float) -> str:
        if odd < 2.00:
            return "blue"

        if 2.00 <= odd <= 3.99:
            return "low_purple"

        if 4.00 <= odd <= 9.99:
            return "high_purple"

        if 10.00 <= odd <= 199.99:
            return "pink"

        return "high_pink"

    def is_blue(self, odd: float) -> bool:
        return odd < 2.00

    def is_purple(self, odd: float) -> bool:
        return 2.00 <= odd <= 9.99

    def is_high_purple(self, odd: float) -> bool:
        return 4.00 <= odd <= 9.99

    def is_pink(self, odd: float) -> bool:
        return odd >= 10.00

    def is_high_pink(self, odd: float) -> bool:
        return odd >= 200.00

    def is_double_digit(self, odd: float) -> bool:
        """
        Examples:
            1.00 -> True
            1.22 -> True
            5.99 -> True
            7.66 -> True
            4.22 -> True
            3.88 -> True
            3.83 -> False
        """
        text = f"{odd:.2f}"
        decimals = text.split(".")[1]
        return decimals[0] == decimals[1]

    # -----------------------------
    # Market analysis
    # -----------------------------

    def longest_run(self, odds: List[float], condition_func) -> int:
        longest = 0
        current = 0

        for odd in odds:
            if condition_func(odd):
                current += 1
                longest = max(longest, current)
            else:
                current = 0

        return longest

    def analyze_market(self) -> Dict:
        """
        Simple market classifier based on the book's market-analysis idea.
        """
        recent = self.odds_history[-30:]

        if not recent:
            return {
                "state": "unknown",
                "pink_count": 0,
                "high_pink_count": 0,
                "high_purple_count": 0,
                "blue_train": 0,
                "purple_train": 0,
            }

        pink_count = sum(1 for odd in recent if self.is_pink(odd))
        high_pink_count = sum(1 for odd in recent if self.is_high_pink(odd))
        high_purple_count = sum(1 for odd in recent if self.is_high_purple(odd))
        blue_train = self.longest_run(recent, self.is_blue)
        purple_train = self.longest_run(recent, self.is_purple)

        stable_score = 0

        if pink_count >= 3:
            stable_score += 2

        if high_pink_count >= 1:
            stable_score += 2

        if high_purple_count >= 1:
            stable_score += 1

        if purple_train >= 4:
            stable_score += 2

        if blue_train >= 5:
            stable_score -= 2

        if pink_count == 0 and high_purple_count == 0:
            state = "unstable"
        elif stable_score >= 4:
            state = "stable"
        elif stable_score >= 1:
            state = "transition"
        else:
            state = "unstable"

        return {
            "state": state,
            "pink_count": pink_count,
            "high_pink_count": high_pink_count,
            "high_purple_count": high_purple_count,
            "blue_train": blue_train,
            "purple_train": purple_train,
        }

    # -----------------------------
    # Theory 1: Purple Train
    # -----------------------------

    def detect_purple_train_signal(self) -> Optional[TheorySignal]:
        """
        Purple Train Theory:
        If 4+ purple odds appear in succession, hunt pink in the next steps.
        """
        if len(self.odds_history) < 4:
            return None

        recent_4 = self.odds_history[-4:]

        if all(self.is_purple(odd) for odd in recent_4):
            latest_odd = self.odds_history[-1]
            trigger_index = len(self.odds_history) - 1

            return TheorySignal(
                theory="Purple Train",
                trigger_odd=latest_odd,
                trigger_index=trigger_index,
                play_steps=[1, 2, 3, 4],
                reason=f"Last 4 odds are purple: {recent_4}",
                weight=self.weights["purple_train"],
            )

        return None

    # -----------------------------
    # Theory 2: Double Digits
    # -----------------------------

    def detect_double_digits_signal(self) -> Optional[TheorySignal]:
        """
        Double Digits Role:
        If the latest odd has matching decimal digits, hunt pink within 4 steps.
        """
        if not self.odds_history:
            return None

        latest_odd = self.odds_history[-1]

        if self.is_double_digit(latest_odd):
            trigger_index = len(self.odds_history) - 1

            return TheorySignal(
                theory="Double Digits",
                trigger_odd=latest_odd,
                trigger_index=trigger_index,
                play_steps=[1, 2, 3, 4],
                reason=f"Latest odd {latest_odd:.2f} has matching decimal digits.",
                weight=self.weights["double_digits"],
            )

        return None

    # -----------------------------
    # Theory 3: Ping Pong
    # -----------------------------

    def ping_pong_rule(self, odd: float) -> Optional[Dict]:
        """
        Ping Pong Theory ranges from the book.

        Returns:
            {
                "skip": 0 or 1,
                "label": "20s pink"
            }
        """
        if 10.00 <= odd <= 10.99:
            return {"skip": 1, "label": "10.xx pink"}

        if 13.00 <= odd <= 13.99:
            return {"skip": 0, "label": "13.xx pink"}

        if 17.00 <= odd <= 17.99:
            return {"skip": 0, "label": "17.xx pink"}

        if 20.00 <= odd <= 29.99:
            return {"skip": 0, "label": "20s pink"}

        if 30.00 <= odd <= 39.99:
            return {"skip": 0, "label": "30s pink"}

        if 60.00 <= odd <= 69.99:
            return {"skip": 1, "label": "60s pink"}

        if 200.00 <= odd <= 299.99:
            return {"skip": 1, "label": "200s pink"}

        return None

    def detect_ping_pong_signal(self) -> Optional[TheorySignal]:
        """
        Ping Pong Theory:
        Special pink ranges can indicate another pink within about 4 steps.
        """
        if not self.odds_history:
            return None

        latest_odd = self.odds_history[-1]
        rule = self.ping_pong_rule(latest_odd)

        if rule is None:
            return None

        trigger_index = len(self.odds_history) - 1
        skip = rule["skip"]

        # If skip = 0, watch steps 1,2,3,4.
        # If skip = 1, ignore step 1 and watch steps 2,3,4.
        play_steps = [step for step in [1, 2, 3, 4] if step > skip]

        return TheorySignal(
            theory="Ping Pong",
            trigger_odd=latest_odd,
            trigger_index=trigger_index,
            play_steps=play_steps,
            reason=f"{rule['label']} detected at {latest_odd:.2f}. Skip={skip}.",
            weight=self.weights["ping_pong"],
        )

    # -----------------------------
    # Signal management
    # -----------------------------

    def add_signal_if_new(self, signal: Optional[TheorySignal]):
        if signal is None:
            return

        # Avoid adding the exact same signal again.
        for existing in self.active_signals:
            if (
                existing.theory == signal.theory
                and existing.trigger_index == signal.trigger_index
                and abs(existing.trigger_odd - signal.trigger_odd) < 0.0001
            ):
                return

        self.active_signals.append(signal)

    def remove_expired_signals(self):
        """
        Remove signals after their maximum play step has passed.
        """
        current_next_index = len(self.odds_history)

        still_active = []

        for signal in self.active_signals:
            next_step = current_next_index - signal.trigger_index
            max_step = max(signal.play_steps)

            if next_step <= max_step:
                still_active.append(signal)

        self.active_signals = still_active

    def get_signals_for_next_round(self) -> List[Dict]:
        """
        Find active signals that point to the next round.

        If latest history index is N-1, next round index is N.
        Step after trigger = N - trigger_index.
        """
        next_round_signals = []
        current_next_index = len(self.odds_history)

        for signal in self.active_signals:
            next_step = current_next_index - signal.trigger_index

            if next_step in signal.play_steps:
                next_round_signals.append({
                    "theory": signal.theory,
                    "trigger_odd": signal.trigger_odd,
                    "step": next_step,
                    "reason": signal.reason,
                    "weight": signal.weight,
                })

        return next_round_signals

    def build_prediction(self) -> Dict:
        """
        Combine active theory signals into one prediction result.
        """
        market = self.analyze_market()
        next_round_signals = self.get_signals_for_next_round()

        base_score = sum(signal["weight"] for signal in next_round_signals)

        # Market adjustment
        if market["state"] == "stable":
            market_bonus = 15
        elif market["state"] == "transition":
            market_bonus = 5
        elif market["state"] == "unstable":
            market_bonus = -20
        else:
            market_bonus = 0

        # Multiple theories agreeing should increase confidence.
        agreement_bonus = 0
        if len(next_round_signals) >= 2:
            agreement_bonus = 15

        if len(next_round_signals) >= 3:
            agreement_bonus = 30

        score = base_score + market_bonus + agreement_bonus
        score = max(0, min(100, score))

        if score >= 75:
            level = "HIGH"
        elif score >= 50:
            level = "MEDIUM"
        elif score >= 30:
            level = "LOW"
        else:
            level = "NONE"

        should_alert = score >= 50

        return {
            "should_alert": should_alert,
            "confidence_level": level,
            "score": score,
            "market": market,
            "signals": next_round_signals,
            "history_size": len(self.odds_history),
        }

    def add_round(self, odd: float) -> Dict:
        """
        Call this once after every completed round.

        Returns prediction for the NEXT round.
        """
        if odd is None:
            return self.build_prediction()

        odd = float(odd)

        self.odds_history.append(odd)

        if len(self.odds_history) > self.max_history_size:
            self.odds_history = self.odds_history[-self.max_history_size:]

        # Add new theory signals created by this latest odd.
        self.add_signal_if_new(self.detect_purple_train_signal())
        self.add_signal_if_new(self.detect_double_digits_signal())
        self.add_signal_if_new(self.detect_ping_pong_signal())

        # Remove old expired signals.
        self.remove_expired_signals()

        # Return combined prediction for the next round.
        return self.build_prediction()

    def format_prediction_message(self, prediction: Dict) -> str:
        """
        Make a readable log message.
        """
        market = prediction["market"]
        signals = prediction["signals"]

        if not signals:
            return (
                f"Prediction: no pink signal | "
                f"market={market['state']} | "
                f"score={prediction['score']}"
            )

        signal_parts = []

        for signal in signals:
            signal_parts.append(
                f"{signal['theory']} step {signal['step']} "
                f"from {signal['trigger_odd']:.2f}x"
            )

        return (
            f"PINK SIGNAL: {prediction['confidence_level']} | "
            f"score={prediction['score']} | "
            f"market={market['state']} | "
            f"signals={'; '.join(signal_parts)}"
        )


class AviatorRoundMonitor:
    """Monitor the Aviator game cashout status and detect round ends."""

    def __init__(
        self,
        url: str = "https://www.betpawa.co.tz/casino/game/aviator",
        headless: bool = True,
        browser: str = "auto",
        login_url: str = "https://www.betpawa.co.tz/login",
        phone: Optional[str] = None,
        password: Optional[str] = None,
        cashout_selector: str = "span.cashout-value",
        login_button_selectors: Optional[str] = None,
        check_interval: float = 0.5,
        min_zero_stable_checks: int = 2,
        wait_timeout: int = 45,
        max_iframe_depth: int = 6,
    ):
        self.url = url
        self.login_url = login_url
        self.phone = phone
        self.password = password
        self.cashout_selectors = self._split_selectors(cashout_selector)
        self.phone_candidates = self._build_phone_candidates(phone)
        self.login_button_selectors = self._split_selectors(
            login_button_selectors
            or (
                'button._button_1h7bd_1._primary_1h7bd_62._lg_1h7bd_47._square_1h7bd_54._fullWidth_1h7bd_144,'
                'button[class*="_button_1h7bd_1"][class*="_primary_1h7bd_62"],'
                'button[type="submit"]'
            )
        )

        self.last_value: Optional[float] = None
        self.round_over_triggered = False
        self.zero_stable_checks = 0
        self.min_zero_stable_checks = max(1, int(min_zero_stable_checks))
        self.check_interval = max(0.1, float(check_interval))
        self.wait_timeout = int(wait_timeout)
        self.max_iframe_depth = int(max_iframe_depth)
        self.zero_threshold = 0.000001
        self.payout_selector = ".payout.ng-star-inserted"
        self.round_history = []
        self.last_payout_signature = None
        self.round_number = 0
        self.prediction_engine = AviatorPredictionEngine()

        self.browser_type = browser
        self.driver = None
        self.last_cashout_location = None
        self.cashout_read_failures = 0
        self._one_time_logs = set()

        self.phone_selectors = [
            'input[data-test-id="loginFormPhoneNumberInput"]',
            'input#phoneNumber',
            'input[type="tel"]',
            'input[type="text"][name*="phone" i]',
            'input[type="tel"][name*="phone" i]',
            'input[name*="msisdn" i]',
            'input[name*="mobile" i]',
            'input[name*="phone" i]',
            'input[placeholder*="phone" i]',
            'input[placeholder*="number" i]',
            'input[id*="phone" i]',
            'input[type="text"]',
        ]

        self.password_selectors = [
            'input[data-test-id="loginFormPasswordInput"]',
            'input[type="password"]',
            'input[name*="password" i]',
            'input[placeholder*="password" i]',
            'input[id*="password" i]',
        ]

        self.login_submit_selectors = [
            'button[data-test-id="logInButton"]',
            'button[type="submit"]',
        ]

        self.driver = self._create_driver(headless)
        if not self.driver:
            raise RuntimeError("Failed to initialize a browser driver")

    @staticmethod
    def _split_selectors(selectors: str):
        parts = [part.strip() for part in str(selectors).split(",") if part.strip()]
        return parts or ["span.cashout-value"]

    @staticmethod
    def _build_phone_candidates(phone: Optional[str]):
        if not phone:
            return []

        raw = str(phone).strip()
        digits = re.sub(r"\D", "", raw)

        candidates = []
        for candidate in [raw, digits]:
            if candidate and candidate not in candidates:
                candidates.append(candidate)

        if digits.startswith("0") and len(digits) > 1:
            stripped = digits[1:]
            if stripped not in candidates:
                candidates.append(stripped)

        return candidates

    def log_event(self, message: str):
        """Log an event with timestamp."""
        timestamp = local_now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        print(f"[{timestamp}] {message}")
        sys.stdout.flush()

    def log_once(self, key: str, message: str):
        """Avoid printing the same noisy warning repeatedly."""
        if key not in self._one_time_logs:
            self._one_time_logs.add(key)
            self.log_event(message)

    def _create_driver(self, headless: bool):
        """Create a webdriver, trying Chrome first, then Firefox."""
        if self.browser_type in {"auto", "chrome"}:
            try:
                return self._create_chrome_driver(headless)
            except Exception as error:
                if self.browser_type == "chrome":
                    raise
                self.log_event(f"[WARN] Chrome driver failed: {error}")

        if self.browser_type in {"auto", "firefox"}:
            try:
                return self._create_firefox_driver(headless)
            except Exception as error:
                if self.browser_type == "firefox":
                    raise
                self.log_event(f"[WARN] Firefox driver failed: {error}")

        return None

    def _create_chrome_driver(self, headless: bool):
        """Create Chrome webdriver."""
        options = webdriver.ChromeOptions()

        if headless:
            options.add_argument("--headless=new")

        options.add_argument("--window-size=1365,768")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument(
            "user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome Safari/537.36"
        )

        chromedriver_path = shutil.which("chromedriver")
        if chromedriver_path:
            service = ChromeService(chromedriver_path)
            driver = webdriver.Chrome(service=service, options=options)
        else:
            driver = webdriver.Chrome(options=options)

        driver.set_window_size(1365, 768)
        return driver

    def _create_firefox_driver(self, headless: bool):
        """Create Firefox webdriver."""
        options = webdriver.FirefoxOptions()

        if headless:
            options.add_argument("--headless")

        options.set_preference(
            "general.useragent.override",
            "Mozilla/5.0 (X11; Linux x86_64) Gecko Firefox",
        )

        geckodriver_path = shutil.which("geckodriver")
        if geckodriver_path:
            service = FirefoxService(geckodriver_path)
            driver = webdriver.Firefox(service=service, options=options)
        else:
            driver = webdriver.Firefox(options=options)

        driver.set_window_size(1365, 768)
        return driver

    def _wait_for_document_ready(self, timeout: int = 15):
        """Wait until the top-level page is at least interactive."""
        try:
            WebDriverWait(self.driver, timeout).until(
                lambda driver: driver.execute_script("return document.readyState")
                in {"interactive", "complete"}
            )
        except Exception:
            pass

    def _find_first_matching_input(self, selectors):
        """Return the first visible, enabled input that matches any selector."""
        for selector in selectors:
            try:
                elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
            except Exception:
                continue

            for element in elements:
                try:
                    if element.is_displayed() and element.is_enabled():
                        return element
                except Exception:
                    continue

        return None

    def _has_visible_password_input(self) -> bool:
        try:
            self.driver.switch_to.default_content()
        except Exception:
            pass
        return self._find_first_matching_input(self.password_selectors) is not None

    def _click_login_link_if_present(self) -> bool:
        """
        Try to click a visible login link/button.

        This intentionally does NOT click "join now", because that may open registration
        instead of login.
        """
        try:
            self.driver.switch_to.default_content()
            candidates = self.driver.find_elements(By.XPATH, "//a|//button")
        except Exception:
            return False

        for element in candidates:
            try:
                text = " ".join((element.text or "").strip().lower().split())
                if not text:
                    continue

                is_login_control = (
                    text in {"login", "log in", "sign in", "ingia"}
                    or "log in" in text
                    or "sign in" in text
                )

                if is_login_control and element.is_displayed() and element.is_enabled():
                    element.click()
                    time.sleep(2)
                    return True
            except Exception:
                continue

        return False

    def _submit_login_via_enter(self, element) -> bool:
        try:
            from selenium.webdriver.common.keys import Keys

            element.send_keys(Keys.ENTER)
            time.sleep(1.5)
            return True
        except Exception:
            return False

    def _fill_login_inputs(self, phone_value: str, password_value: str) -> bool:
        phone_input = self._find_first_matching_input(self.phone_selectors)
        password_input = self._find_first_matching_input(self.password_selectors)

        if phone_input is None or password_input is None:
            return False

        try:
            phone_input.clear()
            phone_input.send_keys(phone_value)
            phone_input.send_keys("\ue004")  # TAB
            password_input.clear()
            password_input.send_keys(password_value)
            password_input.send_keys("\ue004")  # TAB
            return True
        except Exception:
            try:
                self.driver.execute_script(
                    """
                    const phone = arguments[0];
                    const password = arguments[1];
                    const setValue = (el, value) => {
                      if (!el) return false;
                      const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                      nativeInputValueSetter.call(el, value);
                      el.dispatchEvent(new Event('input', { bubbles: true }));
                      el.dispatchEvent(new Event('change', { bubbles: true }));
                      return true;
                    };
                    return [setValue(phone, arguments[2]), setValue(password, arguments[3])];
                    """,
                    phone_input,
                    password_input,
                    phone_value,
                    password_value,
                )
                return True
            except Exception:
                return False

    def _click_login_button_if_present(self) -> bool:
        try:
            self.driver.switch_to.default_content()
        except Exception:
            pass

        for selector in self.login_button_selectors:
            try:
                buttons = self.driver.find_elements(By.CSS_SELECTOR, selector)
            except Exception:
                continue

            for button in buttons:
                try:
                    if button.is_displayed() and button.is_enabled():
                        button.click()
                        time.sleep(1.5)
                        return True
                except Exception:
                    continue

        return False

    def _submit_login_form(self) -> bool:
        try:
            self.driver.switch_to.default_content()
        except Exception:
            pass

        for selector in self.login_submit_selectors:
            try:
                buttons = self.driver.find_elements(By.CSS_SELECTOR, selector)
            except Exception:
                continue

            for button in buttons:
                try:
                    if button.is_displayed() and button.is_enabled():
                        button.click()
                        time.sleep(1.5)
                        return True
                except Exception:
                    continue

        try:
            forms = self.driver.find_elements(By.CSS_SELECTOR, "form")
        except Exception:
            forms = []

        for form in forms:
            try:
                form.submit()
                time.sleep(1.5)
                return True
            except Exception:
                continue

        return False

    def _page_body_text(self) -> str:
        try:
            self.driver.switch_to.default_content()
            return self.driver.find_element(By.TAG_NAME, "body").text.strip()
        except Exception:
            return ""

    def _looks_logged_out(self) -> bool:
        body_text = self._page_body_text().lower()
        logged_out_markers = [
            "great choice! join now or log in to play",
            "join now or log in to play",
            "log in to play",
            "login to play",
            "please log in",
            "please login",
        ]
        return any(marker in body_text for marker in logged_out_markers)

    def _login_confirmed(self) -> bool:
        """
        Best-effort login confirmation.

        A successful login usually means:
        - the browser is no longer on a login form,
        - the logged-out message is gone,
        - and there is no visible password input.
        """
        try:
            self.driver.switch_to.default_content()
        except Exception:
            pass

        body_text = self._page_body_text().lower()
        current_url = ""
        try:
            current_url = self.driver.current_url.lower()
        except Exception:
            pass

        failure_markers = [
            "invalid",
            "incorrect",
            "wrong password",
            "wrong phone",
            "try again",
            "required",
            "failed",
            "error",
        ]

        if any(marker in body_text for marker in failure_markers):
            return False

        if self._looks_logged_out():
            return False

        has_password_input = self._has_visible_password_input()

        if ("login" not in current_url) and not has_password_input:
            return True

        if ("casino" in current_url or "aviator" in current_url) and not has_password_input:
            return True

        return False

    def _save_debug_artifacts(self, label: str):
        """Save screenshot and top-level HTML for troubleshooting."""
        if not self.driver:
            return

        try:
            self.driver.switch_to.default_content()
        except Exception:
            pass

        try:
            safe_label = re.sub(r"[^a-zA-Z0-9_-]+", "_", label).strip("_") or "debug"
            timestamp = local_now().strftime("%Y%m%d_%H%M%S")
            base_name = f"{safe_label}_{timestamp}"
            screenshot_path = os.path.join(os.getcwd(), f"{base_name}.png")
            html_path = os.path.join(os.getcwd(), f"{base_name}.html")

            self.driver.save_screenshot(screenshot_path)

            with open(html_path, "w", encoding="utf-8") as handle:
                handle.write(self.driver.page_source)

            self.log_event(f"[DEBUG] Screenshot saved: {screenshot_path}")
            self.log_event(f"[DEBUG] HTML saved: {html_path}")
        except Exception as error:
            self.log_event(f"[WARN] Failed to save debug artifacts: {error}")

    def debug_page_state(self):
        """Log helpful diagnostics when the cashout element cannot be found."""
        if not self.driver:
            return

        try:
            self.driver.switch_to.default_content()
        except Exception:
            pass

        try:
            title = self.driver.title
        except Exception:
            title = "<unknown>"

        try:
            current_url = self.driver.current_url
        except Exception:
            current_url = "<unknown>"

        self.log_event(f"[DEBUG] Page title: {title}")
        self.log_event(f"[DEBUG] Current URL: {current_url}")

        try:
            iframes = self.driver.find_elements(By.TAG_NAME, "iframe")
            self.log_event(f"[DEBUG] Top-level iframe count: {len(iframes)}")

            for index, iframe in enumerate(iframes[:10], start=1):
                try:
                    src = (iframe.get_attribute("src") or "")[:180]
                    title_attr = (iframe.get_attribute("title") or "")[:80]
                    self.log_event(
                        f"[DEBUG] iframe #{index}: title={title_attr!r}, src={src!r}"
                    )
                except Exception:
                    continue
        except Exception:
            self.log_event("[DEBUG] Could not count/list iframes")

        try:
            body_text = self.driver.find_element(By.TAG_NAME, "body").text.strip()
            if body_text:
                preview = body_text[:700].replace("\n", " | ")
                self.log_event(f"[DEBUG] Body preview: {preview}")
        except Exception:
            pass

    def login(self) -> bool:
        """Log in if credentials were supplied."""
        if not self.phone_candidates or not self.password:
            self.log_event("No login credentials supplied. Skipping login step.")
            return True

        self.log_event(f"Opening login page: {self.login_url}")
        self.driver.get(self.login_url)
        self._wait_for_document_ready()
        time.sleep(2)

        # Only click a login link if a login form is not already visible.
        if not self._has_visible_password_input():
            self._click_login_link_if_present()
            self._wait_for_document_ready()
            time.sleep(1)

        submit_words = {
            "login",
            "log in",
            "sign in",
            "submit",
            "continue",
            "ingia",
        }

        for index, phone_value in enumerate(self.phone_candidates, start=1):
            self.log_event(f"Trying login phone variant #{index}: {phone_value}")

            if not self._fill_login_inputs(phone_value, self.password):
                self.log_event("[ERROR] Could not find login inputs automatically.")
                self.debug_page_state()
                self._save_debug_artifacts("login_inputs_not_found")
                return False

            submitted = False

            # Try pressing Enter immediately after typing the password.
            try:
                password_input = self._find_first_matching_input(self.password_selectors)
                if password_input is not None:
                    submitted = self._submit_login_via_enter(password_input)
                    if submitted:
                        self.log_event("[OK] Login submitted with Enter key.")
            except Exception as error:
                self.log_event(f"[WARN] Enter-key submit failed: {error}")

            if not submitted:
                try:
                    submit_buttons = self.driver.find_elements(
                        By.XPATH, "//button|//input[@type='submit']"
                    )
                except Exception:
                    submit_buttons = []

                for button in submit_buttons:
                    try:
                        label = button.text or button.get_attribute("value") or ""
                        label = " ".join(label.strip().lower().split())

                        if not label:
                            continue

                        label_matches = label in submit_words or any(
                            word in label for word in submit_words
                        )

                        if label_matches and button.is_displayed() and button.is_enabled():
                            button.click()
                            submitted = True
                            self.log_event("[OK] Login submitted using button click.")
                            break
                    except Exception:
                        continue

            if not submitted:
                submitted = self._click_login_button_if_present() or self._submit_login_form()
                if submitted:
                    self.log_event("[OK] Login submitted using selector.")

            if not submitted:
                self.log_event("[ERROR] Could not submit login form.")
                continue

            self.log_event("Waiting for login confirmation...")
            try:
                WebDriverWait(self.driver, 20).until(lambda driver: self._login_confirmed())
                self.log_event("[OK] Login appears to be successful.")
                return True
            except TimeoutException:
                self.log_event("[WARN] Login could not be confirmed with this phone variant.")
                self.debug_page_state()
                self._save_debug_artifacts(f"login_not_confirmed_{index}")

        self.log_event("[ERROR] Login failed after trying all phone variants.")
        return False

    def parse_cashout(self, text: str) -> Optional[float]:
        """
        Convert cashout text to a float.

        Returns None if the text is unreadable. This is important: unreadable text
        must not be treated as 0.0, because that creates false round-over detections.
        """
        if text is None:
            return None

        cleaned = str(text).replace(",", "").strip()
        if not cleaned:
            return None

        match = re.search(r"\d+(?:\.\d+)?", cleaned)
        if not match:
            return None

        try:
            value = float(match.group(0))
        except (TypeError, ValueError):
            return None

        if value < 0:
            return None

        return value

    def _element_text(self, element) -> str:
        """Read useful text from a Selenium element."""
        possible_values = [
            element.text,
            element.get_attribute("textContent"),
            element.get_attribute("innerText"),
            element.get_attribute("aria-label"),
            element.get_attribute("value"),
        ]

        for value in possible_values:
            if value is None:
                continue
            value = str(value).strip()
            if value:
                return value

        return ""

    def parse_payout_odd(self, text):
        """
        Convert payout text like '1.45x' or '2.10' to float.

        Returns:
            float, for example 1.45
            None if unreadable
        """
        if text is None:
            return None

        text = str(text).replace(",", "").strip()

        if not text:
            return None

        match = re.search(r"\d+(?:\.\d+)?", text)

        if not match:
            return None

        try:
            return float(match.group(0))
        except ValueError:
            return None

    def _read_payout_texts_in_current_context(self):
        """Read all payout texts from the current document/frame."""
        payout_texts = []

        try:
            elements = self.driver.find_elements(By.CSS_SELECTOR, self.payout_selector)
        except Exception:
            return payout_texts

        for element in elements:
            try:
                text = self._element_text(element)
                if text:
                    payout_texts.append(text)
            except Exception:
                continue

        return payout_texts

    def _find_payout_texts_recursive(self, depth=0, max_depth=None):
        """
        Search the main page and nested iframes for payout values.

        Returns:
            list of payout texts, for example ['1.25x', '3.40x']
        """
        if max_depth is None:
            max_depth = self.max_iframe_depth

        payout_texts = self._read_payout_texts_in_current_context()

        if payout_texts:
            return payout_texts

        if depth >= max_depth:
            return []

        try:
            iframe_count = len(self.driver.find_elements(By.TAG_NAME, "iframe"))
        except Exception:
            return []

        for index in range(iframe_count):
            switched = False

            try:
                frames = self.driver.find_elements(By.TAG_NAME, "iframe")

                if index >= len(frames):
                    continue

                self.driver.switch_to.frame(frames[index])
                switched = True

                payout_texts = self._find_payout_texts_recursive(
                    depth=depth + 1,
                    max_depth=max_depth,
                )

                if payout_texts:
                    return payout_texts

            except Exception:
                continue
            finally:
                if switched:
                    try:
                        self.driver.switch_to.parent_frame()
                    except Exception:
                        try:
                            self.driver.switch_to.default_content()
                        except Exception:
                            pass

        return []

    def get_payout_snapshot(self):
        """
        Get the current payout/odds list.

        Returns a dict with payout texts, parsed odds, and a signature.
        """
        try:
            self.driver.switch_to.default_content()
            payout_texts = self._find_payout_texts_recursive()
        except Exception as error:
            self.log_event(f"[WARN] Error reading payout values: {error}")
            return {"texts": [], "odds": [], "signature": ""}
        finally:
            try:
                self.driver.switch_to.default_content()
            except Exception:
                pass

        odds = []
        for text in payout_texts:
            value = self.parse_payout_odd(text)
            if value is not None:
                odds.append(value)

        signature = "|".join(payout_texts[:10])

        return {"texts": payout_texts, "odds": odds, "signature": signature}

    def get_latest_payout_odd(self):
        """Return the latest payout odd and the full snapshot."""
        snapshot = self.get_payout_snapshot()
        odds = snapshot["odds"]

        if not odds:
            return None, snapshot

        latest_odd = odds[0]
        return latest_odd, snapshot

    def record_round_result(self):
        """
        Wait for the payout/odds list to update, then store the latest odd.

        This should be called when cashout has dropped to zero.
        """
        snapshot = None
        latest_odd = None

        for _ in range(12):
            latest_odd, snapshot = self.get_latest_payout_odd()

            if snapshot["signature"] and snapshot["signature"] != self.last_payout_signature:
                break

            time.sleep(0.25)

        if snapshot is None:
            snapshot = {"texts": [], "odds": [], "signature": ""}

        self.round_number += 1

        round_data = {
            "round": self.round_number,
            "time": local_now().strftime("%Y-%m-%d %H:%M:%S"),
            "final_cashout": 0.0,
            "payout_odd": latest_odd,
            "raw_payouts": snapshot["texts"],
        }

        self.round_history.append(round_data)
        self.last_payout_signature = snapshot["signature"]

        if latest_odd is None:
            self.log_event(f"Round #{self.round_number}: payout/odds not found")
        else:
            self.log_event(f"Round #{self.round_number}: payout/odds = {latest_odd:.2f}x")

            prediction = self.prediction_engine.add_round(latest_odd)
            message = self.prediction_engine.format_prediction_message(prediction)
            self.log_event(message)

            if prediction["should_alert"]:
                self.log_event(">>> NEXT ROUND WARNING: theory combination says watch for PINK.")

    def _read_cashout_text_in_current_context(self) -> Optional[Tuple[str, str]]:
        """
        Try to read cashout text in the current frame.

        Returns:
            (text, selector) or None
        """
        # Prefer visible elements, but allow non-visible fallback because some sites
        # render values in elements that Selenium considers not displayed.
        for prefer_visible in (True, False):
            for selector in self.cashout_selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                except Exception:
                    continue

                for element in elements:
                    try:
                        if prefer_visible and not element.is_displayed():
                            continue

                        text = self._element_text(element)
                        if text:
                            return text, selector
                    except StaleElementReferenceException:
                        continue
                    except Exception:
                        continue

        return None

    def _find_cashout_text_recursive(
        self,
        depth: int = 0,
        path: str = "main document",
    ) -> Optional[Tuple[str, str, str]]:
        """
        Find and read cashout text in the current document or nested iframes.

        Returns:
            (text, selector, location_path) or None
        """
        direct_result = self._read_cashout_text_in_current_context()
        if direct_result:
            text, selector = direct_result
            return text, selector, path

        if depth >= self.max_iframe_depth:
            return None

        try:
            frame_count = len(self.driver.find_elements(By.TAG_NAME, "iframe"))
        except Exception:
            return None

        for index in range(frame_count):
            switched = False

            try:
                # Refetch frames each time because the DOM may update quickly.
                frames = self.driver.find_elements(By.TAG_NAME, "iframe")
                if index >= len(frames):
                    continue

                self.driver.switch_to.frame(frames[index])
                switched = True

                result = self._find_cashout_text_recursive(
                    depth=depth + 1,
                    path=f"{path} > iframe[{index + 1}]",
                )

                if result:
                    return result

            except (StaleElementReferenceException, NoSuchElementException):
                continue
            except WebDriverException as error:
                self.log_once(
                    f"iframe-error-{depth}-{index}-{type(error).__name__}",
                    f"[WARN] Could not inspect iframe at depth {depth}, index {index + 1}: {error}",
                )
                continue
            finally:
                if switched:
                    try:
                        self.driver.switch_to.parent_frame()
                    except Exception:
                        try:
                            self.driver.switch_to.default_content()
                        except Exception:
                            pass

        return None

    def get_current_cashout(self) -> Optional[float]:
        """Get the current cashout value from the page or nested iframes."""
        try:
            self.driver.switch_to.default_content()
            result = self._find_cashout_text_recursive()
        except Exception as error:
            self.log_once(
                f"cashout-read-error-{type(error).__name__}",
                f"[WARN] Error while reading cashout: {error}",
            )
            return None
        finally:
            try:
                self.driver.switch_to.default_content()
            except Exception:
                pass

        if not result:
            return None

        raw_text, selector, location = result
        value = self.parse_cashout(raw_text)

        if value is None:
            self.log_once(
                f"parse-error-{raw_text[:80]}",
                f"[WARN] Cashout text found but could not parse it: {raw_text!r}",
            )
            return None

        location_key = f"{location} using selector {selector!r}"
        if location_key != self.last_cashout_location:
            self.last_cashout_location = location_key
            self.log_event(
                f"[OK] Cashout found at {location_key}; raw text={raw_text!r}"
            )

        return value

    def wait_for_cashout(self, timeout: Optional[int] = None) -> bool:
        """Load the Aviator page and wait until a readable cashout value exists."""
        timeout = self.wait_timeout if timeout is None else int(timeout)

        self.log_event(f"Loading page: {self.url}")
        self.driver.get(self.url)
        self._wait_for_document_ready()
        time.sleep(2)

        if self._looks_logged_out():
            self.log_event("[WARN] Aviator page appears to be logged out.")
            self.debug_page_state()

        self.log_event(f"Waiting for readable cashout value, max {timeout}s...")
        try:
            WebDriverWait(self.driver, timeout).until(
                lambda driver: self.get_current_cashout() is not None
            )
            self.log_event("[OK] Cashout value found. Starting monitoring.")

            try:
                snapshot = self.get_payout_snapshot()
                self.last_payout_signature = snapshot["signature"]
            except Exception:
                pass

            return True
        except TimeoutException:
            self.log_event(f"[ERROR] Cashout value not found after {timeout}s.")
            self.debug_page_state()
            self._save_debug_artifacts("cashout_not_found")
            return False

    def mark_round_over(self):
        """Handle round-over event."""
        self.log_event("[OK] ROUND IS OVER - cashout dropped to 0")
        self.record_round_result()
        self.round_over_triggered = True

    def check_cashout(self) -> bool:
        """Check the current cashout and detect transitions."""
        current_value = self.get_current_cashout()

        if current_value is None:
            self.cashout_read_failures += 1
            if self.cashout_read_failures == 1 or self.cashout_read_failures % 20 == 0:
                self.log_event("[WARN] Cashout value not readable. Retrying...")
            return False

        self.cashout_read_failures = 0

        value_changed = self.last_value is None or current_value != self.last_value
        if value_changed:
            self.log_event(f"Cashout: {current_value:.2f}")

        is_zero = current_value <= self.zero_threshold
        is_positive = current_value > self.zero_threshold

        if is_positive:
            # A positive value means a round is active or a new round started.
            self.round_over_triggered = False
            self.zero_stable_checks = 0

        elif is_zero and not self.round_over_triggered:
            # This is the fixed logic:
            # Keep counting repeated zero reads even when current_value == last_value.
            # The old code returned early when the value did not change, so the counter
            # could never reach 2.
            saw_positive_before = (
                self.last_value is not None and self.last_value > self.zero_threshold
            )
            already_counting_zero = self.zero_stable_checks > 0

            if saw_positive_before or already_counting_zero:
                self.zero_stable_checks += 1

                if self.zero_stable_checks >= self.min_zero_stable_checks:
                    self.mark_round_over()
                    self.zero_stable_checks = 0

        self.last_value = current_value
        return True

    def start_monitoring(self, duration: Optional[int] = None) -> bool:
        """
        Start monitoring the cashout value.

        Args:
            duration: Monitor for this many seconds. None means infinite.
        """
        try:
            if self.phone and self.password:
                if not self.login():
                    self.log_event("[ERROR] Login failed. Stopping monitor.")
                    return False

            if not self.wait_for_cashout():
                return False

            start_time = time.time()
            self.log_event("Starting cashout monitor. Press Ctrl+C to stop.")

            while True:
                if duration is not None and (time.time() - start_time) >= duration:
                    self.log_event(f"Monitoring duration ({duration}s) reached. Stopping.")
                    return True

                self.check_cashout()
                time.sleep(self.check_interval)

        except KeyboardInterrupt:
            self.log_event("Monitor stopped by user.")
            return True

        finally:
            self.stop()

    def stop(self):
        """Stop monitoring and close the browser."""
        if self.driver is None:
            return

        self.log_event("Closing browser...")
        try:
            self.driver.quit()
        except Exception as error:
            self.log_event(f"[WARN] Could not close browser cleanly: {error}")
        finally:
            self.driver = None


def main():
    parser = argparse.ArgumentParser(
        description="Monitor Betpawa Aviator cashout and detect when rounds end."
    )

    parser.add_argument(
        "--url",
        default="https://www.betpawa.co.tz/casino/game/aviator",
        help="Aviator game URL to monitor",
    )
    parser.add_argument(
        "--login-url",
        default="https://www.betpawa.co.tz/login",
        help="Login page URL",
    )
    parser.add_argument(
        "--display",
        action="store_true",
        help="Show the browser window instead of using headless mode",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=None,
        help="Monitor for N seconds. Default: run forever",
    )
    parser.add_argument(
        "--browser",
        choices=["auto", "chrome", "firefox"],
        default="auto",
        help="Browser to use. Default: auto",
    )
    parser.add_argument(
        "--phone",
        default=os.environ.get("BETPAWA_PHONE"),
        help="Login phone/number. You can also set BETPAWA_PHONE.",
    )
    parser.add_argument(
        "--password",
        default=os.environ.get("BETPAWA_PASSWORD"),
        help="Login password. You can also set BETPAWA_PASSWORD.",
    )
    parser.add_argument(
        "--selector",
        default="span.cashout-value",
        help=(
            "CSS selector for the cashout value. "
            "For multiple selectors, separate them with commas."
        ),
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.5,
        help="Seconds between checks. Default: 0.5",
    )
    parser.add_argument(
        "--zero-checks",
        type=int,
        default=2,
        help="How many consecutive zero reads confirm round over. Default: 2",
    )
    parser.add_argument(
        "--wait-timeout",
        type=int,
        default=45,
        help="Seconds to wait for the cashout value. Default: 45",
    )
    parser.add_argument(
        "--max-iframe-depth",
        type=int,
        default=6,
        help="How deep to search nested iframes. Default: 6",
    )
    args = parser.parse_args()

    try:
        monitor = AviatorRoundMonitor(
            url=args.url,
            headless=not args.display,
            browser=args.browser,
            login_url=args.login_url,
            phone=args.phone,
            password=args.password,
            cashout_selector=args.selector,
            check_interval=args.interval,
            min_zero_stable_checks=args.zero_checks,
            wait_timeout=args.wait_timeout,
            max_iframe_depth=args.max_iframe_depth,
        )

        success = monitor.start_monitoring(duration=args.duration)
        if not success:
            sys.exit(1)

    except Exception as error:
        print(f"Error: {error}")
        print()
        print("Troubleshooting:")
        print("  1. Run once with --display so you can see what the browser is doing.")
        print("  2. Make sure ChromeDriver or GeckoDriver is installed and on PATH.")
        print("  3. Try a specific browser: --browser chrome or --browser firefox.")
        print("  4. If cashout is not found, open the saved debug HTML/screenshot.")
        print("  5. If the site shows OTP/captcha/security checks, complete them manually or stop automation.")
        sys.exit(1)


if __name__ == "__main__":
    main()
