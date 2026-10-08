# NanaphanHub Printer Bridge
# Windows + Gainscha GA-E200 on USB002
# Receipt: Windows GDI driver (Thai-safe) + cash drawer: ESC/POS RAW
# Requires: pywin32

import json
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import win32con
import win32print
import win32ui

HOST = "127.0.0.1"
PORT = 8765
PRINTER_NAME = "GA-E200"
EXPECTED_PORT = "USB002"

ESC = b"\x1b"


def printer_info():
    h = win32print.OpenPrinter(PRINTER_NAME)
    try:
        return win32print.GetPrinter(h, 2)
    finally:
        win32print.ClosePrinter(h)


def check_printer():
    try:
        info = printer_info()
        port = str(info.get("pPortName", ""))
        return True, port, info
    except Exception as e:
        return False, "", {"error": str(e)}


def raw_print(data: bytes, job_name: str):
    """Send a genuine RAW ESC/POS job to the Windows printer queue."""
    h = win32print.OpenPrinter(PRINTER_NAME)
    try:
        win32print.StartDocPrinter(h, 1, (job_name, None, "RAW"))
        try:
            win32print.StartPagePrinter(h)
            written = win32print.WritePrinter(h, data)
            win32print.EndPagePrinter(h)
            return written
        finally:
            win32print.EndDocPrinter(h)
    finally:
        win32print.ClosePrinter(h)


def drawer_kick():
    # ESC p m t1 t2. Pin 0, 50 ms ON, 100 ms OFF.
    # ESC/POS standard: m=0 is drawer kick pin 2.
    return ESC + b"p" + bytes([0, 25, 50])


def require_printer():
    ok, port, info = check_printer()
    if not ok:
        raise RuntimeError(f"ไม่พบเครื่องพิมพ์ {PRINTER_NAME}: {info.get('error', '')}")
    if EXPECTED_PORT and port.upper() != EXPECTED_PORT.upper():
        raise RuntimeError(f"GA-E200 ใช้พอร์ต {port} ไม่ใช่ {EXPECTED_PORT}")
    return port


def money(v):
    return f"{float(v or 0):,.2f}"


def wrap_text_gdi(dc, text, max_width):
    """Wrap Unicode text using the actual Windows printer font metrics."""
    text = str(text or "")
    if not text:
        return [""]
    result = []
    current = ""
    for ch in text:
        candidate = current + ch
        width = dc.GetTextExtent(candidate)[0]
        if current and width > max_width:
            result.append(current)
            current = ch
        else:
            current = candidate
    if current:
        result.append(current)
    return result or [""]


def create_font(height, bold=False, face="Tahoma"):
    # Negative height = character height in logical units for a crisp printer font.
    return win32ui.CreateFont({
        "name": face,
        "height": -abs(int(height)),
        "width": 0,
        "weight": 700 if bold else 400,
        "italic": False,
        "underline": False,
        "strikeout": False,
        "charset": win32con.DEFAULT_CHARSET,
        "outprecision": win32con.OUT_DEFAULT_PRECIS,
        "clipprecision": win32con.CLIP_DEFAULT_PRECIS,
        "quality": win32con.PROOF_QUALITY,
        "pitchandfamily": win32con.DEFAULT_PITCH | win32con.FF_DONTCARE,
    })


def build_receipt_lines(payload):
    items = payload.get("items") or []
    bill_no = payload.get("billNo") or "-"
    subtotal = float(payload.get("subtotal") or 0)
    discount = float(payload.get("discount") or 0)
    tax = float(payload.get("tax") or 0)
    total = float(payload.get("total") or 0)
    received = float(payload.get("received") or 0)
    change = float(payload.get("change") or 0)

    rows = []
    rows.append(("center_bold", "ร้านนานาภัณฑ์ จ.ขอนแก่น"))
    rows.append(("center_bold", "ใบเสร็จรับเงิน"))
    rows.append(("normal", f"เลขที่: {bill_no}"))
    rows.append(("normal", datetime.now().strftime("วันที่ %d/%m/%Y %H:%M")))
    rows.append(("line", "-" * 58))

    for item in items:
        name = str(item.get("name") or "สินค้า")
        qty = float(item.get("qty") or 0)
        price = float(item.get("price") or 0)
        total_item = qty * price
        rows.append(("item", name))
        rows.append(("pair", f"  {qty:g} x {money(price)}", money(total_item)))

    rows.extend([
        ("line", "-" * 58),
        ("pair", "ยอดสินค้า", money(subtotal)),
        ("pair", "ส่วนลด", money(discount)),
        ("pair", "ภาษี", money(tax)),
        ("pair_bold", "ยอดสุทธิ", money(total)),
        ("pair", "รับเงิน", money(received)),
        ("pair_bold", "เงินทอน", money(change)),
        ("line", "-" * 58),
        ("center", "ขอบคุณที่ใช้บริการ"),
    ])
    return rows


