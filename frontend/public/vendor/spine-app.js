/*
 * 交错战线 Spine 动态立绘运行时（预览与导出壁纸共用）。
 *
 * 依赖：spine-webgl 4.2（window.spine）与 wallpaper-layout.js（window.WallpaperLayout）。
 * 职责：加载 Spine 三件套、待机动画、点击互动（程序化反馈 + 语音 + 字幕）、截图封面。
 *
 * 用法：
 *   var app = SpineApp.create(canvas, {
 *     json: 'assets/character.json',
 *     atlas: 'assets/character.atlas',
 *     voice: { lines: [...], base: 'assets/voice/' },
 *     layout: { scale: 100, offsetX: 0, offsetY: 0, alignment: 'center' },
 *     subtitleEl: document.getElementById('subtitle'),
 *     options: { voice: true, interact: true, volume: 0.8, idleInterval: 60 },
 *   })
 */
(function (root) {
  'use strict';

  var MARGIN = 0.94;

  function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }

  function anchorOf(name) {
    var map = {
      center: [0.5, 0.5],
      'left-top': [0, 0],
      'right-top': [1, 0],
      'left-bottom': [0, 1],
      'right-bottom': [1, 1],
    };
    return map[name] || map.center;
  }

  function pick(list, index) {
    if (!list || !list.length) return null;
    return list[((index % list.length) + list.length) % list.length];
  }

  function create(canvas, opts) {
    opts = opts || {};
    var options = Object.assign({
      voice: true,
      interact: true,
      subtitle: true,
      volume: 0.8,
      idleInterval: 60,
      targetFps: 60,
      speed: 100,
      intro: true,
    }, opts.options || {});
    var layout = Object.assign({
      scale: 100, offsetX: 0, offsetY: 0, alignment: 'center',
    }, opts.layout || {});

    // Spine 的 additive 槽会在透明画布上累积出黑块，因此 WebGL 画布保持不透明，
    // 背景直接画在 WebGL 里（palette + 背景模式由导出器传入）。
    var gl = canvas.getContext('webgl', {
      alpha: false, premultipliedAlpha: true, preserveDrawingBuffer: true,
    });
    if (!gl) throw new Error('WebGL 不可用');

    var assetManager = new spine.AssetManager(gl, opts.prefix || '');
    var renderer = new spine.SceneRenderer(canvas, gl);
    var skeleton = null;
    var state = null;
    var bounds = { x: 0, y: 0, width: 1, height: 1 };
    var headBone = null;
    var headAnchor = null;
    var destroyed = false;
    var lastTime = performance.now();
    var lastRender = 0;
    var react = { t: 0, duration: 0.55, kind: '', baseHead: null };

    var audio = { current: null, volume: options.volume, muted: !options.voice };
    var voice = opts.voice || null;
    // 可选调试日志钩子：opts.onDebugLog(message)，预览页可挂到 window 上排查互动问题
    var dbgLog = typeof opts.onDebugLog === 'function' ? opts.onDebugLog : null;
    function slog(msg) {
      if (dbgLog) { try { dbgLog(msg); } catch (e) { /* 调试钩子异常不影响运行 */ } }
    }
    var subtitleEl = opts.subtitleEl || null;
    var subtitleTimer = null;
    var bodyIndex = 0;
    var idleAnims = [];
    var clickAnims = [];
    var introAnim = '';
    var idleAnim = '';
    var idleStarted = false;
    var actions = null;
    var itemVisible = {};
    var actionIndexMap = {};
    var lastAction = '';
    var audioCursor = {};
    var mulState = {};
    var mulPending = null;
    var dragState = null;
    var panState = null;
    var dragFinish = null;
    var clickTimes = [];
    var idleTimer = null;
    var ready = false;
    // 交互区域叠加层（WE「交互区域」开关）：热区世界坐标矩形按帧同步成屏幕 DOM
    var areasEl = opts.areasEl || null;
    var showAreas = false;
    var areaEls = [];
    // 页面焦点跟踪：WE 会把全局鼠标输入转发给壁纸页面（在别的应用里点击也会收到
    // pointerdown），页面获得过焦点后只在仍有焦点时响应点击——直接点桌面时 WE 会把
    // 焦点给壁纸页面，切到其它应用后焦点转走。从未获得过焦点的宿主保持原行为，
    // 避免互动整体失效。
    var pageFocused = false;
    var focusSeen = false;
    function syncPageFocus() {
      pageFocused = document.hasFocus();
      if (pageFocused) focusSeen = true;
    }
    syncPageFocus();
    window.addEventListener('focus', syncPageFocus);
    window.addEventListener('blur', function () { pageFocused = false; });
    function pageActive() {
      return !focusSeen || pageFocused;
    }

    var linesByCategory = {};
    (voice && voice.lines ? voice.lines : []).forEach(function (line) {
      if (!line || !line.category) return;
      (linesByCategory[line.category] = linesByCategory[line.category] || []).push(line);
    });

    function showSubtitle(text) {
      // 开关关闭时强制清空，预览组件的 onSubtitle 路径也要拿到空串才能隐藏
      if (!options.subtitle) text = '';
      if (typeof opts.onSubtitle === 'function') opts.onSubtitle(text);
      if (!subtitleEl) return;
      subtitleEl.textContent = text || '';
      subtitleEl.classList.toggle('visible', !!text);
      if (subtitleTimer) clearTimeout(subtitleTimer);
      if (text) {
        subtitleTimer = setTimeout(function () {
          subtitleEl.classList.remove('visible');
        }, 12000);
      }
    }

    function playAudio(line) {
      if (!line || !voice) return;
      if (!options.voice) { showSubtitle(line.text); return; }
      try {
        if (audio.current) { audio.current.pause(); audio.current = null; }
        var el = new Audio((voice.base || '') + line.file);
        el.volume = clamp(audio.volume, 0, 1);
        el.muted = audio.muted;
        audio.current = el;
        el.play().catch(function () {});
        showSubtitle(line.text);
      } catch (e) {
        showSubtitle(line.text);
      }
    }

    function speak(category, fallbackCategory) {
      var pool = linesByCategory[category];
      if (!pool || !pool.length) pool = linesByCategory[fallbackCategory];
      return pool && pool.length ? pick(pool, Math.floor(Math.random() * pool.length)) : null;
    }

    function playClickAnim() {
      if (!clickAnims.length || !state) return false;
      var name = clickAnims[Math.floor(Math.random() * clickAnims.length)];
      slog('fallbackClick ' + name);
      startIdle();
      state.setAnimation(1, name, false);
      return true;
    }

    function reactNow(kind) {
      react.kind = kind;
      react.t = 0;
      react.duration = kind === 'head' ? 0.5 : 0.65;
      react.baseHead = headBone ? headBone.rotation : null;
    }

    function onTouch(kind) {
      if (!skeleton) return;
      playClickAnim();
      if (kind === 'head') {
        reactNow('head');
        var head = speak('心动接触', '接触1');
        if (head) playAudio(head);
        return;
      }
      var now = performance.now();
      clickTimes = clickTimes.filter(function (t) { return now - t < 6000; });
      clickTimes.push(now);
      if (clickTimes.length >= 4) {
        clickTimes = [];
        var mood = Math.random() < 0.5 ? '愉悦' : '不悦';
        playAudio(speak(mood, '接触1'));
        reactNow('mood');
        return;
      }
      var order = ['接触1', '接触2', '接触3', '友好', '喜欢', '挚爱'];
      var category = order[bodyIndex % order.length];
      bodyIndex += 1;
      playAudio(speak(category, '接触1'));
      reactNow('body');
    }

    function scheduleIdle() {
      if (idleTimer) clearTimeout(idleTimer);
      var seconds = Number(options.idleInterval);
      if (!isFinite(seconds) || seconds <= 0) return;
      var delay = (seconds * (0.7 + Math.random() * 0.6)) * 1000;
      idleTimer = setTimeout(function () {
        if (!destroyed) {
          var line = speak('看板', '登录');
          if (line) playAudio(line);
          scheduleIdle();
        }
      }, delay);
    }

    function worldFromScreen(px, py) {
      var cam = renderer.camera;
      var w = canvas.width, h = canvas.height;
      return {
        x: cam.position.x + (px - w / 2) * cam.zoom,
        y: cam.position.y - (py - h / 2) * cam.zoom,
      };
    }

    function resize() {
      var dpr = Math.min(root.devicePixelRatio || 1, 2);
      var w = Math.max(1, Math.round(canvas.clientWidth * dpr));
      var h = Math.max(1, Math.round(canvas.clientHeight * dpr));
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w;
        canvas.height = h;
      }
      renderer.camera.viewportWidth = canvas.width;
      renderer.camera.viewportHeight = canvas.height;
      applyCamera();
    }

    function applyCamera() {
      if (!skeleton) return;
      var cam = renderer.camera;
      var w = canvas.width, h = canvas.height;
      var bw = Math.max(1, bounds.width), bh = Math.max(1, bounds.height);
      var scalePct = clamp(Number(layout.scale) || 100, 20, 800) / 100;
      var fit = Math.min(w / bw, h / bh) * MARGIN * scalePct;
      if (!isFinite(fit) || fit <= 0) fit = 1;
      var anchors = anchorOf(layout.alignment);
      var ox = clamp(Number(layout.offsetX) || 0, -100, 100) / 100 * w;
      var oy = clamp(Number(layout.offsetY) || 0, -100, 100) / 100 * h;
      var cx = (bw * fit) / 2 + anchors[0] * (w - bw * fit) + ox;
      var cy = (bh * fit) / 2 + anchors[1] * (h - bh * fit) + oy;
      var zoom = 1 / fit;
      cam.zoom = zoom;
      cam.position.x = (bounds.x + bw / 2) - (cx - w / 2) * zoom;
      cam.position.y = (bounds.y + bh / 2) + (cy - h / 2) * zoom;
      cam.update();
    }


    var bgCanvas = document.createElement('canvas');
    var bgTexture = null;
    var bgKey = '';

    function hexToRgb(color) {
      var text = String(color || '#182438').replace('#', '');
      if (text.length === 3) text = text.split('').map(function (c) { return c + c; }).join('');
      return {
        r: parseInt(text.slice(0, 2), 16) || 0,
        g: parseInt(text.slice(2, 4), 16) || 0,
        b: parseInt(text.slice(4, 6), 16) || 0,
      };
    }

    function vivid(color, minLight, minSat) {
      // 深色机甲立绘取色常接近黑灰：提亮/加饱和，避免壁纸背景糊成一片黑
      var rgb = hexToRgb(color);
      var r = rgb.r / 255, g = rgb.g / 255, b = rgb.b / 255;
      var max = Math.max(r, g, b), min = Math.min(r, g, b);
      var l = (max + min) / 2;
      if (l >= minLight) return color;
      var s0 = max === min ? 0 : (l > 0.5 ? (max - min) / (2 - max - min) : (max - min) / (max + min));
      var s = Math.max(s0, minSat);
      var h = s0 < 0.08 ? 0.58 : 0;  // 近中性色统一拉向冷蓝，避免提亮后发红发脏
      if (max !== min) {
        if (max === r) h = ((g - b) / (max - min)) % 6;
        else if (max === g) h = (b - r) / (max - min) + 2;
        else h = (r - g) / (max - min) + 4;
        h /= 6;
      }
      function hue2rgb(p0, q0, t) {
        if (t < 0) t += 1;
        if (t > 1) t -= 1;
        if (t < 1 / 6) return p0 + (q0 - p0) * 6 * t;
        if (t < 1 / 2) return q0;
        if (t < 2 / 3) return p0 + (q0 - p0) * (2 / 3 - t) * 6;
        return p0;
      }
      var q = l < 0.5 ? l * (1 + s) : l + s - l * s;
      var p0 = 2 * l - q;
      var nr = hue2rgb(p0, q, h + 1 / 3), ng = hue2rgb(p0, q, h), nb = hue2rgb(p0, q, h - 1 / 3);
      function hex(v) { var t = Math.round(Math.max(0, Math.min(1, v)) * 255).toString(16); return t.length < 2 ? '0' + t : t; }
      return '#' + hex(nr) + hex(ng) + hex(nb);
    }

    function paintBackground(width, height) {
      var palette = ((opts.background && opts.background.palette) || ['#1d3f6e', '#0e1b33', '#13294d', '#0b0f17'])
        .map(function (c) { return vivid(c, 0.34, 0.42); });
      var mode = (opts.background && opts.background.mode) || 'monet';
      if (mode === 'gradient') mode = 'monet';
      bgCanvas.width = width;
      bgCanvas.height = height;
      var ctx = bgCanvas.getContext('2d');
      var c0 = palette[0] || '#1d3f6e';
      var c1 = palette[1] || c0;
      var c2 = palette[2] || c1;
      var base = ctx.createLinearGradient(0, 0, width, height);
      if (mode === 'solid') {
        ctx.fillStyle = c0;
        ctx.fillRect(0, 0, width, height);
        return;
      }
      if (mode === 'star') {
        base.addColorStop(0, c0);
        base.addColorStop(1, '#080c14');
      } else if (mode === 'frost') {
        base.addColorStop(0, c0);
        base.addColorStop(1, '#0b0f17');
      } else {
        base.addColorStop(0, c0);
        base.addColorStop(0.55, '#0e1b33');
        base.addColorStop(1, '#0b0f17');
      }
      ctx.fillStyle = base;
      ctx.fillRect(0, 0, width, height);
      function glow(cx, cy, rx, ry, color, alpha) {
        var rgb = hexToRgb(color);
        var g = ctx.createRadialGradient(
          cx * width, cy * height, 0,
          cx * width, cy * height, Math.max(rx * width, ry * height)
        );
        g.addColorStop(0, 'rgba(' + rgb.r + ',' + rgb.g + ',' + rgb.b + ',' + alpha + ')');
        g.addColorStop(1, 'rgba(' + rgb.r + ',' + rgb.g + ',' + rgb.b + ',0)');
        ctx.fillStyle = g;
        ctx.fillRect(0, 0, width, height);
      }
      if (mode === 'monet') {
        glow(0.2, 0.25, 0.6, 0.7, c0, 0.35);
        glow(0.8, 0.2, 0.55, 0.65, c1, 0.3);
        glow(0.5, 0.95, 0.7, 0.8, c2, 0.22);
      } else if (mode === 'frost') {
        glow(0.3, 0.3, 0.45, 0.55, c0, 0.25);
        glow(0.75, 0.7, 0.45, 0.55, c1, 0.22);
      } else if (mode === 'star') {
        ctx.fillStyle = 'rgba(255,255,255,0.7)';
        [[0.2, 0.3], [0.6, 0.15], [0.8, 0.45], [0.4, 0.7], [0.9, 0.8]].forEach(function (p) {
          ctx.beginPath();
          ctx.arc(p[0] * width, p[1] * height, Math.max(1.2, width * 0.0016), 0, Math.PI * 2);
          ctx.fill();
        });
      }
    }

    function ensureBackground() {
      var key = canvas.width + 'x' + canvas.height + '|' +
        ((opts.background && opts.background.mode) || 'monet') + '|' +
        ((opts.background && opts.background.palette) || []).join(',');
      if (key === bgKey && bgTexture) return;
      paintBackground(canvas.width, canvas.height);
      if (bgTexture && bgTexture.dispose) bgTexture.dispose();
      bgTexture = new spine.GLTexture(gl, bgCanvas);
      bgKey = key;
    }

    function paintWorld() {
      // 清屏 + 背景 + 当前骨架姿态（animate 与封面截图共用）
      gl.viewport(0, 0, canvas.width, canvas.height);
      gl.clearColor(0.02, 0.03, 0.05, 1);
      gl.clear(gl.COLOR_BUFFER_BIT);
      ensureBackground();
      if (!skeleton) return;
      renderer.begin();
      var cam = renderer.camera;
      if (bgTexture) {
        var worldW = canvas.width * cam.zoom;
        var worldH = canvas.height * cam.zoom;
        renderer.drawTexture(bgTexture, cam.position.x - worldW / 2, cam.position.y - worldH / 2,
          worldW, worldH);
      }
      renderer.drawSkeleton(skeleton, true);
      renderer.end();
    }

    function animate(now) {
      if (destroyed) return;
      var fps = Number(options.targetFps) || 0;
      if (fps > 0 && now - lastRender < 1000 / fps - 1.5) {
        requestAnimationFrame(animate);
        return;
      }
      lastRender = now;
      var dt = Math.min((now - lastTime) / 1000, 0.05);
      lastTime = now;
      resizeIfNeeded();
      if (skeleton) {
        state.update(dt);
        state.apply(skeleton);
        if (idleStarted) {
          if (!state.getCurrent(0) && idleAnim) state.setAnimation(0, idleAnim, true);
        } else if (!state.getCurrent(1)) {
          startIdle();
        }
        if (mulPending) {
          var mulEntry = actionEntry();
          if (!mulEntry || mulEntry !== mulPending.entry) {
            mulPending = null;
          } else if (mulPending.reverse) {
            if (mulEntry.trackTime <= mulPending.target + 0.001) {
              slog('mulReverse done t=' + mulEntry.trackTime.toFixed(3) + ' final=' + mulPending.final);
              mulEntry.timeScale = 0;
              if (mulPending.final) {
                applyActivation(mulPending.item);
                if (mulPending.state) mulPending.state.trackTime = 0;
              } else if (mulPending.state) {
                mulPending.state.trackTime = mulEntry.trackTime;
              }
              mulPending = null;
            }
          } else if (mulEntry.trackTime >= mulPending.target - 0.001) {
            slog('mulHold t=' + mulEntry.trackTime.toFixed(3) + ' target=' + mulPending.target.toFixed(3) + ' final=' + mulPending.final);
            if (mulPending.final) {
              applyActivation(mulPending.item);
              if (mulPending.state) mulPending.state.trackTime = 0;
              mulPending = null;
            } else {
              mulEntry.timeScale = 0;
              if (mulPending.state) mulPending.state.trackTime = mulEntry.trackTime;
              mulPending = null;
            }
          }
        }
        if (dragFinish) {
          var de = actionEntry();
          if (!de || de !== dragFinish.entry || de.trackTime >= (de.animation ? de.animation.duration : 1) - 0.001) {
            applyActivation(dragFinish.item);
            dragFinish = null;
          }
        }
        // 点击/拖拽动作播完清掉动作轨道，回到 track0 的待机（否则会停在动作最后一帧）
        if (!mulPending && !dragState) {
          var done = actionEntry();
          if (done && done.timeScale !== 0 && done.animation &&
              done.trackTime >= done.animation.duration - 0.001) {
            slog('actionClear ' + done.animation.name + ' t=' + done.trackTime.toFixed(3));
            state.clearTrack(1);
          }
        }
        skeleton.updateWorldTransform(spine.Physics.update);

        // 点击反馈：整身轻微弹跳/摇摆 + 头部骨骼小幅偏转
        if (react.t < react.duration) {
          react.t += dt;
          var p = Math.min(1, react.t / react.duration);
          var decay = (1 - p) * (1 - p);
          var amp = react.kind === 'head' ? 10 : 16;
          skeleton.x = Math.sin(p * Math.PI * 6) * amp * decay;
          skeleton.y = Math.abs(Math.sin(p * Math.PI * 3)) * amp * 0.6 * decay;
          if (headBone && react.baseHead != null) {
            headBone.rotation = react.baseHead + Math.sin(p * Math.PI * 4) * 3.2 * decay;
          }
        } else {
          if (skeleton.x || skeleton.y) { skeleton.x = 0; skeleton.y = 0; }
          if (headBone && react.baseHead != null) {
            headBone.rotation = react.baseHead;
            react.baseHead = null;
          }
        }
      }
      paintWorld();
      if (showAreas) syncAreas();
      requestAnimationFrame(animate);
    }

    var resizePending = false;
    function resizeIfNeeded() {
      if (resizePending) return;
      resizePending = true;
      try { resize(); } finally { resizePending = false; }
    }

    function boneName(bone) {
      return String((bone && bone.data && bone.data.name) || '').toLowerCase();
    }

    function findBone(sk, names) {
      var bones = sk.bones || [];
      for (var i = 0; i < names.length; i++) {
        for (var j = 0; j < bones.length; j++) {
          if (boneName(bones[j]) === names[i]) return bones[j];
        }
      }
      return null;
    }

    function findHeadBones(sk) {
      // 交错战线的 Spine 骨架没有 head 骨骼：命中锚点用 face，反馈旋转用 neck。
      var anchor = findBone(sk, ['face', 'head', 'b_head', 'neck']) ||
        (sk.bones || []).find(function (b) { return boneName(b).indexOf('head') >= 0; }) || null;
      var rotate = findBone(sk, ['neck', 'head', 'b_head', 'face']) || anchor;
      return { anchor: anchor || null, rotate: rotate || null };
    }

    function setActions(data) {
      actions = data && data.items && data.items.length ? data : null;
      itemVisible = {};
      actionIndexMap = {};
      if (actions) {
        actions.items.forEach(function (item, i) {
          // 游戏里带 isHide 的动作初始隐藏，靠 activation 开关
          itemVisible[i] = item.isHide ? false : true;
          if (item.index != null) actionIndexMap[String(item.index)] = i;
        });
      }
      // 叠加层依赖 actions/itemVisible：异步 actions.json 到位后（或被清空时）重建
      buildAreas();
    }

    function rectContains(px, py, rect) {
      var cx = rect[0], cy = rect[1], w = rect[2], h = rect[3];
      var angle = (rect[4] || 0) * Math.PI / 180;
      var dx = px - cx, dy = py - cy;
      // Unity UI 旋转为正角度逆时针；转到矩形本地坐标相当于反向旋转
      var rx = dx * Math.cos(angle) + dy * Math.sin(angle);
      var ry = -dx * Math.sin(angle) + dy * Math.cos(angle);
      return Math.abs(rx) <= w / 2 && Math.abs(ry) <= h / 2;
    }

    function findAction(world) {
      if (!actions) return null;
      var hit = null;
      for (var i = 0; i < actions.items.length; i++) {
        var item = actions.items[i];
        if (!itemVisible[i]) continue;
        if (!item.rects || !item.rects.length) continue;
        for (var j = 0; j < item.rects.length; j++) {
          if (rectContains(world.x, world.y, item.rects[j])) hit = item; // 后创建的在上层
        }
      }
      return hit;
    }

    // ---- 交互区域叠加层：热区（世界坐标矩形）按帧同步成屏幕坐标 DOM ----
    // 换算公式与预览组件 SpinePreview.vue 的实现一致，改动需两处同步。
    function clearAreaLayer() {
      areaEls = [];
      if (areasEl) areasEl.innerHTML = '';
    }

    function buildAreas() {
      if (!areasEl) return;
      clearAreaLayer();
      if (!showAreas) return;
      if (!actions || !actions.items.length) {
        // 没有动作配置的装束没有热区可画：给出明确提示，别让开关看起来像坏了
        var tip = document.createElement('div');
        tip.className = 'spine-areas-empty';
        tip.textContent = '当前装束没有交互热区配置（商店皮才有）';
        areasEl.appendChild(tip);
        return;
      }
      actions.items.forEach(function (item, i) {
        (item.rects || []).forEach(function (rect) {
          var el = document.createElement('div');
          el.className = 'spine-area';
          el.__rect = rect;
          el.__item = i;
          el.__name = item.name;
          el.__dormant = null;
          el.innerHTML = '<span>' + item.name + '</span>';
          areasEl.appendChild(el);
          areaEls.push(el);
        });
      });
      syncAreas();
    }

    function syncAreas() {
      if (!showAreas || !areaEls.length || !skeleton) return;
      var cssW = canvas.clientWidth || 1;
      var cssH = canvas.clientHeight || 1;
      // 相机以设备像素计，换算成 CSS 像素再定位
      var zoom = renderer.camera.zoom * (canvas.width / cssW);
      for (var k = 0; k < areaEls.length; k++) {
        var el = areaEls[k];
        var r = el.__rect;
        var sx = (r[0] - renderer.camera.position.x) / zoom + cssW / 2;
        var sy = cssH / 2 - (r[1] - renderer.camera.position.y) / zoom;
        var dormant = !itemVisible[el.__item];
        if (dormant !== el.__dormant) {
          el.__dormant = dormant;
          el.classList.toggle('dormant', dormant);
        }
        var w = r[2] / zoom;
        var h = r[3] / zoom;
        // 太小的热区放不下动作名徽标，藏掉避免糊成一团
        el.classList.toggle('tiny', w < 30 || h < 15);
        el.style.width = w + 'px';
        el.style.height = h + 'px';
        // 热区旋转为正角度逆时针（Unity UI），屏幕 y 翻转后 CSS 取负角
        el.style.transform = 'translate(' + sx + 'px, ' + sy + 'px) translate(-50%, -50%) rotate(' + (-(r[4] || 0)) + 'deg)';
      }
    }

    function startIdle() {
      if (idleStarted || !state || !idleAnim) return;
      state.setAnimation(0, idleAnim, true);
      idleStarted = true;
    }

    function performAction(item) {
      if (!state || !item || !item.name) return false;
      lastAction = item.name;
      startIdle();
      state.setAnimation(1, item.name, false);
      if (item.audio && item.audio.length) {
        // 同一动作的台词按顺序轮换（游戏里 clicks/actions 走 records 顺序），不随机重复
        var cursor = audioCursor[item.name] || 0;
        var line = item.audio[cursor % item.audio.length];
        audioCursor[item.name] = cursor + 1;
        playAudio(line);
        if (item.hideTxt) showSubtitle('');
      }
      applyActivation(item);
      if (item.changeIdle && item.changeIdle.anim) {
        var anim = item.changeIdle.anim;
        var delay = Number(item.changeIdle.delay) || 0;
        setTimeout(function () {
          idleAnim = anim;
          if (state && idleStarted) state.setAnimation(0, anim, true);
        }, delay);
      }
      return true;
    }

    function actionEntry() {
      try {
        return state ? state.getCurrent(1) : null;
      } catch (e) {
        return null;
      }
    }

    function applyActivation(item) {
      if (!item.activation) return;
      Object.keys(item.activation).forEach(function (key) {
        // activation 的 key 是配置里的动作 index（含 in 占位），按 index 映射而不是数组下标
        var pos = actionIndexMap[String(key)];
        if (pos !== undefined) itemVisible[pos] = !!item.activation[key];
      });
    }

    function playStageAudio(item, stage) {
      if (!item.audio || !item.audio.length) return;
      var index = Math.max(0, Math.min(stage, item.audio.length - 1));
      var line = item.audio[index];
      playAudio(line);
      if (item.hideTxt) showSubtitle('');
    }

    function animDuration(name) {
      var anims = (skeleton && skeleton.data && skeleton.data.animations) || [];
      for (var i = 0; i < anims.length; i++) {
        if (anims[i].name === name) return anims[i].duration || 1;
      }
      return 1;
    }

  // 多段点击：clicks 是每段的动画进度（0 表示反向），走同一轨道，最后一段播完才执行 activation
  function startMultiClick(item) {
    if (!state) return;
    startIdle();
    lastAction = item.name;
    var now = performance.now();
    var limitMs = Number(item.clickTime) || 3000;
    var st = mulState[item.name] || { stage: 0, lastAt: 0 };
    if (now - st.lastAt > limitMs) {
      // 超时重新开始一段序列：进度记录一并清零，否则首段会瞬间"保持"在旧进度
      st.stage = 0;
      st.trackTime = 0;
    }
    st.stage += 1;
    st.lastAt = now;
    mulState[item.name] = st;

    var startTime = Number(st.trackTime) || 0;
    var entry = state.setAnimation(1, item.name, false);
    var duration = animDuration(item.name);
    var progress = Number(item.clicks[st.stage - 1]);
    if (!isFinite(progress)) progress = 1;
    var target = 0;
    if (progress === 0) {
      entry.timeScale = -1;
      entry.trackTime = duration;
      target = 0;
    } else {
      entry.timeScale = 1;
      entry.trackTime = Math.min(startTime, duration);
      target = Math.max(0, Math.min(1, progress)) * duration;
    }
    mulPending = {
      item: item,
      entry: entry,
      target: target,
      final: st.stage >= item.clicks.length,
      duration: duration,
      reverse: progress === 0,
      state: st,
    };
    if (st.stage >= item.clicks.length) st.stage = 0;
    slog('mulClick ' + item.name + ' stage=' + st.stage + '/' + item.clicks.length +
      ' start=' + startTime.toFixed(3) + ' target=' + target.toFixed(3) +
      ' dur=' + duration.toFixed(3) + ' final=' + mulPending.final + ' reverse=' + mulPending.reverse);
    playStageAudio(item, mulPending.final ? item.audio.length - 1 : st.stage - 1);
  }

    function startDrag(item, clientX, clientY) {
      if (!state) return;
      startIdle();
      lastAction = item.name;
      var entry = state.setAnimation(1, item.name, false);
      entry.timeScale = 0;
      dragState = {
        item: item,
        entry: entry,
        duration: animDuration(item.name),
        startX: clientX,
        startY: clientY,
        base: 0,
        progress: 0,
      };
      playStageAudio(item, 0);
    }

    function moveDrag(clientX, clientY) {
      if (!dragState) return;
      var data = dragState.item.gestureDatas || {};
      var delta = dragState.item.gesture === 3 ? (clientX - dragState.startX) : -(clientY - dragState.startY);
      var speed = (Number(data.speed) || 2) * 0.001;
      var limit = Math.max(0.05, Math.min(1, Number(data.limitPerc) || 1));
      dragState.progress = Math.max(0, Math.min(limit, dragState.base + delta * speed));
      dragState.entry.trackTime = dragState.progress * dragState.duration;
    }

    function endDrag() {
      if (!dragState) return;
      var d = dragState;
      dragState = null;
      var data = d.item.gestureDatas || {};
      var limit = Math.max(0.05, Math.min(1, Number(data.limitPerc) || 1));
      if (d.progress >= limit * 0.99) {
        d.entry.timeScale = 1;
        d.entry.trackTime = Math.min(d.duration, limit * d.duration);
        dragFinish = { item: d.item, entry: d.entry };
      } else {
        d.entry.timeScale = 0;
        setTimeout(function () {
          if (state && actionEntry() === d.entry) state.clearTrack(1);
        }, 220);
      }
    }

    function bindPointer() {
      var downAt = null;
      // 把最近一次平移位移同步给布局回调（pointermove 频率高于帧率，逐事件回调会卡顿）
      function flushPan() {
        if (!panState || !panState.moved || !opts.onPan) return;
        if (panState.raf) {
          cancelAnimationFrame(panState.raf);
          panState.raf = 0;
        }
        var rect = canvas.getBoundingClientRect();
        opts.onPan(
          (panState.dx / Math.max(1, rect.width)) * 100,
          (panState.dy / Math.max(1, rect.height)) * 100
        );
      }
      canvas.addEventListener('pointerdown', function (e) {
        if (!options.interact || !ready) return;
        if (!pageActive()) return;
        if (typeof opts.isAdjusting === 'function' && opts.isAdjusting()) return;
        if (e.button != null && e.button !== 0) return;
        var rect = canvas.getBoundingClientRect();
        var px = (e.clientX - rect.left) * (canvas.width / Math.max(1, rect.width));
        var py = (e.clientY - rect.top) * (canvas.height / Math.max(1, rect.height));
        var world = worldFromScreen(px, py);
        var action = findAction(world);
        downAt = { x: e.clientX, y: e.clientY };
        if (action && action.gesture && action.gestureDatas) {
          startDrag(action, e.clientX, e.clientY);
          try { canvas.setPointerCapture(e.pointerId); } catch (err) {}
          e.preventDefault();
        } else if (opts.onPan) {
          panState = { x: e.clientX, y: e.clientY, moved: false, dx: 0, dy: 0, raf: 0 };
          if (opts.onPanStart) opts.onPanStart();
          canvas.style.cursor = 'grabbing';
        }
      });
      canvas.addEventListener('pointermove', function (e) {
        if (dragState) {
          moveDrag(e.clientX, e.clientY);
          e.preventDefault();
          return;
        }
        if (!panState) return;
        var dx = e.clientX - panState.x;
        var dy = e.clientY - panState.y;
        if (!panState.moved && Math.abs(dx) + Math.abs(dy) < 4) return;
        panState.moved = true;
        panState.dx = dx;
        panState.dy = dy;
        // 平移回调按帧合并：位移记下来，下一帧统一交给 onPan
        if (!panState.raf) {
          panState.raf = requestAnimationFrame(function () {
            if (!panState) return;
            panState.raf = 0;
            flushPan();
          });
        }
        e.preventDefault();
      });
      canvas.addEventListener('pointerup', function (e) {
        if (!options.interact || !ready) return;
        if (!pageActive()) return;
        if (typeof opts.isAdjusting === 'function' && opts.isAdjusting()) return;
        if (e.button != null && e.button !== 0) return;
        if (dragState) {
          endDrag();
          downAt = null;
          return;
        }
        if (panState) {
          var panned = panState.moved;
          if (panned) flushPan(); // 松手前把最后一次位移落盘，避免停在旧位置
          panState = null;
          canvas.style.cursor = 'grab';
          if (panned) {
            downAt = null;
            return;
          }
        }
        // 拖动过就不当点击处理
        if (downAt && (Math.abs(e.clientX - downAt.x) > 6 || Math.abs(e.clientY - downAt.y) > 6)) {
          downAt = null;
          return;
        }
        var rect = canvas.getBoundingClientRect();
        var px = (e.clientX - rect.left) * (canvas.width / Math.max(1, rect.width));
        var py = (e.clientY - rect.top) * (canvas.height / Math.max(1, rect.height));
        var world = worldFromScreen(px, py);
        if (world.x < bounds.x || world.x > bounds.x + bounds.width) return;
        if (world.y < bounds.y || world.y > bounds.y + bounds.height) return;
        var action = findAction(world);
        if (action) {
          if (action.clicks && action.clicks.length) startMultiClick(action);
          else performAction(action);
          return;
        }
        // 没有热区配置时才退回粗略的头/身体判定（游戏里点热区外也是无反应的）。
        // 有热区配置的装束点空白处不再误触发「接触」——WE 会全局转发鼠标点击，
        // 不收紧这里的话在别的应用里点一下也可能随机冒出台词。
        if (actions && actions.items.length) return;
        var isHead = false;
        if (headAnchor && isFinite(headAnchor.worldX)) {
          var dx = world.x - headAnchor.worldX;
          var dy = world.y - headAnchor.worldY;
          isHead = Math.sqrt(dx * dx + dy * dy) < bounds.height * 0.12;
        } else {
          isHead = world.y >= bounds.y + bounds.height * 0.74;
        }
        onTouch(isHead ? 'head' : 'body');
      });
      canvas.addEventListener('wheel', function (e) {
        if (!opts.onZoom) return;
        e.preventDefault();
        opts.onZoom(Math.pow(1.1, -e.deltaY / 100));
      }, { passive: false });
      if (opts.onPan) canvas.style.cursor = 'grab';
      canvas.addEventListener('contextmenu', function (e) { e.preventDefault(); });
    }

    bindPointer();

    var pending = 0;
    function loadOne(method, path) {
      pending += 1;
      assetManager[method](path, function () {
        pending -= 1;
        if (pending === 0) setup();
      }, function (p, message) {
        pending -= 1;
        if (opts.onError) opts.onError(message);
        if (pending === 0) setup();
      });
    }

    function setup() {
      try {
        var atlas = assetManager.require(opts.atlasPath || 'character.atlas');
        var json = assetManager.require(opts.jsonPath || 'character.json');
        if (!atlas || !json) throw new Error('资源加载失败');
        var loader = new spine.AtlasAttachmentLoader(atlas);
        var skeletonJson = new spine.SkeletonJson(loader);
        var data = skeletonJson.readSkeletonData(json);
        skeleton = new spine.Skeleton(data);
        skeleton.setToSetupPose();
        skeleton.updateWorldTransform(spine.Physics.update);
        var rect = skeleton.getBoundsRect();
        bounds = {
          x: rect.x, y: rect.y,
          width: Math.max(1, rect.width), height: Math.max(1, rect.height),
        };
        var headBones = findHeadBones(skeleton);
        headAnchor = headBones.anchor;
        headBone = headBones.rotate;
        state = new spine.AnimationState(new spine.AnimationStateData(data));
        var names = (data.animations || []).map(function (a) { return a.name; });
        idleAnims = names.filter(function (n) { return /^idle/i.test(n); });
        clickAnims = names.filter(function (n) { return /^click/i.test(n); });
        introAnim = names.find(function (n) { return /^in$/i.test(n); }) || '';
        var idle = idleAnims[0] || names[0] || '';
        idleAnim = idle;
        if (introAnim && options.intro) {
          state.setAnimation(1, introAnim, false);
          var introData = (data.animations || []).find(function (a) { return a.name === introAnim; });
          var waitMs = Math.max(300, ((introData && introData.duration) || 1.2) * 1000);
          setTimeout(startIdle, waitMs);
        } else {
          startIdle();
        }
        state.timeScale = Math.max(0.25, Math.min(2, (Number(options.speed) || 100) / 100));
        ready = true;
        resize();
        if (opts.actions) {
          setActions(opts.actions);
        } else if (opts.actionsPath) {
          fetch(opts.actionsPath, { cache: 'no-store' })
            .then(function (resp) { return resp.ok ? resp.json() : null; })
            .then(function (data) { if (data) setActions(data); })
            .catch(function () {});
        }
        if (opts.onReady) opts.onReady({ animations: (data.animations || []).map(function (a) { return a.name; }) });
        var opening = speak('登录');
        if (opening) setTimeout(function () { playAudio(opening); }, 600);
        scheduleIdle();
      } catch (e) {
        if (opts.onError) opts.onError(String(e && e.message || e));
      }
    }

    loadOne('loadTextureAtlas', opts.atlasPath || 'character.atlas');
    loadOne('loadJson', opts.jsonPath || 'character.json');
    requestAnimationFrame(animate);
    root.addEventListener('resize', applyCamera);

    return {
      setLayout: function (next) {
        layout = Object.assign(layout, next || {});
        applyCamera();
      },
      setBackground: function (next) {
        opts.background = Object.assign({}, opts.background || {}, next || {});
        bgKey = '';
        applyCamera();
      },
      setOptions: function (next) {
        next = next || {};
        options = Object.assign(options, next);
        // 关闭台词开关时立即清掉已显示的字幕（含预览回调路径）
        if ('subtitle' in next && !options.subtitle) showSubtitle('');
        if ('speed' in next && state) {
          state.timeScale = Math.max(0.25, Math.min(2, (Number(next.speed) || 100) / 100));
        }
        if ('volume' in (next || {})) audio.volume = Number(next.volume);
        if ('voice' in (next || {})) audio.muted = !next.voice;
        if ('idleInterval' in (next || {})) scheduleIdle();
      },
      // 交互区域叠加层开关（WE「交互区域」属性 / 预览控制面板共用）
      setAreasVisible: function (on) {
        showAreas = !!on;
        buildAreas();
      },
      playCategory: function (category) { playAudio(speak(category)); },
      getSlotAlpha: function (name) {
        if (!skeleton) return null;
        var s = skeleton.findSlot(String(name));
        return s ? s.color.a : null;
      },
      getBounds: function () { return Object.assign({}, bounds); },
      debug: function () {
        return {
          bounds: Object.assign({}, bounds),
          head: headAnchor ? { x: headAnchor.worldX, y: headAnchor.worldY, name: headAnchor.data && headAnchor.data.name } : null,
          camera: { x: renderer.camera.position.x, y: renderer.camera.position.y, zoom: renderer.camera.zoom },
          canvas: [canvas.width, canvas.height],
          action: lastAction,
          current: (function () {
            try {
              var entry = state && state.getCurrent(0);
              return entry && entry.animation ? entry.animation.name : '';
            } catch (e) { return ''; }
          })(),
          hasActions: !!actions,
          actionName: (function () { var e = actionEntry(); return e && e.animation ? e.animation.name : ''; })(),
          actionTime: (function () { var e = actionEntry(); return e ? e.trackTime : 0; })(),
          visible: actions ? actions.items.map(function (it, i) { return itemVisible[i] ? it.name : null; }) : [],
        };
      },
      captureCover: function (width, height) {
        // 封面：临时把主画布切到封面尺寸、按可见包围盒完整取景渲染一帧后立刻还原。
        // 不能开第二个 WebGL context 离屏重画——贴图对象只在创建它的 context 里有效，
        // 跨 context 只会得到黑色剪影。整个流程同步执行，浏览器只会合成还原后的帧。
        if (!skeleton || !renderer) return null;
        var w = Math.max(1, width || 1280);
        var h = Math.max(1, height || 720);
        var savedW = canvas.width, savedH = canvas.height;
        var cam = renderer.camera;
        var savedCam = {
          x: cam.position.x, y: cam.position.y, zoom: cam.zoom,
          vw: cam.viewportWidth, vh: cam.viewportHeight,
        };
        var savedX = skeleton.x, savedY = skeleton.y;
        try {
          var bw = Math.max(1, bounds.width), bh = Math.max(1, bounds.height);
          var fit = Math.min(w / bw, h / bh) * 0.94;
          if (!isFinite(fit) || fit <= 0) fit = 1;
          canvas.width = w;
          canvas.height = h;
          cam.viewportWidth = w;
          cam.viewportHeight = h;
          cam.zoom = 1 / fit;
          cam.position.x = bounds.x + bw / 2;
          cam.position.y = bounds.y + bh / 2;
          cam.update();
          skeleton.x = 0;
          skeleton.y = 0;
          paintWorld();
          return canvas.toDataURL('image/png');
        } catch (e) {
          return null;
        } finally {
          canvas.width = savedW;
          canvas.height = savedH;
          cam.position.x = savedCam.x;
          cam.position.y = savedCam.y;
          cam.zoom = savedCam.zoom;
          cam.viewportWidth = savedCam.vw;
          cam.viewportHeight = savedCam.vh;
          cam.update();
          skeleton.x = savedX;
          skeleton.y = savedY;
          paintWorld(); // 还原渲染一帧，避免下一次合成还停留在封面帧
        }
      },
      destroy: function () {
        destroyed = true;
        if (idleTimer) clearTimeout(idleTimer);
        if (subtitleTimer) clearTimeout(subtitleTimer);
        if (audio.current) { audio.current.pause(); audio.current = null; }
        if (ready) state.clearTracks();
      },
    };
  }

  root.SpineApp = { create: create };
})(typeof window !== 'undefined' ? window : this);
