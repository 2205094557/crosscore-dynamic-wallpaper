import { Application, Ticker } from 'pixi.js'
import { Live2DModel } from 'pixi-live2d-display-lipsyncpatch/cubism4'

// 布局统一模块由 wallpaper-layout.js 注入 window.WallpaperLayout
const WL = window.WallpaperLayout

// pixi.js v7 单包已自动注册 TickerPlugin 与 interaction 插件，无需手动 registerPlugin；
// Live2DModel 的 autoUpdate 需要知道用哪个 Ticker。
Live2DModel.registerTicker(Ticker)

let model = null
let app = null
let initW = 0
let initH = 0
let scale = 100
let ox = 0
let oy = 0
let alignment = 'center'
let meta = null
let lastMotionLabel = ''
let fpsLimit = 60
let layoutControl = null
// WE 面板开关（右侧属性面板）：鼠标追踪开/关（视线/头部跟随鼠标）
let mouseTrackOn = true

function applyLayout() {
  if (!model || !initW || !initH) return
  const a = WL.anchor(alignment)
  const out = WL.l2dLayout(initW, initH, window.innerWidth, window.innerHeight, {
    scale,
    offsetX: ox,
    offsetY: oy,
    alignX: a.x,
    alignY: a.y,
  })
  model.scale.set(out.sx)
  model.position.set(out.x, out.y)
}

// 鼠标追踪：鼠标移动时让角色视线/头部跟随（受 WE 面板「鼠标追踪」开关控制）
function onMouseMove(e) {
  if (!mouseTrackOn || layoutControl?.state.adjustSize || !model) return
  model.focus(e.clientX, e.clientY)
}

function syncInputMode() {
  const adjusting = !!layoutControl?.state.adjustSize
  if (!mouseTrackOn || adjusting) {
    try {
      model?.internalModel?.focusController?.focus(0, 0, true)
    } catch (e) { /* 模型未就绪时忽略 */ }
  }
}

// 切换鼠标追踪：关闭或调整位置时把视线/头部参数归零
function setMouseTrack(on) {
  mouseTrackOn = on
  syncInputMode()
}

function applyFpsLimit(value) {
  const fps = Number(value)
  fpsLimit = Number.isFinite(fps) ? Math.min(200, Math.max(15, fps)) : 60
  if (app) app.ticker.maxFPS = fpsLimit
}

// 收集 model3.json 里全部动作条目：[{ group, stem }]（stem 为动作文件名去 .motion3.json）
function collectMotionEntries(motions) {
  const entries = []
  for (const g of Object.keys(motions || {})) {
    for (const it of motions[g] || []) {
      const stem = (it.File || '').split('/').pop().replace(/\.motion3\.json$/i, '')
      if (stem && !entries.some((e) => e.stem === stem)) entries.push({ group: g, stem })
    }
  }
  return entries
}

// 默认动作：优先模型声明的 Idle 组（Cubism 标准 Groups.Idle），否则第一个动作。
// 不依赖任何具体游戏的动作命名。
function defaultMotionLabel(entries) {
  const idleGroup = meta?.Groups?.Idle
  if (idleGroup) {
    const hit = entries.find((e) => e.group === idleGroup)
    if (hit) return hit.stem
  }
  const idleNamed = entries.find((e) => /^idle/i.test(e.stem))
  return (idleNamed || entries[0] || {}).stem || ''
}

