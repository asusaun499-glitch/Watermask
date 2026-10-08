# NanaphanHub Printer Bridge - Windows GDI TEXT + ESC/POS DRAWER
# GA-E200 / GA-E200 Series / USB002
# Requires: pywin32

import json
import subprocess
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import win32con
import win32print
import win32ui

HOST = "127.0.0.1"
PORT = 8765
EXPECTED_PORT = "USB002"
CONFIGURED_PRINTER = "GA-E200"  # fallback only

ESC = b"\x1b"


def powershell_printers():
    """Return [{name, port}] from Windows Print Management/WMI."""
    script = (
        "Get-CimInstance Win32_Printer | "
        "Select-Object Name,PortName,Default | "
        "ConvertTo-Json -Compress"
    )
    try:
        p = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True, text=True, timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if p.returncode != 0 or not p.stdout.strip():
            return []
        data = json.loads(p.stdout)
        if isinstance(data, dict):
            data = [data]
        return [
            {"name": str(x.get("Name") or ""), "port": str(x.get("PortName") or ""), "default": bool(x.get("Default"))}
            for x in data
        ]
    except Exception:
        return []


def win32_printers():
    rows = []
    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    for level in (2, 5):
        try:
            for p in (win32print.EnumPrinters(flags, None, level) or []):
                if level == 2:
                    rows.append({
                        "name": str(p.get("pPrinterName") or ""),
                        "port": str(p.get("pPortName") or ""),
                        "default": False,
                    })
                else:
                    # Level 5 can expose pPrinterName more reliably for local queues.
                    name = str(p[2]) if len(p) > 2 else ""
                    rows.append({"name": name, "port": "", "default": False})
        except Exception:
            pass
    return rows


def find_printer():
    # 1) WMI/Print Management gives exact Windows queue name + PortName.
    printers = powershell_printers()
    target = EXPECTED_PORT.upper()
    for p in printers:
        if p["port"].upper().strip() == target and p["name"]:
            return p["name"], p["port"], "WMI"

    # 2) pywin32 enumeration.
    printers = win32_printers()
    for p in printers:
        if p["port"].upper().strip() == target and p["name"]:
            return p["name"], p["port"], "EnumPrinters"

    # 3) Name fallback, but only if OpenPrinter actually accepts it.
    for candidate in [CONFIGURED_PRINTER, "GA-E200 Series", "Gainscha GA-E200", "Gainscha GA-E200 Series"]:
        try:
            h = win32print.OpenPrinter(candidate)
            win32print.ClosePrinter(h)
            return candidate, EXPECTED_PORT, "name-fallback"
        except Exception:
            continue

    return None, None, None


def printer_status():
    name, port, source = find_printer()
    if not name:
        return False, "", "", "ไม่พบ Windows printer queue ที่ใช้พอร์ต USB002"
    return True, name, port, f"พร้อมใช้งาน ({source})"


def raw_print(data: bytes, job_name: str):
    ok, name, port, msg = printer_status()
    if not ok:
        raise RuntimeError(msg)
    h = win32print.OpenPrinter(name)
    try:
        job = win32print.StartDocPrinter(h, 1, (job_name, None, "RAW"))
        try:
            win32print.StartPagePrinter(h)
            win32print.WritePrinter(h, data)
            win32print.EndPagePrinter(h)
        finally:
            win32print.EndDocPrinter(h)
        return job, name, port
    finally:
        win32print.ClosePrinter(h)


def drawer_kick():
    # Standard ESC/POS drawer pulse: pin 0, 50ms ON, 500ms OFF.
    return ESC + b"p" + bytes([0, 25, 250])


def money(v):
    return f"{float(v or 0):,.2f}"


def make_lines(payload):
    items = payload.get("items") or []
    subtotal = float(payload.get("subtotal") or 0)
    discount = float(payload.get("discount") or 0)
    tax = float(payload.get("tax") or 0)
    total = float(payload.get("total") or 0)
    received = float(payload.get("received") or 0)
    change = float(payload.get("change") or 0)
    bill_no = str(payload.get("billNo") or "-")
    lines = [
        "ร้านนานาภัณฑ์ จ.ขอนแก่น",
        "ใบเสร็จรับเงิน",
        f"เลขที่: {bill_no}",
        datetime.now().strftime("วันที่ %d/%m/%Y %H:%M"),
        "------------------------------------------",
    ]
    for item in items:
        name = str(item.get("name") or "สินค้า")
        qty = float(item.get("qty") or 0)
        price = float(item.get("price") or 0)
        lines.append(name)
        lines.append(f"  {qty:g} x {money(price)} = {money(qty*price)}")
    lines += [
        "------------------------------------------",
        f"ยอดสินค้า     {money(subtotal)}",
        f"ส่วนลด        -{money(discount)}",
        f"ภาษี           {money(tax)}",
        f"ยอดสุทธิ       {money(total)}",
        f"รับเงิน         {money(received)}",
        f"เงินทอน        {money(change)}",
        "------------------------------------------",
        "ขอบคุณที่ใช้บริการ",
    ]
    return lines


