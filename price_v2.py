"""
Amazon Price Tracker (PyQt5 + Selenium) 

A small desktop app that:
- Takes an Amazon product URL
- Opens the page with Selenium
- Extracts product title + price with BeautifulSoup
- Optionally alerts when the price is below a target value
- Saves results to a CSV file

Notes:
- This is a learning project. The UI may freeze briefly while fetching (no threading).
- Amazon may show bot checks/captcha; in that case price parsing can fail.
"""

import sys
import os
import csv
import re
import time
from datetime import datetime

from PyQt5.QtWidgets import (
    QApplication,
    QWidget,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QSpinBox,
    QTextEdit,
    QMessageBox,
    QGroupBox,
)

from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.common.exceptions import WebDriverException, TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from webdriver_manager.chrome import ChromeDriverManager


# ----------------------------
# Small helper functions
# ----------------------------
def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_data_dir():
    # Create a local folder for CSV logs if it doesn't exist
    os.makedirs("data", exist_ok=True)


def append_history_csv(url: str, title: str, price: float, raw_price: str):
    # Append a new row into data/price_history.csv
    ensure_data_dir()
    path = os.path.join("data", "price_history.csv")
    file_exists = os.path.exists(path)

    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not file_exists:
            w.writerow(["timestamp", "url", "title", "price", "raw_price"])
        w.writerow([now_str(), url, title, price, raw_price])


def normalize_price_to_float(text: str):
    """
    A simple price parser that tries to convert strings like:
    - "1.234,56 TL"
    - "1,234.56"
    into float.

    This is heuristic-based and may fail on some formats.
    """
    if not text:
        return None

    cleaned = re.sub(r"[^\d\.,]", "", text).strip()
    if not cleaned:
        return None

    # If both separators exist, decide the decimal separator by the last occurrence
    if "." in cleaned and "," in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            # decimal is ','  thousands is '.'
            cleaned = cleaned.replace(".", "")
            cleaned = cleaned.replace(",", ".")
        else:
            # decimal is '.'  thousands is ','
            cleaned = cleaned.replace(",", "")
    else:
        # Only comma exists: often decimal in TR/EU style
        if "," in cleaned:
            cleaned = cleaned.replace(".", "")
            cleaned = cleaned.replace(",", ".")
        # Only dot exists: assume it can be decimal; keep as-is

    try:
        return float(cleaned)
    except ValueError:
        return None


# ----------------------------
# Selenium fetch 
# ----------------------------
def fetch_amazon_price(url: str, timeout_sec: int = 20, headless: bool = True):
    """
    Fetch title and price from an Amazon product page.

    Returns a dict with:
    - url, title, price, price_text, currency_hint
    Raises an Exception if parsing fails.
    """
    options = webdriver.ChromeOptions()
    options.add_argument("--window-size=1200,900")

    # Headless mode runs Chrome without showing a window
    if headless:
        options.add_argument("--headless=new")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)

    try:
        driver.get(url)

        # Wait for the page body to exist
        WebDriverWait(driver, timeout_sec).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )

        # Small delay for dynamic elements
        time.sleep(1.2)

        soup = BeautifulSoup(driver.page_source, "lxml")

        # Try common title selectors
        title = ""
        title_el = soup.select_one("#productTitle") or soup.select_one("h1#title")
        if title_el:
            title = title_el.get_text(" ", strip=True)

        # Try common Amazon price selectors
        price_text = None
        candidates = [
            "#priceblock_ourprice",
            "#priceblock_dealprice",
            "#priceblock_saleprice",
            "span.a-price span.a-offscreen",
            "#corePriceDisplay_desktop_feature_div span.a-price span.a-offscreen",
            "#corePrice_feature_div span.a-price span.a-offscreen",
        ]

        for sel in candidates:
            el = soup.select_one(sel)
            if el:
                t = el.get_text(" ", strip=True)
                if t:
                    price_text = t
                    break

        if not price_text:
            raise RuntimeError(
                "Price element not found. The page layout may be different or blocked by Amazon."
            )

        currency_hint = re.sub(r"[\d\.,\s]", "", price_text).strip()
        price_val = normalize_price_to_float(price_text)

        if price_val is None:
            raise RuntimeError(
                "Price could not be parsed. The content may be blocked or the format is unexpected."
            )

        return {
            "url": url,
            "title": title,
            "price": price_val,
            "price_text": price_text,
            "currency_hint": currency_hint,
        }

    finally:
        try:
            driver.quit()
        except Exception:
            pass


