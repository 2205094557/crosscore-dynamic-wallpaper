<template>
  <div class="spine-preview">
    <canvas ref="canvasRef" class="spine-canvas"></canvas>
    <div ref="areaLayerRef" class="spine-areas"></div>
    <div v-if="areasEmpty" class="spine-areas-empty">当前装束没有交互热区配置（商店皮才有）</div>
    <transition name="fade">
      <div v-if="subtitle" class="spine-subtitle">{{ subtitle }}</div>
    </transition>
    <div class="spine-tip" v-if="ready && interact">点击立绘互动</div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { assetUrl } from '../bridge'

const props = defineProps({
  skin: { type: Object, required: true },
  scale: { type: Number, default: 100 },
  offsetX: { type: Number, default: 0 },
  offsetY: { type: Number, default: 0 },
  alignment: { type: String, default: 'center' },
  voice: { type: Boolean, default: true },
  interact: { type: Boolean, default: true },
  subtitle: { type: Boolean, default: true },
  intro: { type: Boolean, default: true },
  areas: { type: Boolean, default: false },
  palette: { type: Array, default: () => [] },
  bgMode: { type: String, default: 'auto' },
})
const emit = defineEmits(['error', 'animations', 'scale-change', 'pan-change'])

const canvasRef = ref(null)
const areaLayerRef = ref(null)
const ready = ref(false)
const subtitle = ref('')
const areasEmpty = ref(false)
let app = null
let panBase = null
// 当前动作配置与热区叠加层状态（叠加层直接操作 DOM，避免每帧触发响应式更新）
let currentActions = null
let areaEls = []
let areaRaf = 0

const voiceId = computed(() => props.skin?.voice_id || '')
const assetDir = computed(() => {
  const dir = props.skin?.asset?.dir || ''
  return dir ? assetUrl(`${dir}/`) : ''
})
const voiceBase = computed(() => (voiceId.value ? assetUrl(`voice/${voiceId.value}/`) : ''))

async function loadVoice() {
  if (!voiceId.value) return null
  try {
    const resp = await fetch(assetUrl(`voice/${voiceId.value}/voice.json`), { cache: 'no-store' })
    if (!resp.ok) return null
    const data = await resp.json()
    return { lines: data.lines || [], base: voiceBase.value }
  } catch (e) {
    return null
  }
}

async function loadActions(voice) {
  const dir = props.skin?.asset?.dir || ''
  if (!dir) return null
  try {
    const [actResp, metaResp] = await Promise.all([
      fetch(assetUrl(`${dir}/actions.json`), { cache: 'no-store' }),
      fetch(assetUrl(`${dir}/spine.json`), { cache: 'no-store' }),
    ])
    // dev 下 /@fs 命中不存在的文件会 SPA-fallback 成 200+HTML，json() 必炸：
    // 各自容错，绝不能让解析失败跳过下面的 skins.json actions 后备
    let raw = null
    if (actResp.ok) {
      try { raw = await actResp.json() } catch (e) { raw = null }
    }
    // 老下载目录可能没有 actions.json：退回元数据里的 actions
    if ((!raw || !raw.items || !raw.items.length) && props.skin?.actions) raw = props.skin.actions
    let meta = {}
    if (metaResp.ok) {
      try { meta = await metaResp.json() } catch (e) { meta = {} }
    }
    if (!raw || !raw.items || !raw.items.length) return null
    const ui = meta.ui || { pos: [0, 0], scale: 1 }
    const px = Number(ui.pos?.[0] || 0)
    const py = Number(ui.pos?.[1] || 0)
    const scale = Number(ui.scale || 1) || 1
    const files = {}
    for (const line of (voice?.lines || [])) files[line.cue] = line.file
    const items = []
    for (const item of raw.items) {
      if (!item.sName || item.sName === 'in') continue
      const rects = (item.areas || [])
        .filter((a) => Array.isArray(a) && a.length >= 4)
        .map((a) => [
          (Number(a[0]) - px) / scale,
          (Number(a[1]) - py) / scale,
          Number(a[2]) / scale,
          Number(a[3]) / scale,
          Number(a[4] || 0),
        ])
      if (!rects.length) continue
      const audio = []
      for (const aid of item.audioId || []) {
        const info = (raw.audio || {})[String(aid)] || {}
        const file = files[info.cue]
        if (file) audio.push({ file, text: info.text || '' })
      }
      const entry = {
        name: item.sName,
        type: item.sType,
        hideTxt: Number(item.hideTxt || 0),
        rects,
        audio,
      }
      const content = item.content || {}
      if (content.activation) {
        entry.activation = Object.fromEntries(
          Object.entries(content.activation).map(([k, v]) => [String(k), Number(v)]),
        )
      }
      if (content.isHide !== undefined) entry.isHide = Number(content.isHide)
      if (content.changeIdle) {
        entry.changeIdle = { anim: String(content.changeIdle[0]), delay: Number(content.changeIdle[1] || 0) }
      }
      if (content.clicks) entry.clicks = content.clicks.map(Number)
      if (content.clickTime) entry.clickTime = Number(content.clickTime)
      if (item.gesture) entry.gesture = Number(item.gesture)
      if (content.gestureDatas) {
        entry.gestureDatas = {
          speed: Number(content.gestureDatas.speed || 2),
          limitPerc: Number(content.gestureDatas.limitPerc || 1),
          stopTime: Number(content.gestureDatas.stopTime || 0),
        }
      }
      items.push(entry)
    }
    return items.length ? { items } : null
  } catch (e) {
    return null
  }
}

