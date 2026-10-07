# NanaphanHub Printer Bridge
# Windows + Gainscha GA-E200 on USB002
# Requires: pywin32, Pillow

import json
import os
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import win32print
from PIL import Image, ImageDraw, ImageFont

HOST = "127.0.0.1"
PORT = 8765
PRINTER_NAME = "GA-E200"
EXPECTED_PORT = "USB002"
PAPER_DOTS = 576  # 80mm class printer at ~203dpi

ESC = b"\x1b"
GS = b"\x1d"


def printer_info():
    h = win32print.OpenPrinter(PRINTER_NAME)
    try:
        info = win32print.GetPrinter(h, 2)
        return info
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
    # ESC/POS cash drawer kick: ESC p m t1 t2
    # Pin 0, 25ms ON, 250ms OFF.
    return ESC + b"p" + bytes([0, 25, 250])


def find_font(bold=False, size=28):
    candidates = []
    if bold:
        candidates += [
            r"C:\\Windows\\Fonts\\LeelaUIb.ttf",
            r"C:\\Windows\\Fonts\\LeelawUI-Bold.ttf",
            r"C:\\Windows\\Fonts\\tahomabd.ttf",
        ]
    candidates += [
        r"C:\\Windows\\Fonts\\LeelawUI.ttf",
        r"C:\\Windows\\Fonts\\tahoma.ttf",
        r"C:\\Windows\\Fonts\\arial.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                pass
    return ImageFont.load_default()


def text_width(draw, text, font):
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def wrap_text(draw, text, font, max_width):
    text = str(text or "")
    if not text:
        return [""]
    lines = []
    current = ""
    for ch in text:
        candidate = current + ch
        if current and text_width(draw, candidate, font) > max_width:
            lines.append(current)
            current = ch
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def money(v):
    return f"{float(v or 0):,.2f}"


def build_receipt_image(payload):
    items = payload.get("items") or []
    bill_no = payload.get("billNo") or "-"
    subtotal = float(payload.get("subtotal") or 0)
    discount = float(payload.get("discount") or 0)
    tax = float(payload.get("tax") or 0)
    total = float(payload.get("total") or 0)
    received = float(payload.get("received") or 0)
    change = float(payload.get("change") or 0)

    title_font = find_font(True, 32)
    head_font = find_font(True, 24)
    body_font = find_font(False, 22)
    small_font = find_font(False, 19)

    dummy = Image.new("1", (PAPER_DOTS, 100), 1)
    d = ImageDraw.Draw(dummy)

    rows = []
    rows.append(("title", "NanaphanHub POS"))
    rows.append(("center", "ใบเสร็จรับเงิน"))
    rows.append(("small", f"เลขที่: {bill_no}"))
    rows.append(("small", datetime.now().strftime("วันที่ %d/%m/%Y %H:%M")))
    rows.append(("line", "-" * 48))

    for item in items:
        name = str(item.get("name") or "สินค้า")
        qty = float(item.get("qty") or 0)
        price = float(item.get("price") or 0)
        total_item = qty * price
        name_lines = wrap_text(d, name, body_font, PAPER_DOTS - 20)
        rows.append(("item", name_lines[0]))
        for extra in name_lines[1:]:
            rows.append(("item", "  " + extra))
        rows.append(("pair", f"  {qty:g} x {money(price)}", money(total_item)))

    rows += [
        ("line", "-" * 48),
        ("pair", "ยอดสินค้า", money(subtotal)),
        ("pair", "ส่วนลด", money(discount)),
        ("pair", "ภาษี", money(tax)),
        ("pair_bold", "ยอดสุทธิ", money(total)),
        ("pair", "รับเงิน", money(received)),
        ("pair_bold", "เงินทอน", money(change)),
        ("line", "-" * 48),
        ("center", "ขอบคุณที่ใช้บริการ"),
    ]

    line_height = 30
    small_height = 26
    height = 35
    for kind, *vals in rows:
        height += small_height if kind == "small" else line_height
    height += 25

    img = Image.new("1", (PAPER_DOTS, height), 1)
    draw = ImageDraw.Draw(img)
    y = 10
    margin = 10

    for row in rows:
        kind = row[0]
        if kind == "line":
            draw.text((margin, y), row[1], font=small_font, fill=0)
            y += small_height
        elif kind == "title":
            text = row[1]
            x = (PAPER_DOTS - text_width(draw, text, title_font)) // 2
            draw.text((x, y), text, font=title_font, fill=0)
            y += 38
        elif kind == "center":
            text = row[1]
            x = (PAPER_DOTS - text_width(draw, text, head_font)) // 2
            draw.text((x, y), text, font=head_font, fill=0)
            y += 34
        elif kind == "small":
            draw.text((margin, y), row[1], font=small_font, fill=0)
            y += small_height
        elif kind == "item":
            draw.text((margin, y), row[1], font=body_font, fill=0)
            y += 29
        elif kind in ("pair", "pair_bold"):
            font = head_font if kind == "pair_bold" else body_font
            left, right = row[1], row[2]
            draw.text((margin, y), left, font=font, fill=0)
            rw = text_width(draw, right, font)
            draw.text((PAPER_DOTS - margin - rw, y), right, font=font, fill=0)
            y += 32

    return img.crop((0, 0, PAPER_DOTS, y + 10))


def image_to_escpos(img: Image.Image):
    img = img.convert("L")
    # 1-bit threshold; ESC/POS raster expects horizontal bytes.
    bw = img.point(lambda p: 0 if p < 160 else 255, "1")
    width, height = bw.size
    if width % 8:
        padded = Image.new("1", (width + (8 - width % 8), height), 1)
        padded.paste(bw, (0, 0))
        bw = padded
        width = bw.size[0]

    pixels = bw.load()
    data = bytearray()
    for y in range(height):
        for x in range(0, width, 8):
            byte = 0
            for bit in range(8):
                if pixels[x + bit, y] == 0:
                    byte |= 1 << (7 - bit)
            data.append(byte)

    x_bytes = width // 8
    header = GS + b"v0" + bytes([0]) + bytes([x_bytes & 0xFF, (x_bytes >> 8) & 0xFF, height & 0xFF, (height >> 8) & 0xFF])
    return header + bytes(data)


def require_printer():
    ok, port, info = check_printer()
    if not ok:
        raise RuntimeError(f"ไม่พบเครื่องพิมพ์ {PRINTER_NAME}: {port or info.get('error', '')}")
    if EXPECTED_PORT and port.upper() != EXPECTED_PORT.upper():
        raise RuntimeError(f"GA-E200 ใช้พอร์ต {port} ไม่ใช่ {EXPECTED_PORT}")
    return port


def open_drawer():
    port = require_printer()
    # Drawer-only job: does not print a receipt.
    raw_print(ESC + b"@" + drawer_kick(), "NanaphanHub Open Cash Drawer")
    return port


def print_receipt(payload):
    port = require_printer()
    image = build_receipt_image(payload)
    raw = bytearray()
    raw += ESC + b"@"
    raw += image_to_escpos(image)
    raw += b"\n\n\n"
    raw += GS + b"V" + bytes([1])  # cut if supported by printer firmware
    raw_print(bytes(raw), "NanaphanHub POS Receipt")
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
            self._json({
                "ok": ok and port.upper() == EXPECTED_PORT.upper(),
                "printer": PRINTER_NAME,
                "port": port,
                "expectedPort": EXPECTED_PORT,
                "message": "พร้อมใช้งาน" if ok else "ไม่พบเครื่องพิมพ์"
            }, 200 if ok else 503)
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
                self._json({"ok": True, "port": port, "message": "พิมพ์ใบเสร็จแล้ว"})
                return

            self._json({"ok": False, "message": "Not found"}, 404)
        except Exception as e:
            self._json({"ok": False, "message": str(e)}, 500)

    def log_message(self, fmt, *args):
        print("[PrinterBridge] " + fmt % args)


if __name__ == "__main__":
    ok, port, info = check_printer()
    print("=" * 60)
    print("NanaphanHub Printer Bridge")
    print(f"Printer : {PRINTER_NAME}")
    print(f"Port    : {port or 'NOT FOUND'} (expected {EXPECTED_PORT})")
    print(f"Server  : http://{HOST}:{PORT}")
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