def gdi_print_receipt(payload):
    """Print through the installed GA-E200 Windows driver.

    This intentionally does NOT send Thai as CP874 bytes. The E200 documentation
    lists GB18030/Big5/international character sets rather than Thai CP874, so
    Windows renders Unicode Thai with the installed printer driver instead.
    """
    port = require_printer()

    dc = win32ui.CreateDC()
    dc.CreatePrinterDC(PRINTER_NAME)

    try:
        # Printer pixel dimensions from the actual driver.
        dpi_x = dc.GetDeviceCaps(win32con.LOGPIXELSX) or 203
        dpi_y = dc.GetDeviceCaps(win32con.LOGPIXELSY) or 203
        printable_width = dc.GetDeviceCaps(win32con.HORZRES)
        if printable_width <= 0:
            printable_width = int(80 / 25.4 * dpi_x)

        margin = max(10, int(3 * dpi_x / 25.4))
        max_width = printable_width - margin * 2
        left = margin
        y = margin
        line_gap = max(4, int(1.2 * dpi_y / 25.4))
        normal_h = max(20, int(3.0 * dpi_y / 25.4))
        small_h = max(18, int(2.6 * dpi_y / 25.4))
        bold_h = max(24, int(3.4 * dpi_y / 25.4))

        font_normal = create_font(normal_h, False)
        font_small = create_font(small_h, False)
        font_bold = create_font(bold_h, True)

        dc.StartDoc("NanaphanHub POS Receipt")
        try:
            dc.StartPage()
            for kind, *values in build_receipt_lines(payload):
                if kind == "line":
                    font = font_small
                    text = values[0]
                    dc.SelectObject(font)
                    dc.TextOut(left, y, text)
                    y += small_h + line_gap
                    continue

                if kind == "pair" or kind == "pair_bold":
                    font = font_bold if kind == "pair_bold" else font_normal
                    left_text, right_text = values
                    dc.SelectObject(font)
                    dc.TextOut(left, y, left_text)
                    right_w = dc.GetTextExtent(right_text)[0]
                    dc.TextOut(max(left, printable_width - margin - right_w), y, right_text)
                    y += (bold_h if kind == "pair_bold" else normal_h) + line_gap
                    continue

                font = font_bold if kind in ("center_bold",) else font_normal
                dc.SelectObject(font)
                text = values[0]
                lines = wrap_text_gdi(dc, text, max_width)
                for line in lines:
                    text_w = dc.GetTextExtent(line)[0]
                    x = max(left, (printable_width - text_w) // 2) if kind.startswith("center") else left
                    dc.TextOut(x, y, line)
                    y += (bold_h if kind == "center_bold" else normal_h) + line_gap

                # Item names can wrap to multiple lines; pair rows remain compact.

            # Extra feed so the receipt clears the cutter.
            y += int(5 * dpi_y / 25.4)
            dc.EndPage()
        finally:
            dc.EndDoc()
    finally:
        dc.DeleteDC()

    return port


def open_drawer():
    port = require_printer()
    raw_print(ESC + b"@" + drawer_kick(), "NanaphanHub Open Cash Drawer")
    return port


def print_receipt(payload):
    port = gdi_print_receipt(payload)
    # Open only after the receipt has been handed to the Windows printer driver.
    raw_print(ESC + b"@" + drawer_kick(), "NanaphanHub Cash Drawer Kick")
    return port


class Handler(BaseHTTPRequestHandler):
    def _headers(self, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def _json(self, obj, status=200):
        self._headers(status)
        self.wfile.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def do_OPTIONS(self):
        self._headers(204)

    def do_GET(self):
        if self.path == "/status":
            ok, port, info = check_printer()
            ready = ok and port.upper() == EXPECTED_PORT.upper()
            self._json({
                "ok": ready,
                "printer": PRINTER_NAME,
                "port": port,
                "expectedPort": EXPECTED_PORT,
                "mode": "Windows GDI Unicode + ESC/POS drawer",
                "encoding": "Unicode via Windows driver",
                "message": "พร้อมใช้งาน" if ready else "ไม่พบเครื่องพิมพ์/พอร์ตไม่ตรง"
            }, 200 if ready else 503)
            return
        self._json({"ok": False, "message": "Not found"}, 404)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else b"{}"
            payload = json.loads(body.decode("utf-8"))

            if self.path == "/open-drawer":
                port = open_drawer()
                self._json({"ok": True, "port": port, "message": "เปิดลิ้นชักแล้ว"})
                return

            if self.path == "/print-receipt":
                port = print_receipt(payload)
                self._json({
                    "ok": True,
                    "port": port,
                    "message": "พิมพ์ใบเสร็จภาษาไทยและเปิดลิ้นชักแล้ว",
                    "mode": "Windows GDI Unicode + ESC/POS drawer"
                })
                return

            if self.path == "/test-print":
                port = gdi_print_receipt({
                    "billNo": "TEST",
                    "items": [{"name": "ทดสอบเครื่องพิมพ์ GA-E200", "qty": 1, "price": 123}],
                    "subtotal": 123,
                    "discount": 0,
                    "tax": 0,
                    "total": 123,
                    "received": 200,
                    "change": 77,
                })
                self._json({"ok": True, "port": port, "message": "ส่งใบพิมพ์ทดสอบแล้ว"})
                return

            self._json({"ok": False, "message": "Not found"}, 404)
        except Exception as e:
            self._json({"ok": False, "message": str(e)}, 500)


if __name__ == "__main__":
    print("=" * 64)
    print("NanaphanHub Printer Bridge - WINDOWS GDI + ESC/POS DRAWER")
    print(f"Printer : {PRINTER_NAME}")
    print(f"Port    : {EXPECTED_PORT}")
    print(f"Server  : http://{HOST}:{PORT}")
    print("Receipt : Windows driver Unicode (Thai-safe)")
    print("Drawer  : ESC/POS RAW kick after printing")
    print("=" * 64)
    ok, port, info = check_printer()
    print(f"Printer check: {'OK' if ok else 'FAILED'} | Port={port or '-'}")
    if not ok:
        print("ERROR:", info.get("error", "unknown"))

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        server.server_close()