async function mountApp() {
  if (!canvasRef.value || !assetDir.value) return
  if (typeof window.SpineApp === 'undefined' || typeof window.spine === 'undefined') {
    emit('error', 'Spine 运行时未加载')
    return
  }
  destroy()
  const voice = props.voice ? await loadVoice() : null
  const actions = await loadActions(voice)
  currentActions = actions
  const palette = props.palette && props.palette.length
    ? props.palette
    : ['#1d3f6e', '#0e1b33', '#13294d', '#0b0f17']
  app = window.SpineApp.create(canvasRef.value, {
    prefix: assetDir.value,
    atlasPath: 'character.atlas',
    jsonPath: 'character.json',
    voice,
    actions,
    background: { palette, mode: props.bgMode === 'auto' ? 'monet' : props.bgMode },
    layout: {
      scale: props.scale,
      offsetX: props.offsetX,
      offsetY: props.offsetY,
      alignment: props.alignment,
    },
    subtitleEl: null,
    onSubtitle: (text) => { subtitle.value = text || '' },
    onDebugLog: (msg) => {
      const log = window.__crossLog || (window.__crossLog = [])
      log.push(`[${(performance.now() / 1000).toFixed(2)}] ${msg}`)
      if (log.length > 400) log.shift()
    },
    options: {
      voice: props.voice,
      interact: props.interact,
      subtitle: props.subtitle,
      intro: props.intro,
      volume: 0.8,
      idleInterval: 0,
    },
    onPanStart: () => { panBase = { x: props.offsetX, y: props.offsetY } },
    onPan: (dx, dy) => {
      if (!panBase) return
      const clamp = (v) => Math.max(-100, Math.min(100, v))
      // 保留 1 位小数：取整成整数百分点会在大画布上产生 ~10px 的阶梯跳变
      emit('pan-change', {
        x: Math.round(clamp(panBase.x + dx) * 10) / 10,
        y: Math.round(clamp(panBase.y + dy) * 10) / 10,
      })
    },
    onZoom: (factor) => {
      const next = Math.max(20, Math.min(800, props.scale * factor))
      emit('scale-change', Math.round(next))
    },
    onReady: (info) => {
      ready.value = true
      emit('animations', info.animations || [])
    },
    onError: (message) => emit('error', message),
  })
  if (typeof window !== 'undefined') window.__crossApp = app
}