async function start() {
  const cfg = window.__L2D_CONFIG
  const init = window.__L2D_INIT || {}
  scale = WL.clampScale(init.scale != null ? init.scale : 100)
  ox = WL.clampOffset(init.ox != null ? init.ox : 0)
  oy = WL.clampOffset(init.oy != null ? init.oy : 0)
  alignment = init.alignment || 'center'
  layoutControl = WL.layoutController(document.getElementById('canvas'), {
    scale,
    offsetX: ox,
    offsetY: oy,
    alignment,
    storageKey: init.storageKey || '',
    onChange: (state) => {
      scale = state.scale
      ox = state.offsetX
      oy = state.offsetY
      alignment = state.alignment
      applyLayout()
    },
    onModeChange: syncInputMode,
  })
  scale = layoutControl.state.scale
  ox = layoutControl.state.offsetX
  oy = layoutControl.state.offsetY
  alignment = layoutControl.state.alignment
  const wantAnim = init.anim || ''
  mouseTrackOn = init.track !== false

  app = new Application({
    view: document.getElementById('canvas'),
    backgroundAlpha: 0,
    autoStart: true,
    resizeTo: window,
    antialias: true,
  })
  applyFpsLimit(fpsLimit)
  model = await Live2DModel.from(cfg.model, {
    autoUpdate: true,
    ticker: app.ticker,
    // 关闭库自带的鼠标追踪/命中（autoHitTest 会监听 pointermove 并自动 focus），
    // 改为自定义 mousemove 实现，以便 WE 面板「鼠标追踪」开关能实时开/关。
    // （0.5.0 起 autoInteract 拆分为 autoHitTest + autoFocus）
    autoHitTest: false,
    autoFocus: false,
  })
  model.anchor.set(0.5, 0.5)
  app.stage.addChild(model)
  // pixi-live2d-display 会忽略动作自带的 Loop 标志，动作播完会自动随机切 Idle 姿势。
  // 这里禁用库的自动随机 Idle：壁纸只会一直播放当前动作；循环与否由动作自带 Meta.Loop 决定。
  const motionManager = model.internalModel?.motionManager
  if (motionManager) {
    motionManager.groups.idle = '__none__'
    motionManager.on('motionStart', (group, index) => {
      const m = motionManager.motionGroups?.[group]?.[index]
      const autoLoop = !!(m && m._motionData && m._motionData.loop)
      if (m && typeof m.setIsLoop === 'function') {
        m.setIsLoop(autoLoop)
        // 循环时禁止“循环重淡入”：Cubism SDK 在循环点会重置 fadeInStartTime
        //（_isLoopFadeIn 默认 true），fadeIn 权重瞬间归零再花 2 秒淡入，
        // 每循环一次就“顿一下回默认姿势再淡入”——这是 idle 卡顿感的来源。
        if (typeof m.setIsLoopFadeIn === 'function') m.setIsLoopFadeIn(false)
        // 禁用动作淡入淡出：权重恒 1，循环点衔接平滑，消除“渐停/淡入”的卡顿感
        if (typeof m.setFadeInTime === 'function') m.setFadeInTime(0)
        if (typeof m.setFadeOutTime === 'function') m.setFadeOutTime(0)
      }
    })
  }
  initW = model.width
  initH = model.height
  applyLayout()
  // 鼠标追踪：视线/头部跟随鼠标（pixi-live2d-display 的 model.focus 会按
  // 鼠标相对模型的方向驱动 ParamEyeBall/Angle/BodyAngle，与库内置行为一致）
  window.addEventListener('mousemove', onMouseMove)
  try {
    meta = await (await fetch(cfg.model)).json()
    const entries = collectMotionEntries(meta?.FileReferences?.Motions || meta?.Motions || {})
    lastMotionLabel = (wantAnim && entries.some((e) => e.stem === wantAnim))
      ? wantAnim
      : defaultMotionLabel(entries)
    if (lastMotionLabel) playMotionByLabel(lastMotionLabel)
  } catch (e) { /* 无动作不影响显示 */ }
}

// 按动作文件名播放。
// 切换前重置参数并停掉当前动作（避免旧动作的部件/表情参数残留、
// 以及强制循环后 NORMAL 优先级不复位导致同优先级新动作被库拒绝）。
function playMotionByLabel(label) {
  if (!label || !meta || !model) return false
  const entries = collectMotionEntries(meta?.FileReferences?.Motions || meta?.Motions || {})
  const hit = entries.find((e) => e.stem === label)
  if (!hit) return false
  const groups = meta?.FileReferences?.Motions || meta?.Motions || {}
  const idx = (groups[hit.group] || []).findIndex((m) => {
    const stem = (m.File || '').split('/').pop().replace(/\.motion3\.json$/i, '')
    return stem === label
  })
  if (idx < 0) return false
  try {
    const im = model.internalModel
    if (im?.parameters) {
      im.parameters.values.set(im.parameters.defaultValues)
    }
    im?.motionManager?.stopAllMotions?.()
    model.motion(hit.group, idx)
  } catch (e) {
    return false
  }
  lastMotionLabel = label
  return true
}

// WE 首次推送用于恢复该壁纸路径下保存的布局与用户偏好；后续推送实时生效。
// layoutController 在稳定 localStorage 布局存在时保留它，否则按首推中实际存在的字段采纳，
// 不假定首推一定包含 alignment。
window.wallpaperPropertyListener = {
  applyUserProperties: function (properties) {
    try {
      if (properties.targetfps) applyFpsLimit(properties.targetfps.value)
      if (layoutControl) layoutControl.applyUserProperties(properties)
      if (properties.mousetrack) setMouseTrack(!!properties.mousetrack.value)
      if (model) applyLayout()
    } catch (e) {}
  },
}

window.__captureCanvasDataURL = function () {
  try {
    return document.getElementById('canvas').toDataURL('image/png')
  } catch (e) {
    return ''
  }
}

window.addEventListener('resize', () => { if (model) applyLayout() })
start().catch((e) => { document.title = 'ERR ' + e.message })
