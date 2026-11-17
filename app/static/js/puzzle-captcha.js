/* =========================================
 * 滑块拼图人机验证（原生 JS，不依赖 Vue）
 * 用法：
 *   const captcha = new PuzzleCaptcha();        // 可传 { images: [...] }
 *   const ok = await captcha.verify();          // 弹出验证，通过 resolve(true)，取消 resolve(false)
 * ========================================= */
(function (global) {
  const HANDLE_W = 42;
  const TOLERANCE = 5;
  const DEFAULT_IMAGES = [
    '/static/images/captcha/captcha1.jpg',
    '/static/images/captcha/captcha2.jpg',
    '/static/images/captcha/captcha3.jpg',
    '/static/images/captcha/captcha4.jpg',
    '/static/images/captcha/captcha5.jpg',
    '/static/images/captcha/captcha6.jpg',
  ];
  const imgCache = {};

  function loadImage(src) {
    if (imgCache[src]) return Promise.resolve(imgCache[src]);
    return new Promise((resolve) => {
      const img = new Image();
      img.onload = () => { imgCache[src] = img; resolve(img); };
      img.onerror = () => resolve(null);
      img.src = src;
    });
  }

  /** 拼图块轮廓：圆角方块 + 右侧半圆凸起 */
  function piecePath(ctx, x, y, size, knob) {
    const r = 5;
    const cx = x + size;
    const cy = y + size / 2;
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(cx, y);
    ctx.lineTo(cx, cy - knob);
    ctx.arc(cx, cy, knob, -Math.PI / 2, Math.PI / 2, false);
    ctx.lineTo(cx, y + size);
    ctx.lineTo(x + r, y + size);
    ctx.quadraticCurveTo(x, y + size, x, y + size - r);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.closePath();
  }

  /** 图片按 cover 铺满，随机放大 + 随机裁切 + 随机镜像 */
  function drawPhoto(ctx, img, W, H) {
    const zoom = 1 + Math.random() * 0.25;
    const scale = Math.max(W / img.width, H / img.height) * zoom;
    const sw = W / scale;
    const sh = H / scale;
    const sx = Math.random() * Math.max(0, img.width - sw);
    const sy = Math.random() * Math.max(0, img.height - sh);
    ctx.save();
    if (Math.random() < 0.5) {
      ctx.translate(W, 0);
      ctx.scale(-1, 1);
    }
    ctx.drawImage(img, sx, sy, sw, sh, 0, 0, W, H);
    ctx.restore();
  }

  function PuzzleCaptcha(options) {
    options = options || {};
    this.images = options.images && options.images.length ? options.images : DEFAULT_IMAGES;
    this.lastIndex = -1;
    this.mask = null;
    this.geo = null;
    this.state = 'idle';
    this.offset = 0;
    this.dragFrom = 0;
    this._resolve = null;
    this._onMove = this._handleMove.bind(this);
    this._onUp = this._handleUp.bind(this);
  }

  /** 弹出验证，返回 Promise<boolean> */
  PuzzleCaptcha.prototype.verify = function () {
    return new Promise((resolve) => {
      this._resolve = resolve;
      this._mount();
      this._init();
    });
  };

  PuzzleCaptcha.prototype._mount = function () {
    const mask = document.createElement('div');
    mask.className = 'pz-mask';
    mask.innerHTML = [
      '<div class="pz-card">',
      '  <div class="pz-head">',
      '    <div>',
      '      <h3>人机验证</h3>',
      '      <p>拖动滑块，把拼图块移到画面缺口处</p>',
      '    </div>',
      '    <button type="button" class="pz-close" aria-label="关闭验证">&times;</button>',
      '  </div>',
      '  <div class="pz-body">',
      '    <div class="pz-stage">',
      '      <canvas class="pz-bg"></canvas>',
      '      <canvas class="pz-piece"></canvas>',
      '      <div class="pz-loading">图片加载中…</div>',
      '      <button type="button" class="pz-refresh">换一张</button>',
      '    </div>',
      '    <div class="pz-bar">',
      '      <div class="pz-fill"></div>',
      '      <div class="pz-handle">›</div>',
      '      <span class="pz-text">按住滑块，拖动完成拼图</span>',
      '    </div>',
      '  </div>',
      '</div>',
    ].join('');

    document.body.appendChild(mask);
    this.mask = mask;
    this.stage = mask.querySelector('.pz-stage');
    this.bg = mask.querySelector('.pz-bg');
    this.piece = mask.querySelector('.pz-piece');
    this.bar = mask.querySelector('.pz-bar');
    this.fill = mask.querySelector('.pz-fill');
    this.handle = mask.querySelector('.pz-handle');
    this.text = mask.querySelector('.pz-text');
    this.loadingEl = mask.querySelector('.pz-loading');

    mask.addEventListener('mousedown', (e) => {
      if (e.target === mask) this._finish(false);
    });
    mask.querySelector('.pz-close').addEventListener('click', () => this._finish(false));
    mask.querySelector('.pz-refresh').addEventListener('click', () => this._init());
    this.handle.addEventListener('pointerdown', this._handleDown.bind(this));
  };

  PuzzleCaptcha.prototype._init = async function () {
    this.state = 'idle';
    this.offset = 0;
    this.geo = null;
    this.bar.className = 'pz-bar';
    this.handle.style.transform = 'translateX(0px)';
    this.fill.style.width = HANDLE_W + 'px';
    this.text.textContent = '图片加载中…';
    this.loadingEl.style.display = 'flex';

    // 随机挑图，避免连续两次一样
    let pick = Math.floor(Math.random() * this.images.length);
    if (this.images.length > 1 && pick === this.lastIndex) {
      pick = (pick + 1 + Math.floor(Math.random() * (this.images.length - 1))) % this.images.length;
    }
    this.lastIndex = pick;
    const img = await loadImage(this.images[pick]);
    if (!this.mask) return;   // 加载途中被关掉了

    const W = Math.round(this.stage.clientWidth) || 420;
    const H = Math.round(this.stage.clientHeight) || 150;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const size = 46;
    const knob = 10;
    const boxW = size + knob;
    const startX = 8;
    const y = 14 + Math.random() * Math.max(1, H - size - 28);
    const travel = Math.max(30, W - boxW - startX - 12);
    const minX = Math.min(startX + boxW + 12, startX + travel);
    const targetX = minX + Math.random() * Math.max(1, startX + travel - minX);

    this.bg.width = Math.round(W * dpr);
    this.bg.height = Math.round(H * dpr);
    this.bg.style.width = W + 'px';
    this.bg.style.height = H + 'px';
    const bctx = this.bg.getContext('2d');
    bctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (img) drawPhoto(bctx, img, W, H);
    else {   // 图片加载失败时退化为纯色渐变，保证功能可用
      const g = bctx.createLinearGradient(0, 0, W, H);
      g.addColorStop(0, '#7fd1ae');
      g.addColorStop(1, '#1f7a56');
      bctx.fillStyle = g;
      bctx.fillRect(0, 0, W, H);
    }

    // 缺口
    bctx.save();
    piecePath(bctx, targetX, y, size, knob);
    bctx.fillStyle = 'rgba(6, 20, 14, 0.55)';
    bctx.fill();
    bctx.lineWidth = 1.5;
    bctx.strokeStyle = 'rgba(255,255,255,0.85)';
    bctx.stroke();
    bctx.restore();

    // 拼图块（从同一画面裁出）
    this.piece.width = Math.round(boxW * dpr);
    this.piece.height = Math.round(size * dpr);
    this.piece.style.width = boxW + 'px';
    this.piece.style.height = size + 'px';
    const pctx = this.piece.getContext('2d');
    pctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    pctx.clearRect(0, 0, boxW, size);
    pctx.save();
    piecePath(pctx, 0, 0, size, knob);
    pctx.clip();
    pctx.drawImage(this.bg, targetX * dpr, y * dpr, boxW * dpr, size * dpr, 0, 0, boxW, size);
    pctx.restore();
    piecePath(pctx, 0, 0, size, knob);
    pctx.strokeStyle = 'rgba(255,255,255,0.9)';
    pctx.lineWidth = 1.5;
    pctx.stroke();

    this.geo = {
      startX: startX,
      travel: travel,
      targetX: targetX,
      maxOffset: Math.max(1, this.bar.clientWidth - HANDLE_W),
    };
    this.piece.style.left = startX + 'px';
    this.piece.style.top = y + 'px';
    this.loadingEl.style.display = 'none';
    this.text.textContent = '按住滑块，拖动完成拼图';
  };

  PuzzleCaptcha.prototype._handleDown = function (e) {
    if (!this.geo || this.state === 'success') return;
    e.preventDefault();
    this.state = 'dragging';
    this.dragFrom = e.clientX - this.offset;
    window.addEventListener('pointermove', this._onMove);
    window.addEventListener('pointerup', this._onUp);
    window.addEventListener('pointercancel', this._onUp);
  };

  PuzzleCaptcha.prototype._handleMove = function (e) {
    if (this.state !== 'dragging' || !this.geo) return;
    const next = Math.min(Math.max(0, e.clientX - this.dragFrom), this.geo.maxOffset);
    this.offset = next;
    this.handle.style.transform = 'translateX(' + next + 'px)';
    this.fill.style.width = (next + HANDLE_W) + 'px';
    this.piece.style.left = (this.geo.startX + (next / this.geo.maxOffset) * this.geo.travel) + 'px';
  };

  PuzzleCaptcha.prototype._handleUp = function () {
    window.removeEventListener('pointermove', this._onMove);
    window.removeEventListener('pointerup', this._onUp);
    window.removeEventListener('pointercancel', this._onUp);
    if (this.state !== 'dragging' || !this.geo) return;

    const pieceX = this.geo.startX + (this.offset / this.geo.maxOffset) * this.geo.travel;
    if (Math.abs(pieceX - this.geo.targetX) <= TOLERANCE) {
      this.piece.style.left = this.geo.targetX + 'px';
      this.state = 'success';
      this.bar.classList.add('success');
      this.text.textContent = '验证通过';
      setTimeout(() => { if (this.mask) this._finish(true); }, 450);
    } else {
      this.state = 'fail';
      this.bar.classList.add('fail');
      this.text.textContent = '未对齐，正在换一张…';
      setTimeout(() => { if (this.mask) this._init(); }, 700);
    }
  };

  PuzzleCaptcha.prototype._finish = function (ok) {
    window.removeEventListener('pointermove', this._onMove);
    window.removeEventListener('pointerup', this._onUp);
    window.removeEventListener('pointercancel', this._onUp);
    if (this.mask) {
      this.mask.remove();
      this.mask = null;
    }
    this.geo = null;
    const resolve = this._resolve;
    this._resolve = null;
    if (resolve) resolve(!!ok);
  };

  global.PuzzleCaptcha = PuzzleCaptcha;
})(window);