function destroy() {
  if (app) {
    app.destroy()
    app = null
  }
  ready.value = false
  currentActions = null
  areasEmpty.value = false
  clearAreas()
  if (areaRaf) {
    cancelAnimationFrame(areaRaf)
    areaRaf = 0
  }
}

// ---- 交互区域叠加层：按帧把热区（世界坐标矩形）同步成屏幕坐标的 DOM ----
function clearAreas() {
  areaEls = []
  const layer = areaLayerRef.value
  if (layer) layer.innerHTML = ''
}

function buildAreas() {
  const layer = areaLayerRef.value
  if (!layer) return
  clearAreas()
  const active = props.areas && ready.value
  // 没有动作配置的装束没有热区可画：给出明确提示，别让开关看起来像坏了
  areasEmpty.value = active && !currentActions
  if (!active || !currentActions) return
  for (const item of currentActions.items) {
    for (const rect of item.rects) {
      const el = document.createElement('div')
      el.className = 'spine-area'
      el.__rect = rect
      el.__name = item.name
      el.__dormant = null
      el.innerHTML = `<span>${item.name}</span>`
      layer.appendChild(el)
      areaEls.push(el)
    }
  }
  syncAreas()
}

function syncAreas() {
  if (!areaEls.length || !app || !canvasRef.value) return
  let info
  try { info = app.debug() } catch (e) { return }
  if (!info || !info.camera) return
  const cssW = canvasRef.value.clientWidth || 1
  const cssH = canvasRef.value.clientHeight || 1
  // 相机以设备像素计，换算成 CSS 像素再定位
  const zoom = (info.camera.zoom || 1) * ((info.canvas ? info.canvas[0] : cssW) / cssW)
  // debug().visible 是当前激活的动作名（activation 未开启的算隐藏态）
  const visNames = Array.isArray(info.visible) ? new Set(info.visible.filter(Boolean)) : null
  for (const el of areaEls) {
    const r = el.__rect
    const sx = (r[0] - info.camera.x) / zoom + cssW / 2
    const sy = cssH / 2 - (r[1] - info.camera.y) / zoom
    const dormant = visNames ? !visNames.has(el.__name) : false
    if (dormant !== el.__dormant) {
      el.__dormant = dormant
      el.classList.toggle('dormant', dormant)
    }
    const w = r[2] / zoom
    const h = r[3] / zoom
    // 太小的热区放不下动作名徽标，藏掉避免糊成一团
    el.classList.toggle('tiny', w < 30 || h < 15)
    el.style.width = `${w}px`
    el.style.height = `${h}px`
    // 热区旋转为正角度逆时针（Unity UI），屏幕 y 翻转后 CSS 取负角
    el.style.transform = `translate(${sx}px, ${sy}px) translate(-50%, -50%) rotate(${-(r[4] || 0)}deg)`
  }
}

function areaLoop() {
  if (!props.areas || !ready.value || !app) {
    areaRaf = 0
    return
  }
  syncAreas()
  areaRaf = requestAnimationFrame(areaLoop)
}

function applyLayout() {
  if (app) {
    app.setLayout({
      scale: props.scale,
      offsetX: props.offsetX,
      offsetY: props.offsetY,
      alignment: props.alignment,
    })
  }
}

function captureCover() {
  if (!app) return null
  return app.captureCover(1280, 720)
}

onMounted(mountApp)
onBeforeUnmount(destroy)

