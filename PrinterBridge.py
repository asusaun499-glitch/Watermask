# NanaphanHub Printer Bridge
# Windows + Gainscha GA-E200 on USB002
# Text-mode ESC/POS receipt + automatic cash drawer
# Requires: pywin32

import json
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import win32print

HOST = "127.0.0.1"
PORT = 8765
PRINTER_NAME = "GA-E200 Series"
EXPECTED_PORT = "USB002"

ESC = b"\x1b"
GS = b"\x1d"

# Epson/ESC-POS Thai code page. GA-E200 firmware/driver variants commonly
# map ESC t 255 to CP874 (Thai). ASCII remains compatible as usual.
THAI_CODEPAGE = 255
TEXT_ENCODING = "cp874"
LINE_WIDTH = 48


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
    h = win32print.OpenPrinter(PRINTER_NAME)
    try:
        win32print.StartDocPrinter(h, 1, (job_name, None, "RAW"))
        try:
            win32print.StartPagePrinter(h)
            win32print.WritePrinter(h, data)
            win32print.EndPagePrinter(h)
        finally:
            win32print.EndDocPrinter(h)
    finally:
        win32print.ClosePrinter(h)


def drawer_kick():
    # ESC p m t1 t2: pin 0, 25ms ON, 250ms OFF.
    return ESC + b"p" + bytes([0, 25, 250])


def esc_text(text=""):
    """Encode Thai/ASCII text for the printer, ending with CR/LF."""
    value = str(text if text is not None else "")
    # cp874 handles Thai + ASCII. Characters outside the printer's code page
    # are replaced instead of crashing the sale.
    return value.encode(TEXT_ENCODING, errors="replace") + b"\r\n"


def text_width(value):
    """Approximate printable character width for 48-column 80mm receipts."""
    return len(str(value))


def wrap_text(text, width=LINE_WIDTH):
    text = str(text or "")
    if not text:
        return [""]
    result = []
    while len(text) > width:
        # Prefer breaking at a space when possible.
        cut = text.rfind(" ", 0, width + 1)
        if cut <= 0:
            cut = width
        result.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    result.append(text)
    return result


def money(value):
    return f"{float(value or 0):,.2f}"


def centered(text, width=LINE_WIDTH):
    text = str(text)
    if len(text) >= width:
        return text[:width]
    return text.center(width)


def pair_line(left, right, width=LINE_WIDTH):
    left = str(left)
    right = str(right)
    if len(right) >= width:
        return right[-width:]
    available = width - len(right) - 1
    return left[:max(0, available)] + " " * max(1, available - min(len(left), available) + 1) + right


def build_text_receipt(payload):
    items = payload.get("items") or []
    bill_no = payload.get("billNo") or "-"
    subtotal = float(payload.get("subtotal") or 0)
    discount = float(payload.get("discount") or 0)
    tax = float(payload.get("tax") or 0)
    total = float(payload.get("total") or 0)
    received = float(payload.get("received") or 0)
    change = float(payload.get("change") or 0)

    lines = []
    lines.append(centered("ร้านนานาภัณฑ์ จ.ขอนแก่น"))
    lines.append(centered("ใบเสร็จรับเงิน"))
    lines.append(f"เลขที่: {bill_no}")
    lines.append(datetime.now().strftime("วันที่ %d/%m/%Y %H:%M"))
    lines.append("-" * LINE_WIDTH)

    for item in items:
        name = str(item.get("name") or "สินค้า")
        qty = float(item.get("qty") or 0)
        price = float(item.get("price") or 0)
        total_item = qty * price

        for idx, part in enumerate(wrap_text(name)):
            lines.append(part if idx == 0 else "  " + part)
        lines.append(pair_line(f"  {qty:g} x {money(price)}", money(total_item)))

    lines.extend([
        "-" * LINE_WIDTH,
        pair_line("ยอดสินค้า", money(subtotal)),
        pair_line("ส่วนลด", money(discount)),
        pair_line("ภาษี", money(tax)),
        pair_line("ยอดสุทธิ", money(total)),
        pair_line("รับเงิน", money(received)),
        pair_line("เงินทอน", money(change)),
        "-" * LINE_WIDTH,
        centered("ขอบคุณที่ใช้บริการ"),
        "",
        "",
        "",
    ])
    return lines


def text_receipt_bytes(payload):
    # Initialize printer, select Thai code page, align left.
    raw = bytearray()
    raw += ESC + b"@"
    raw += ESC + b"t" + bytes([THAI_CODEPAGE])
    raw += ESC + b"a" + b"\x00"

    lines = build_text_receipt(payload)
    for index, line in enumerate(lines):
        # Bold the main totals and header without converting the receipt to an image.
        if index in (0, 1):
            raw += ESC + b"E" + b"\x01"
        elif "ยอดสุทธิ" in line or "เงินทอน" in line:
            raw += ESC + b"E" + b"\x01"
        else:
            raw += ESC + b"E" + b"\x00"
        raw += esc_text(line)

    raw += ESC + b"E" + b"\x00"
    # Feed a little paper, cut, then kick the cash drawer.
    raw += b"\n\n"
    raw += GS + b"V" + bytes([1])
    raw += drawer_kick()
    return bytes(raw)


def require_printer():
    ok, port, info = check_printer()
    if not ok:
        raise RuntimeError(f"ไม่พบเครื่องพิมพ์ {PRINTER_NAME}: {port or info.get('error', '')}")
    if EXPECTED_PORT and port.upper() != EXPECTED_PORT.upper():
        raise RuntimeError(f"GA-E200 ใช้พอร์ต {port} ไม่ใช่ {EXPECTED_PORT}")
    return port


def open_drawer():
    port = require_printer()
    raw_print(ESC + b"@" + drawer_kick(), "NanaphanHub Open Cash Drawer")
    return port


def print_receipt(payload):
    port = require_printer()
    raw = text_receipt_bytes(payload)
    raw_print(raw, "NanaphanHub POS Receipt")
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
                "mode": "ESC/POS TEXT",
                "encoding": TEXT_ENCODING,
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
                    "message": "พิมพ์ข้อความและเปิดลิ้นชักแล้ว",
                    "mode": "ESC/POS TEXT"
                })
                return

            self._json({"ok": False, "message": "Not found"}, 404)
        except Exception as e:
            self._json({"ok": False, "message": str(e)}, 500)

    def log_message(self, fmt, *args):
        print("[PrinterBridge] " + fmt % args)


if __name__ == "__main__":
    ok, port, info = check_printer()
    print("=" * 60)
    print("NanaphanHub Printer Bridge - ESC/POS TEXT")
    print(f"Printer : {PRINTER_NAME}")
    print(f"Port    : {port or 'NOT FOUND'} (expected {EXPECTED_PORT})")
    print(f"Server  : http://{HOST}:{PORT}")
    print(f"Encoding: {TEXT_ENCODING} / ESC t {THAI_CODEPAGE}")
    print("=" * 60)
    if not ok:
        print("WARNING: Windows ยังไม่พบ GA-E200")
    elif port.upper() != EXPECTED_PORT.upper():
        print("WARNING: พอร์ตไม่ตรงกับ USB002")
    else:
        print("พร้อมใช้งาน")

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nปิด Printer Bridge")
    finally:
        server.server_close()
