<template>
  <div class="spine-panel-a" :class="{ collapsed, inline }">
    <div class="spine-panel-a-head">
      <span class="spine-panel-a-title"><span class="spine-panel-a-dot"></span>预览控制</span>
      <button
        v-if="!inline"
        class="spine-panel-a-collapse"
        :title="collapsed ? '展开控制面板' : '收起控制面板'"
        @click="collapsed = !collapsed"
      >
        {{ collapsed ? '⚙' : '−' }}
      </button>
    </div>
    <div v-if="!collapsed" class="spine-panel-a-body">
      <div class="spine-switch-row">
        <span class="spine-switch-ico" :class="{ on: interact }">
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>
        </span>
        <div class="spine-switch-txt">动作互动<small>点击身体各部位触发动作</small></div>
        <span class="spine-switch" :class="{ on: interact }" :title="interact ? '关闭互动' : '开启互动'" @click="$emit('update:interact', !interact)"></span>
      </div>
      <div class="spine-switch-row">
        <span class="spine-switch-ico" :class="{ on: voice }">
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>
        </span>
        <div class="spine-switch-txt">语音互动<small>点击播放对应语音音频</small></div>
        <span class="spine-switch" :class="{ on: voice }" :title="voice ? '关闭语音' : '开启语音'" @click="$emit('update:voice', !voice)"></span>
      </div>
      <div class="spine-switch-row">
        <span class="spine-switch-ico" :class="{ on: subtitle }">
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 15h4M14 15h3M7 11h10"/></svg>
        </span>
        <div class="spine-switch-txt">台词文本<small>播放语音时下方显示台词</small></div>
        <span class="spine-switch" :class="{ on: subtitle }" :title="subtitle ? '隐藏台词' : '显示台词'" @click="$emit('update:subtitle', !subtitle)"></span>
      </div>
      <div class="spine-switch-row">
        <span class="spine-switch-ico" :class="{ on: intro }">
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v4M12 18v4M2 12h4M18 12h4M4.9 4.9l2.8 2.8M16.3 16.3l2.8 2.8M4.9 19.1l2.8-2.8M16.3 7.7l2.8-2.8"/></svg>
        </span>
        <div class="spine-switch-txt">入场动画<small>进入立绘播放问候动作与台词</small></div>
        <span class="spine-switch" :class="{ on: intro }" :title="intro ? '关闭入场动画' : '开启入场动画'" @click="$emit('update:intro', !intro)"></span>
      </div>
      <div class="spine-switch-row">
        <span class="spine-switch-ico" :class="{ on: areas }">
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="7"/><path d="M12 2v4M12 18v4M2 12h4M18 12h4"/></svg>
        </span>
        <div class="spine-switch-txt">交互区域<small>叠加显示可点击热区（虚线为未激活）</small></div>
        <span class="spine-switch" :class="{ on: areas }" :title="areas ? '隐藏交互区域' : '显示交互区域'" @click="$emit('update:areas', !areas)"></span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'

defineProps({
  interact: { type: Boolean, default: true },
  voice: { type: Boolean, default: true },
  subtitle: { type: Boolean, default: true },
  intro: { type: Boolean, default: true },
  areas: { type: Boolean, default: false },
  inline: { type: Boolean, default: false },
})
defineEmits(['update:interact', 'update:voice', 'update:subtitle', 'update:intro', 'update:areas'])

const collapsed = ref(false)
</script>

<style scoped>
.spine-panel-a {
  width: 252px;
  background: linear-gradient(165deg, rgba(24, 34, 58, 0.78), rgba(10, 14, 24, 0.66));
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 18px;
  padding: 12px 12px 10px;
  box-shadow: 0 18px 50px rgba(0, 0, 0, 0.45), inset 0 1px 0 rgba(255, 255, 255, 0.08);
  backdrop-filter: blur(14px);
  font-family: var(--font-sans);
  transition: all 0.25s ease;
}
.spine-panel-a:not(.inline) {
  position: absolute;
  right: 16px;
  top: 50%;
  transform: translateY(-50%);
  z-index: 6;
}
.spine-panel-a.collapsed { width: 46px; padding: 10px 0; }
.spine-panel-a-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}
.spine-panel-a-title {
  display: flex;
  align-items: center;
  gap: 7px;
  font-family: var(--font-serif);
  font-size: 13px;
  letter-spacing: 3px;
  color: rgba(240, 235, 227, 0.92);
}
.spine-panel-a-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--gold);
  box-shadow: 0 0 8px rgba(201, 168, 76, 0.8);
}
.spine-panel-a-collapse {
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: 8px;
  background: rgba(0, 0, 0, 0.35);
  color: rgba(255, 255, 255, 0.7);
  font-size: 13px;
  line-height: 1;
  cursor: pointer;
  padding: 0;
  transition: all 0.2s ease;
}
.spine-panel-a-collapse:hover { background: rgba(255, 255, 255, 0.16); color: #fff; }
.spine-panel-a.collapsed .spine-panel-a-head { margin-bottom: 0; justify-content: center; }
.spine-panel-a.collapsed .spine-panel-a-body { display: none; }

.spine-switch-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 4px;
  border-radius: 12px;
  transition: background 0.2s ease;
}
.spine-switch-row:hover { background: rgba(255, 255, 255, 0.05); }
.spine-switch-ico {
  width: 26px;
  height: 26px;
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 9px;
  background: rgba(255, 255, 255, 0.07);
  color: rgba(240, 235, 227, 0.85);
}
.spine-switch-ico.on { color: var(--gold); background: rgba(201, 168, 76, 0.22); }
.spine-switch-txt {
  flex: 1;
  font-size: 12.5px;
  letter-spacing: 1px;
  color: rgba(240, 235, 227, 0.9);
}
.spine-switch-txt small {
  display: block;
  font-size: 10.5px;
  color: rgba(240, 235, 227, 0.5);
  letter-spacing: 0.5px;
  margin-top: 1px;
}
.spine-switch {
  position: relative;
  width: 38px;
  height: 22px;
  flex: none;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.14);
  cursor: pointer;
  transition: background 0.25s ease;
}
.spine-switch::after {
  content: '';
  position: absolute;
  top: 3px;
  left: 3px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.85);
  transition: transform 0.25s ease, background 0.25s ease;
}
.spine-switch.on { background: rgba(201, 168, 76, 0.55); }
.spine-switch.on::after { transform: translateX(16px); background: #fff8e1; }

/* 预览页内嵌在浅色侧栏里时换浅色描边 */
.spine-panel-a.inline {
  width: 100%;
  border: 1px solid var(--line);
  background: rgba(250, 248, 245, 0.9);
  box-shadow: none;
  backdrop-filter: none;
}
.spine-panel-a.inline .spine-panel-a-title { color: var(--ink, #3a3a3a); }
.spine-panel-a.inline .spine-switch-txt { color: var(--ink, #3a3a3a); }
.spine-panel-a.inline .spine-switch-txt small { color: var(--muted); }
.spine-panel-a.inline .spine-switch-ico { background: rgba(74, 111, 165, 0.08); color: var(--accent, #4a6fa5); }
.spine-panel-a.inline .spine-switch-ico.on { background: rgba(201, 168, 76, 0.18); color: #b8912f; }
</style>