watch(() => props.skin?.key, () => { destroy(); mountApp() })
watch(
  () => [props.scale, props.offsetX, props.offsetY, props.alignment],
  applyLayout,
)
watch(() => props.voice, (on) => { if (app) app.setOptions({ voice: on }); if (on && !app) mountApp() })
watch(() => props.interact, (on) => { if (app) app.setOptions({ interact: on }) })
watch(() => props.subtitle, (on) => { if (app) app.setOptions({ subtitle: on }) })
watch([() => props.areas, ready], ([on]) => {
  buildAreas()
  if (on && ready.value && app && !areaRaf) areaRaf = requestAnimationFrame(areaLoop)
  if (!on && areaRaf) {
    cancelAnimationFrame(areaRaf)
    areaRaf = 0
  }
})
watch(
  () => [props.palette, props.bgMode],
  () => {
    if (!app) return
    app.setBackground({
      palette: props.palette && props.palette.length ? props.palette : ['#1d3f6e', '#0e1b33', '#13294d', '#0b0f17'],
      mode: props.bgMode === 'auto' ? 'monet' : props.bgMode,
    })
  },
)

defineExpose({ captureCover })
</script>

<style scoped>
.spine-preview {
  position: absolute;
  inset: 0;
  overflow: hidden;
}
.spine-canvas {
  display: block;
  width: 100%;
  height: 100%;
  cursor: pointer;
}
.spine-subtitle {
  position: absolute;
  left: 50%;
  bottom: 6%;
  transform: translateX(-50%);
  max-width: 82%;
  padding: 8px 16px;
  border: 1px solid rgba(255, 255, 255, 0.18);
  background: rgba(8, 12, 20, 0.6);
  color: #f2f6ff;
  font-size: 14px;
  line-height: 1.55;
  text-align: center;
  text-shadow: 0 1px 4px rgba(0, 0, 0, 0.8);
  pointer-events: none;
}
.spine-tip {
  position: absolute;
  right: 12px;
  bottom: 10px;
  font-size: 11px;
  color: rgba(255, 255, 255, 0.42);
  letter-spacing: 0.06em;
  pointer-events: none;
}
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.25s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>

<style>
/*
 * 交互区域叠加层的矩形是运行时 createElement 出来的，带不上 scoped 的 data-v
 * 属性，这些规则必须全局生效（类名都有 spine- 前缀，不会外泄冲突）。
 */
.spine-areas {
  position: absolute;
  inset: 0;
  overflow: hidden;
  pointer-events: none;
  z-index: 2;
}
.spine-areas-empty {
  position: absolute;
  top: 12px;
  left: 50%;
  transform: translateX(-50%);
  padding: 4px 12px;
  border-radius: 999px;
  background: rgba(8, 12, 20, 0.55);
  border: 1px solid rgba(255, 255, 255, 0.14);
  color: rgba(255, 255, 255, 0.68);
  font-size: 11px;
  letter-spacing: 0.5px;
  white-space: nowrap;
  pointer-events: none;
  z-index: 2;
}
.spine-area {
  position: absolute;
  left: 0;
  top: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  border: 1.5px solid rgba(255, 205, 112, 0.5);
  border-radius: 8px;
  background: linear-gradient(135deg, rgba(255, 190, 70, 0.14), rgba(255, 150, 30, 0.07));
  box-shadow: 0 0 10px rgba(255, 178, 44, 0.26), inset 0 0 10px rgba(255, 178, 44, 0.14);
  animation: spine-area-float 1.8s ease-in-out infinite;
}
/* 呼吸式透明度：composited 动画，多个矩形也便宜 */
@keyframes spine-area-float {
  0%, 100% { opacity: 0.45; }
  50% { opacity: 0.85; }
}
.spine-area.dormant {
  border: 1.5px dashed rgba(255, 255, 255, 0.38);
  background: rgba(255, 255, 255, 0.05);
  box-shadow: none;
  animation: none;
}
.spine-area span {
  max-width: calc(100% - 8px);
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 10px;
  font-weight: 600;
  line-height: 1.5;
  letter-spacing: 0.5px;
  white-space: nowrap;
  padding: 0 6px;
  border-radius: 999px;
  background: rgba(10, 14, 22, 0.42);
  color: rgba(255, 227, 160, 0.8);
}
.spine-area.tiny span { display: none; }
.spine-area.dormant span { color: rgba(255, 255, 255, 0.5); background: rgba(10, 14, 22, 0.3); }
</style>
