<template>
  <div ref="wrapRef" class="l2d-wrap">
    <canvas ref="canvasRef"></canvas>
  </div>
</template>

<script setup>
import { onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import { Application, Ticker } from 'pixi.js'
import { Live2DModel } from 'pixi-live2d-display-lipsyncpatch/cubism4'
import { assetUrl } from '../bridge'
import '../../../templates/wallpaper-layout.js'

const WL = window.WallpaperLayout

// pixi.js v7 单包已自动注册 TickerPlugin 与 interaction 插件，无需手动 registerPlugin；
// Live2DModel 的 autoUpdate 需要知道用哪个 Ticker。
Live2DModel.registerTicker(Ticker)

const props = defineProps({
  skin: { type: Object, required: true },
  animation: { type: String, default: '' },
  scale: { type: Number, default: 100 },
  offsetX: { type: Number, default: 0 },
  offsetY: { type: Number, default: 0 },
  alignment: { type: String, default: 'center' },
  // 鼠标追踪开关：角色视线/头部跟随鼠标（与壁纸端 WE 面板开关一致）
  mouseTrack: { type: Boolean, default: true },
})
const emit = defineEmits(['ready', 'error', 'animations', 'scaleChange', 'panChange'])

const wrapRef = ref(null)
const canvasRef = ref(null)

let app = null
let model = null
let motionList = []
let initW = 0
let initH = 0
let drag = null
let currentLabel = ''
let disposed = false // 卸载后置位：异步 load() 恢复时不再触碰已销毁的实例

async function load() {
  const base = assetUrl(props.skin.asset.dir)
  const modelPath = props.skin.asset.model
  const meta = await (await fetch(`${base}/${modelPath}`)).json()
  // 装束切换/退出预览发生在加载期间：组件已卸载，不能再碰 refs 与 emit
  if (disposed) return

  motionList = []
  for (const [group, items] of Object.entries(meta.FileReferences?.Motions || meta.Motions || {})) {
    for (let i = 0; i < items.length; i++) {
      const file = items[i].File || ''
      const label = file.split('/').pop().replace(/\.motion3\.json$/i, '')
      motionList.push({ label, group, index: i })
    }
  }
  emit('animations', motionList.map((m) => m.label))

  app = new Application({
    view: canvasRef.value,
    backgroundAlpha: 0,
    autoStart: true,
    resizeTo: wrapRef.value,
    antialias: true,
    // 透明 WebGL 画布在拖拽时容易在合成阶段留下上一帧的“虚影”，
    // preserveDrawingBuffer 可避免浏览器在合成后清空/残留缓冲
    preserveDrawingBuffer: true,
  })
  model = await Live2DModel.from(`${base}/${modelPath}`, {
    autoUpdate: true,
    // 关闭库自带的鼠标追踪/命中（autoHitTest 会监听 pointermove 并自动 focus），
    // 改为自定义 mousemove 实现，以便「鼠标追踪」开关能实时开/关。
    // （0.5.0 起 autoInteract 拆分为 autoHitTest + autoFocus）
    autoHitTest: false,
    autoFocus: false,
  })
  if (disposed) {
    // 卸载时 app 已 destroy，model 尚未挂到舞台：手动释放避免资源残留
    model.destroy?.()
    return
  }
  model.anchor.set(0.5, 0.5)
  app.stage.addChild(model)
  // pixi-live2d-display 会忽略动作自带的 Loop 标志（Meta.Loop 解析后从未应用），
  // 导致每个动作播完就触发“自动随机播放 Idle 姿势”，看起来像莫名换姿势。
  // 这里禁用库的自动随机 Idle：只有手动切换动画才会换姿势。
  const motionManager = model.internalModel?.motionManager
  if (motionManager) {
    motionManager.groups.idle = '__none__'
    motionManager.on('motionStart', (group, index) => {
      const m = motionManager.motionGroups?.[group]?.[index]
      // 循环与否按动作自带 Meta.Loop 决定
      const autoLoop = !!(m && m._motionData && m._motionData.loop)
      if (m && typeof m.setIsLoop === 'function') {
        m.setIsLoop(autoLoop)
        // 循环时禁止“循环重淡入”：Cubism SDK 在循环点会重置 fadeInStartTime
        //（_isLoopFadeIn 默认 true），fadeIn 权重瞬间归零再花 2 秒淡入，
        // 每循环一次就“顿一下回默认姿势再淡入”——这是 idle 卡顿感的来源。
        if (typeof m.setIsLoopFadeIn === 'function') m.setIsLoopFadeIn(false)
        // 禁用动作淡入淡出（默认 idle 组 fadeIn 长达 2 秒）：权重恒 1，
        // 循环点衔接平滑，消除“渐停/淡入”的卡顿感
        if (typeof m.setFadeInTime === 'function') m.setFadeInTime(0)
        if (typeof m.setFadeOutTime === 'function') m.setFadeOutTime(0)
      }
    })
  }
  // 记录加载时的稳定模型尺寸，作为基础缩放基准
  initW = model.width
  initH = model.height
  applyLayout()
  // 鼠标追踪：视线/头部跟随鼠标（pixi-live2d-display 的 model.focus 会按
  // 鼠标相对模型的方向驱动 ParamEyeBall/Angle/BodyAngle，与库内置行为一致）
  window.addEventListener('mousemove', onTrackMove)
  // 进场目标：父级选中的动画优先；否则取模型声明的 Idle 组（Cubism 标准
  // Groups.Idle），再退化为第一个动作。不依赖任何具体游戏的动作命名。
  const want = props.animation || ''
  const idleGroup = meta.Groups?.Idle
  const target =
    (want && motionList.some((m) => m.label === want))
      ? want
      : (idleGroup && motionList.find((m) => m.group === idleGroup)?.label) ||
        motionList[0]?.label ||
        ''
  currentLabel = target
  if (target) playMotion(target)
  emit('ready')
}

function applyLayout() {
  if (!model || !initW || !initH) return
  const w = wrapRef.value.clientWidth
  const h = wrapRef.value.clientHeight
  if (!w || !h) return
  const a = WL.anchor(props.alignment)
  const out = WL.l2dLayout(initW, initH, w, h, {
    scale: props.scale,
    offsetX: props.offsetX,
    offsetY: props.offsetY,
    alignX: a.x,
    alignY: a.y,
  })
  model.scale.set(out.sx)
  model.position.set(out.x, out.y)
}

function onResize() {
  applyLayout()
}

function playMotion(label) {
  if (!model || !motionList.length) return
  const target = motionList.find((m) => m.label === label)
  if (!target) return
  try {
    const im = model.internalModel
    // 切换前重置参数并停掉当前动作：避免旧动作的部件/表情参数残留，
    // 以及循环状态不复位导致同优先级新动作被库拒绝
    if (im?.parameters) {
      im.parameters.values.set(im.parameters.defaultValues)
    }
    im?.motionManager?.stopAllMotions?.()
    model.motion(target.group, target.index)
    currentLabel = target.label
  } catch (e) {
    /* 动作组不存在时忽略 */
  }
}

watch(
  () => [props.scale, props.offsetX, props.offsetY, props.alignment],
  () => applyLayout(),
)

watch(
  () => props.animation,
  (v) => {
    if (v) playMotion(v)
  },
)

// 鼠标追踪开关实时生效：关闭时视线/头部参数归零
watch(
  () => props.mouseTrack,
  (on) => setMouseTrack(on),
)

function onWheel(e) {
  e.preventDefault()
  const factor = Math.pow(1.1, -e.deltaY / 100)
  const next = WL.clampScale(props.scale * factor)
  emit('scaleChange', Math.round(next))
}

function onPanDown(e) {
  if (e.button !== 0) return
  drag = { x: e.clientX, y: e.clientY, ox: props.offsetX, oy: props.offsetY }
  wrapRef.value.style.cursor = 'grabbing'
}

// 鼠标追踪：鼠标移动时让角色视线/头部跟随（受「鼠标追踪」开关控制）
function onTrackMove(e) {
  if (!props.mouseTrack || !model) return
  model.focus(e.clientX, e.clientY)
}

// 切换鼠标追踪：关闭时把视线/头部参数平滑归零（focusController 目标置 0，
// 参数累加量归零后恢复动画原本的姿态）
function setMouseTrack(on) {
  if (on || !model) return
  try {
    model.internalModel?.focusController?.focus(0, 0, true)
  } catch (e) { /* 模型未就绪时忽略 */ }
}

// 拖拽/缩放事件绑定（幂等：先全部解绑再绑定，避免重复监听）
function bindPanEvents() {
  const wrap = wrapRef.value
  wrap.removeEventListener('wheel', onWheel)
  wrap.removeEventListener('mousedown', onPanDown)
  window.removeEventListener('mousemove', onMove)
  window.removeEventListener('mouseup', onUp)
  wrap.addEventListener('wheel', onWheel, { passive: false })
  wrap.addEventListener('mousedown', onPanDown)
  window.addEventListener('mousemove', onMove)
  window.addEventListener('mouseup', onUp)
  wrap.style.cursor = 'grab'
}

function onMove(e) {
  if (!drag) return
  drag.lastX = e.clientX
  drag.lastY = e.clientY
  const w = wrapRef.value.clientWidth || 1
  const h = wrapRef.value.clientHeight || 1
  const ox = WL.clampOffset(drag.ox + ((e.clientX - drag.x) / w) * 100)
  const oy = WL.clampOffset(drag.oy + ((e.clientY - drag.y) / h) * 100)
  const a = WL.anchor(props.alignment)
  const out = WL.l2dLayout(initW, initH, w, h, {
    scale: props.scale,
    offsetX: ox,
    offsetY: oy,
    alignX: a.x,
    alignY: a.y,
  })
  model.scale.set(out.sx)
  model.position.set(out.x, out.y)
}

function onUp() {
  if (!drag) return
  const w = wrapRef.value.clientWidth || 1
  const h = wrapRef.value.clientHeight || 1
  const lx = drag.lastX != null ? drag.lastX : drag.x
  const ly = drag.lastY != null ? drag.lastY : drag.y
  const ox = Math.round(WL.clampOffset(drag.ox + ((lx - drag.x) / w) * 100))
  const oy = Math.round(WL.clampOffset(drag.oy + ((ly - drag.y) / h) * 100))
  drag = null
  wrapRef.value.style.cursor = 'grab'
  emit('panChange', { x: ox, y: oy })
}

onMounted(() => {
  bindPanEvents()
  load().catch((e) => emit('error', e.message || String(e)))
  window.addEventListener('resize', onResize)
})

// keep-alive 缓存（图鉴页）下切走时暂停渲染：隐藏页继续跑 ticker 是纯资源浪费
onDeactivated(() => {
  if (app) app.ticker.stop()
})
onActivated(() => {
  if (app) app.ticker.start()
})

onBeforeUnmount(() => {
  disposed = true
  window.removeEventListener('resize', onResize)
  window.removeEventListener('mousemove', onMove)
  window.removeEventListener('mousemove', onTrackMove)
  window.removeEventListener('mouseup', onUp)
  if (app) app.destroy(true)
})

defineExpose({
  capture: () => {
    try {
      return canvasRef.value ? canvasRef.value.toDataURL('image/png') : null
    } catch (e) {
      return null
    }
  },
  // 导出壁纸封面：把整张立绘（按实际可渲染内容包围盒）完整装进 16:9 画布。
  // 不能直接截当前画面——用户可能放大/偏移导致裁切，且导出封面不应跟随拖拽。
  // 这里临时把渲染器尺寸切到封面尺寸、按内容居中缩放渲染一帧后立刻还原，
  // 整个流程同步执行，浏览器只会在结束后合成一次，预览不会闪。
  captureCover: () => {
    if (!app || !model || !canvasRef.value) return null
    const width = 1280
    const height = 720
    const content = measureContentBounds()
    const contentW = content?.width || initW
    const contentH = content?.height || initH
    if (!contentW || !contentH) return null
    const centerX = content?.centerX || 0
    const centerY = content?.centerY || 0
    const renderer = app.renderer
    const saved = {
      width: renderer.width,
      height: renderer.height,
      scaleX: model.scale.x,
      scaleY: model.scale.y,
      x: model.position.x,
      y: model.position.y,
    }
    try {
      // 留 4% 边距：头/脚/裙摆不贴边，WE 缩略图裁切也不容易切到内容。
      const scale = Math.min(width / contentW, height / contentH) * 0.96
      renderer.resize(width, height)
      model.scale.set(scale)
      model.position.set(width / 2 - centerX * scale, height / 2 + centerY * scale)
      app.render()
      return canvasRef.value.toDataURL('image/png')
    } catch (e) {
      return null
    } finally {
      renderer.resize(saved.width, saved.height)
      model.scale.set(saved.scaleX, saved.scaleY)
      model.position.set(saved.x, saved.y)
      app.render()
    }
  },
})

// 以当前实际可见 drawable 的顶点包围盒作为封面取景基准。
// 部分模型声明的画布远大于/小于真实几何范围，直接用 model.width/height 会让
// 封面出现大片空白或裁掉头脚。过滤「超大 + 顶点极少」的全屏特效四边形，
// 避免背景/相机层把取景拉飞；正常大网格（披风/裙摆）顶点数多，会计入。
function measureContentBounds() {
  const internal = model?.internalModel
  const drawables = internal?.coreModel?.getModel?.()?.drawables
  if (!drawables?.count) return null
  let left = Infinity
  let right = -Infinity
  let top = Infinity
  let bottom = -Infinity
  for (let i = 0; i < drawables.count; i++) {
    if (!(drawables.dynamicFlags[i] & 1) || drawables.opacities[i] <= 0) continue
    const vertices = drawables.vertexPositions[i]
    if (!vertices) continue
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity
    for (let j = 0; j + 1 < vertices.length; j += 2) {
      const x = vertices[j]
      const y = vertices[j + 1]
      if (!Number.isFinite(x) || !Number.isFinite(y)) continue
      minX = Math.min(minX, x)
      maxX = Math.max(maxX, x)
      minY = Math.min(minY, y)
      maxY = Math.max(maxY, y)
    }
    const w = maxX - minX
    const h = maxY - minY
    if ((w > 50 || h > 50) && vertices.length / 2 <= 16) continue
    left = Math.min(left, minX)
    right = Math.max(right, maxX)
    top = Math.min(top, minY)
    bottom = Math.max(bottom, maxY)
  }
  if (!Number.isFinite(left) || right <= left || bottom <= top) return null
  const ppu = internal.pixelsPerUnit || 1
  return {
    width: (right - left) * ppu,
    height: (bottom - top) * ppu,
    centerX: ((left + right) / 2) * ppu,
    centerY: ((top + bottom) / 2) * ppu,
  }
}
</script>

<style scoped>
.l2d-wrap {
  width: 100%;
  height: 100%;
  position: relative;
  touch-action: none;
}

.l2d-wrap canvas {
  width: 100%;
  height: 100%;
}
</style>
