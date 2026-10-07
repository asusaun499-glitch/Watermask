NanaphanHub Printer Bridge

1. Install Python 3.x on Windows.
2. Install dependencies:
   python -m pip install pywin32 Pillow
3. Keep the GA-E200 installed as printer name "GA-E200" on port USB002.
4. Run:
   python PrinterBridge.py
5. Keep the black window open while using the POS website.

POS flow:
- Admin drawer button -> opens cash drawer only.
- Cash sale -> records sale -> opens drawer -> prints receipt.

Bridge:
http://127.0.0.1:8765/status