def gdi_print_text(payload, test=False):
    ok, printer, port, msg = printer_status()
    if not ok:
        raise RuntimeError(msg)

    hdc = win32ui.CreateDC()
    hdc.CreatePrinterDC(printer)
    hdc.StartDoc("NanaphanHub POS Receipt")
    hdc.StartPage()
    try:
        # 24pt-ish bold header, then normal body. Windows driver handles Thai Unicode.
        font = win32ui.CreateFont({
            "name": "Leelawadee UI",
            "height": 36,
            "weight": win32con.FW_BOLD,
        })
        body_font = win32ui.CreateFont({
            "name": "Leelawadee UI",
            "height": 28,
            "weight": win32con.FW_NORMAL,
        })
        hdc.SelectObject(font)
        y = 30
        hdc.TextOut(30, y, "NanaphanHub TEST" if test else "ร้านนานาภัณฑ์ จ.ขอนแก่น")
        y += 50
        hdc.SelectObject(body_font)
        lines = ["GA-E200 / USB002", "ทดสอบการพิมพ์"] if test else make_lines(payload)
        for line in lines:
            hdc.TextOut(30, y, str(line))
            y += 34
            # Avoid running off a single page.
            if y > 3200:
                break
        hdc.EndPage()
        hdc.EndDoc()
    except Exception:
        try:
            hdc.AbortDoc()
        except Exception:
            pass
        raise
    finally:
        hdc.DeleteDC()
    return printer, port


class Handler(BaseHTTPRequestHandler):
    def headers(self, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def json(self, obj, status=200):
        self.headers(status)
        self.wfile.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def do_OPTIONS(self):
        self.headers(204)

    def do_GET(self):
        if self.path == "/status":
            ok, name, port, msg = printer_status()
            self.json({"ok": ok, "printer": name, "port": port, "expectedPort": EXPECTED_PORT, "message": msg}, 200 if ok else 503)
        else:
            self.json({"ok": False, "message": "Not found"}, 404)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            if self.path == "/test-print":
                printer, port = gdi_print_text({}, test=True)
                self.json({"ok": True, "printer": printer, "port": port, "message": "ส่งงานทดสอบผ่าน Windows GDI แล้ว"})
                return
            if self.path == "/open-drawer":
                job, printer, port = raw_print(drawer_kick(), "NanaphanHub Cash Drawer")
                self.json({"ok": True, "printer": printer, "port": port, "job": job, "message": "เปิดลิ้นชักแล้ว"})
                return
            if self.path == "/print-receipt":
                printer, port = gdi_print_text(payload, test=False)
                # Drawer only after successful receipt print job.
                job, _, _ = raw_print(drawer_kick(), "NanaphanHub Cash Drawer")
                self.json({"ok": True, "printer": printer, "port": port, "drawerJob": job, "message": "พิมพ์ใบเสร็จและเปิดลิ้นชักแล้ว"})
                return
            self.json({"ok": False, "message": "Not found"}, 404)
        except Exception as e:
            self.json({"ok": False, "message": str(e)}, 500)

    def log_message(self, fmt, *args):
        print("[PrinterBridge] " + fmt % args)


if __name__ == "__main__":
    ok, name, port, msg = printer_status()
    print("=" * 68)
    print("NanaphanHub Printer Bridge - WINDOWS GDI TEXT + ESC/POS DRAWER")
    print(f"Printer : {name or 'AUTO-DETECT'}")
    print(f"Port    : {port or 'NOT FOUND'} (target {EXPECTED_PORT})")
    print(f"Server  : http://{HOST}:{PORT}")
    print("Receipt : Windows printer driver (Thai Unicode)")
    print("Drawer  : ESC/POS RAW kick after printing")
    print("=" * 68)
    if ok:
        print(f"Printer check: OK | {name} | Port={port}")
        print("พร้อมใช้งาน")
    else:
        print("Printer check: FAILED")
        print("ERROR:", msg)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