# ----------------------------
# GUI
# ----------------------------
class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Amazon Price Tracker")
        self.setMinimumWidth(760)

        self.last_result = None

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText(
            "Paste an Amazon product URL (e.g. https://www.amazon.com.tr/dp/...)"
        )

        self.target_price = QSpinBox()
        self.target_price.setRange(0, 10_000_000)
        self.target_price.setValue(0)
        self.target_price.setSuffix(" target price")

        self.timeout_input = QSpinBox()
        self.timeout_input.setRange(5, 120)
        self.timeout_input.setValue(20)
        self.timeout_input.setSuffix(" sec timeout")

        self.headless_btn = QPushButton("Headless: ON")
        self.headless_btn.setCheckable(True)
        self.headless_btn.setChecked(True)
        self.headless_btn.clicked.connect(self.toggle_headless)

        self.fetch_btn = QPushButton("Fetch Price")
        self.fetch_btn.clicked.connect(self.fetch_price)

        self.save_btn = QPushButton("Save to CSV")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self.save_history)

        self.title_lbl = QLabel("Product: —")
        self.price_lbl = QLabel("Price: —")
        self.price_lbl.setStyleSheet("font-size: 16px; font-weight: 600;")

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)

        # Layout
        top = QGroupBox("Input")
        top_l = QVBoxLayout()

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("URL:"))
        row1.addWidget(self.url_input)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Settings:"))
        row2.addWidget(self.target_price)
        row2.addWidget(self.timeout_input)
        row2.addWidget(self.headless_btn)
        row2.addStretch()
        row2.addWidget(self.fetch_btn)
        row2.addWidget(self.save_btn)

        top_l.addLayout(row1)
        top_l.addLayout(row2)
        top.setLayout(top_l)

        out = QGroupBox("Output")
        out_l = QVBoxLayout()
        out_l.addWidget(self.title_lbl)
        out_l.addWidget(self.price_lbl)
        out_l.addWidget(self.log_box)
        out.setLayout(out_l)

        main_l = QVBoxLayout()
        main_l.addWidget(top)
        main_l.addWidget(out)
        self.setLayout(main_l)

    def toggle_headless(self):
        self.headless_btn.setText("Headless: ON" if self.headless_btn.isChecked() else "Headless: OFF")

    def log(self, msg: str):
        self.log_box.append(msg)

    def fetch_price(self):
        url = self.url_input.text().strip()
        if not url or "amazon" not in url.lower():
            QMessageBox.warning(self, "Invalid URL", "Please enter a valid Amazon product URL.")
            return

        # Reset UI state
        self.fetch_btn.setEnabled(False)
        self.save_btn.setEnabled(False)
        self.title_lbl.setText("Product: —")
        self.price_lbl.setText("Price: —")
        self.last_result = None

        timeout = int(self.timeout_input.value())
        headless = bool(self.headless_btn.isChecked())

        self.log(f"[{now_str()}] Fetching page... (UI may freeze briefly)")

        try:
            data = fetch_amazon_price(url=url, timeout_sec=timeout, headless=headless)

            title = data.get("title") or "(title not found)"
            price = data.get("price")
            price_text = data.get("price_text") or ""
            currency = data.get("currency_hint") or ""

            self.last_result = data

            self.title_lbl.setText(f"Product: {title}")
            self.price_lbl.setText(f"Price: {price}  |  (raw: {price_text})")
            self.log(f"[{now_str()}] OK — parsed price: {price} {currency}".strip())

            self.save_btn.setEnabled(True)

            # Target price alert
            target = int(self.target_price.value())
            if target > 0 and price is not None and price <= target:
                QMessageBox.information(
                    self,
                    "Target Reached!",
                    f"Price is below your target.\n\nTarget: {target}\nCurrent: {price} {currency}".strip(),
                )

        except TimeoutException:
            self.log(f"[{now_str()}] ERROR — Timeout while loading the page.")
            QMessageBox.critical(self, "Error", "Timeout: page is slow or blocked.")
        except WebDriverException as e:
            self.log(f"[{now_str()}] ERROR — WebDriver issue: {e}")
            QMessageBox.critical(self, "Error", f"Selenium/WebDriver error:\n{e}")
        except Exception as e:
            self.log(f"[{now_str()}] ERROR — {e}")
            QMessageBox.critical(self, "Error", str(e))
        finally:
            self.fetch_btn.setEnabled(True)

    def save_history(self):
        if not self.last_result:
            return

        url = self.last_result["url"]
        title = self.last_result.get("title", "")
        price = self.last_result.get("price", "")
        raw_price = self.last_result.get("price_text", "")

        append_history_csv(url, title, price, raw_price)
        self.log(f"[{now_str()}] Saved to data/price_history.csv")
        QMessageBox.information(self, "Saved", "Saved to CSV (data/price_history.csv).")


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
