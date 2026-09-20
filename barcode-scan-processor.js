(function () {
    function normalizeBarcode(value) {
        return String(value || '').trim();
    }

    function findInProducts(products, code) {
        const normalized = normalizeBarcode(code);
        if (!normalized) return null;

        for (const item of products || []) {
            if ((item.barcode || '').trim() === normalized) {
                return { product: item, variant: null, type: 'product' };
            }

            if (Array.isArray(item.variants)) {
                const variant = item.variants.find(v => (v.barcode || '').trim() === normalized);
                if (variant) {
                    return { product: item, variant, type: 'variant' };
                }
            }
        }

        return null;
    }

    class BarcodeScanProcessor {
        constructor(options = {}) {
            this.products = options.products || (() => []);
            this.onMatch = options.onMatch || function () {};
            this.onNoMatch = options.onNoMatch || function () {};
            this.onStatus = options.onStatus || function () {};
            this.intervalMs = options.intervalMs || 300;
            this.lastCode = null;
            this.lastProcessedAt = 0;
            this.running = false;
            this.timer = null;
            this.videoEl = null;
            this.detector = null;
        }

        ensureDetector() {
            if (!('BarcodeDetector' in window)) return null;
            if (!this.detector) {
                this.detector = new BarcodeDetector({
                    formats: ['code_128', 'code_39', 'ean_13', 'ean_8', 'upc_a', 'upc_e', 'qr_code']
                });
            }
            return this.detector;
        }

        evaluateCode(code) {
            const normalized = normalizeBarcode(code);
            if (!normalized) return false;

            const now = Date.now();
            if (this.lastCode === normalized && now - this.lastProcessedAt < 1200) {
                return false;
            }

            this.lastCode = normalized;
            this.lastProcessedAt = now;

            const found = findInProducts(this.products(), normalized);
            if (!found) {
                this.onNoMatch(normalized);
                return false;
            }

            const product = found.product;
            const variant = found.variant;
            const payload = {
                id: product.id,
                variantId: variant ? variant.id : null,
                name: variant ? (variant.fullName || product.name) : product.name,
                sku: product.sku || 'SKU',
                price: variant ? Number(variant.price || 0) : Number(product.price || 0),
                unit: variant ? (variant.unit || product.unit || 'ชิ้น') : (product.unit || 'ชิ้น')
            };

            if (navigator.vibrate) {
                navigator.vibrate([80, 60, 120]);
            }

            this.onMatch(payload);
            return true;
        }

        async tick() {
            if (!this.running || !this.videoEl) return;

            try {
                const detector = this.ensureDetector();
                if (detector && this.videoEl.readyState >= 2) {
                    const codes = await detector.detect(this.videoEl);
                    if (codes && codes.length) {
                        const detected = normalizeBarcode(codes[0].rawValue);
                        if (detected) {
                            const handled = this.evaluateCode(detected);
                            if (handled) {
                                this.onStatus('กำลังยืนยันสินค้า...');
                                return;
                            }
                        }
                    }
                }
            } catch (_) {
                // Ignore temporary detection errors and continue loop.
            }

            this.timer = setTimeout(() => this.tick(), this.intervalMs);
        }

        start(videoEl) {
            this.stop();
            this.videoEl = videoEl;
            this.running = true;
            this.onStatus('กำลังสแกน...');
            this.timer = setTimeout(() => this.tick(), 250);
        }

        stop() {
            this.running = false;
            if (this.timer) {
                clearTimeout(this.timer);
                this.timer = null;
            }
        }
    }

    window.BarcodeScanProcessor = BarcodeScanProcessor;
})();
