# AGENTS.md — 交错战线动态壁纸工具（crosscore-dynamic-wallpaper）

从 `D:\codex\dynamic-wallpaper-framework` 派生（通用导出/封面/WE 集成契约见框架
AGENTS.md，本文件只记录交错战线专属层与实测结论）。

家族规则：通用层 bug/功能改框架再回灌；游戏专属层（sources/extractors/图鉴/品牌）
留在本项目。

## 一、资源协议（2026-11 实测）

### 1. CDN

```text
基础地址  https://cdn.megagamelog.com/cross/release/{platform}/curr/
platform  android | ios（两端资源一致，默认 android）
```

- **关键坑**：镜像清单里常见 `curr_1` / `curr_new_1` 通道，但 `curr_1` 只有 `Custom/`，
  `sounds/` 与 `videos/` 全部 404；只有 **`curr`** 通道同时提供 `Custom/` 与 `sounds/`。
- 资源键（`Custom/...`、`sounds/...`）整段拼在基础地址后面，大小写敏感。
- 无签名/Token/Referer 校验，普通 GET 即可。
- 官方 ilist 已失效（1 字节）；清单缓存来自镜像
  `R2iCecho/Crosscore_Assets_curr_1/lists/ilist.txt` → `resources/metadata/asset_list.txt`。

### 2. AssetBundle

- 文件头被改写：真正内容从**第二个** `UnityFS` 开始（`core/unityfs.strip_fake_header`）。
- 立绘：`Custom/textures_bigs_character_{img}` → Texture2D（RGBA），同 key 加 `_face`
  是表情差分。
- Spine：`Custom/prefabs_spine_{name}`（基础）与
  `prefabs_spine_{role}_{skin|break|synchro}_..._spine`（装束变体）。
  bundle 内是 spine-unity 的 SkeletonGraphic：TextAsset `*_character.json` +
  `*.atlas` + 一张 Texture2D，**Spine 4.2**。包内还有特效骨架（effect_*），
  取体积最大的 skeleton JSON 才是角色本体。
- 纹理**不要翻转**：UnityPy 的 `texture.image` 已是正确朝向，翻转会让网格错位。

### 3. 语音（CriWare ACB）

- 路径 `sounds/{cue_sheet}`，如 `sounds/cv/alps.acb`、`sounds/cv/cv_skin/Alps_skin108.acb`。
- cue 名形如 `alps_25`，**不等于 stream 序号**（vgmstream 按名字排序输出流），
  必须先 `vgmstream-cli -m -s N` 逐条读 `stream name` 建映射（`core/voice.py` 有缓存）。
- 沿用 `voice-cue-pipeline` skill 的坑：`CREATE_NO_WINDOW`、
  `encoding='utf-8', errors='replace'`、解码前确认是 `.acb`。
- 台词表来自游戏 Lua `Sound` 配置：`group` = voiceID、`cue_name`、`name`（分类）、
  `script1`（文本）。皮肤语音分类带装束后缀（`接触1（夜幕幽冥）`），同步时剥离。
- 只有中文语音（`CfgSoundLanguage` 全 cn）。

## 二、元数据（tools/sync_crosscore_metadata.py）

数据源是社区整理的 Lua 配置（GitHub `AXiX-official/CrossCoreLuascripts`），
同步时按需下载到 `resources/metadata/cfg/`：

| 文件 | 用途 |
|---|---|
| `cfgCfgCardRole.lua` | 角色主表（sName/sAliasName/eName/sTeam/声优） |
| `cfgCardData.lua` | 品质 quality(1-6)、职业 career(1-4) |
| `cfgcharacter.lua` | 装束记录（role_id/img/skinType/desc/voiceID） |
| `cfgSound*.lua` | 语音行（cue/分类/文本/sheet） |
| `cfgCfgTeamEnum.lua` | 阵营名 |
| `cfgCfgRoleEnum.lua` | 职业名 |

**游戏更新后怎么拿新角色**：配置表和清单都是缓存文件，`_download` 默认跳过已存在
的文件——不带参数跑同步**拿不到新角色**。正确姿势：

```text
python tools/sync_crosscore_metadata.py --refresh   # 重下配置+清单，全量重探 ~5000 个 key（通常 5~10 分钟）
python tools/build_local_index.py                   # 重建本地索引（后端执行时会自动跑）
```

**注意 `_read_internation()` 只能调一次**：曾把它内联进推导式的 `if` 条件
（`{k for k in verified if k in _read_internation(refresh)}`），refresh=True 时
会按每个候选 key 重复下载几千次清单，同步永远跑不完。必须先把结果存进局部变量再
做集合过滤。

界面入口：设置 → 数据更新 → 「更新资源清单」（或「同步图鉴数据」，两者是同一条链路，
后端已固定带 `--refresh`，跑完自动重建索引并热重载）。手工新增装束/装束补数据后
也要跑一次 `build_local_index`，否则图鉴不显示。
**新角色能否出现取决于社区配置仓库是否已跟进**（`AXiX-official/CrossCoreLuascripts`），
CDN 通道/清单镜像地址变了则改 `sync_crosscore_metadata.py` 顶部的常量与
`plugins/sources/crosscore_cdn.py` 的基础地址。

**反和谐/国际服变体**：国服 cfg 的立绘 key 常与国际服差一个末尾序号
（如 `alps031` ↔ `alps03`）。同步时会探测 ±1 位序号的变体，并优先采用
`resources/metadata/internation_list.txt`（社区 Internationalizer 清单）里列出的
未和谐 key；不在清单里的皮肤保持 cfg 原样（可能是国服独有改图，无法凭空还原）。
语音不受影响（只有中文一套）。

**清单镜像不等于全量**：`asset_list.txt` 只覆盖部分资源（商店皮、新皮肤经常缺项，
且 key 大小写与 cfg 不一致）。同步时按 cfg 生成候选 key（draw 大小写两版 +
`prefabs_spine_{role}_skin_{name}{NN}` / `skin{NN}_{name}` / `break_` / `synchro_`），
用 **HEAD 探测 CDN** 确认存在性，结果缓存在 `resources/metadata/probe_cache.json`
（离线可复用；`--refresh` 会重探）。只认探测 200 的 key，宁可少认不猜错。

产物（**characters/skins 是裸数组**，前端 `mock/api.js` 直接迭代；voices 是对象）：

- `characters.json`：name(常用名)/alias/title_name/english_name/role_id/faction/
  career/quality/rarity/grade/voice_id/voice_sheet
- `skins.json`：character/bundle(=role_id)/name(装束 desc)/painting(文件标识)/
  type(spine|static)/series/draw_key/spine_key/voice_id/voice_sheet
- `voices.json`：voice_id → {sheet, lines:[{cue,category,text}]}

**重音折叠**：角色英文名含重音（`Marée Rouge`）时，CDN key 用 `mareerouge`
（é→e）而不是删掉重音（`marerouge`）。`_ascii_fold` 用 NFKD 展开再拼回，曾因此
误判赤溟没有 Spine。

装束 → Spine 包匹配（`assign_spine_keys`）**优先用 cfgcharacter.lua 的 `l2dName`**
（官方写死的 spine 包名，探测存在就 1:1 直配；可能带拼写错误/空格，如宙斯·棋外绮念
是 `70010_skin_zues04 _spine`——Zues 拼错且带空格；部分记录数字前缀是 cfg id 而非
CDN 的 role_id，同时候选 `prefabs_spine_{role_id}_{tail}` 变体）。l2dName 匹配不到
再走命名规则启发式：基础用 `prefabs_spine_{soundLanguageName}`；`_break_` ↔
skinType=2；`_synchro_` ↔ skinType=3；`_skin{NN}_` / `_skin_{name}{NN}_` 的 NN =
该角色装束在配置表里的序号（按 id 升序）。
**一个包只分配给一个装束**，匹配不到就当静态图，宁可少认也不错配。
CDN 上不存在的 img（如阿尔卑斯的三套商店皮）不会进入图鉴，属正常现象。

**CDN 探测要防抖**：同一 key（尤其带空格编码的怪名）会间歇性 404，`_probe_one`
对 404 也重试一次；被坏窗口写入 probe_cache 的 False 会永久生效，疑似误判时用
`--refresh` 全量重探。**l2dName 的怪 key 必须 URL 编码**（`urllib.parse.quote`），
`plugins/sources/crosscore_cdn.py` 的 `asset_url` 已统一处理。
**类型切换要清旧目录**：装束补数据后从 static 变 spine 时，backend 下载成功后会
删掉 `extracted/static/<painting>`（反向同理），否则本地索引同 painting 双条目、
mock/api 按第一条取会读到旧类型。

## 三、专属层新增（相对框架）

- `plugins/sources/crosscore_cdn.py`：`curr` 通道下载；spine 装束顺手下载 draw 作封面。
- `plugins/extractors/crosscore_static.py`：draw → `painting.png`（可选 `painting_face.png`）。
- `plugins/extractors/crosscore_spine.py`：Spine 三件套 → `character.json/.atlas/.png`
  ＋ `spine.json`（版本/动画/贴图尺寸）＋ `cover.png`（有 draw 时）。
- `core/unityfs.py`、`core/voice.py`：剥头/纹理导出、ACB 下载解码。
- `plugins/exporters/wallpaper_spine.py` + `templates/wallpaper_spine.html` +
  `frontend/public/vendor/spine-app.js`：Spine 4.2 壁纸运行时。
- `frontend/src/components/SpinePreview.vue`：图鉴/预览页的动态立绘预览；
  滚轮缩放与拖拽平移通过运行时回调 `onZoom/onPanStart/onPan` 实现（拖拽手势热区优先，
  其余区域拖动超过 4px 视为平移，不触发点击）。
- `frontend/src/components/SpineControls.vue`：仿 PtN/碧蓝的「预览控制」面板
  （动作互动/语音互动/台词文本/入场动画/交互区域），图鉴悬浮、预览页内嵌两套皮肤。
  「交互区域」开关把 actions 热区按帧同步成 DOM 叠加层（SpinePreview 实现：读
  `app.debug()` 的相机/可见态换算屏幕坐标，`pointer-events` 穿透不挡点击；
  激活=金色呼吸辉光斑（渐变半透明填充+动作名徽标，放不下徽标的小热区隐藏标注）、
  activation 未开启=灰白虚线。注意：叠加层元素是 createElement 动态建的，带不上
  scoped 的 data-v 属性，相关样式必须放在非 scoped 的 `<style>` 块里）。
  只有商店皮带 CfgSpineAction 热区配置（基础装束没有）；无配置时装束顶部显示
  空状态提示。
  导出壁纸里的同款叠加层由 spine-app.js 原生实现（`opts.areasEl` +
  `app.setAreasVisible`，样式写在 wallpaper_spine.html 全局 `<style>`），换算公式
  与类名和 SpinePreview.vue 这份实现一致——**改一处必须同步另一处**。

### 壁纸运行时（Spine）

- **按位置点击**：游戏配置 `CfgSpineAction` 给每个模型一组动作（`in/clickN`），
  每条动作带 1..N 个热区 `[x,y,w,h,angle]`（UI 坐标，angle 是旋转角度）与 `audioId`。
  热区换算：`骨骼坐标 = (UI - prefab.pos) / prefab.scale`，其中 `pos` 是 prefab 里名为
  `pos` 的 RectTransform（提取器写进 `spine.json` 的 `ui`）。运行时把点击点转到骨骼
  坐标，按动作 index 从后往前命中（后创建的在 UI 上层），播对应 `clickN` 动画 +
  `audioId` 随机一条语音/字幕，并执行 `content.activation`（开关其它热区）与
  `changeIdle`（延时切换待机动画）。动作配置随 `skins.json` 的 `actions` 下发，
  下载时写 `extracted/spine/{painting}/actions.json`，导出时换算成 `assets/actions.json`。
- 动画分两条轨道：**track0 待机**（`idle1/idle2` 循环）、**track1 点击动作**。
  `changeIdle` 只切 track0，动作播完不会把待机顶回旧状态——这是“脱丝袜后一直保持
  脱掉待机”的关键（单轨道实现会回退）。`activation` 的 key 是配置里的动作 index
  （包含 `in` 占位），运行时按 index→数组位置映射，不能按数组下标。
- 同一动作的 `audioId` 按顺序轮换（游戏里 clicks/actions 走 records 顺序），不随机，
  避免连点重复同一句。
- 多段点击（`content.clicks`）：每点一次推进一段，`clicks[n]` 是动画进度（0=反向播放），
  非最后一段停在目标进度，最后一段播完才执行 `activation`；`clickTime` 内连点才累计。
- 拖拽（`gesture` + `gestureDatas`）：按下开始播放并暂停在 0 帧，按拖动距离
  （gesture 3 横向 / 其余纵向）× `speed` 刮动画，进度到 `limitPerc` 松手后播完并执行
  `activation`，不够则回退清轨。
- 点击/拖拽动作播完清空 track1，回到 track0 当前待机；`changeIdle` 改成新的待机后
  后续动作结束都回它（脱丝袜类状态不会弹回）。
- 台词开关关闭时立即清空已显示字幕；`showSubtitle` 先查开关再触发 `onSubtitle`
  （预览组件的 DOM 字幕走回调、不走 subtitleEl，顺序反了开关就失效）。
- 拖拽平移的 `onPan` 按渲染帧合并（pointermove 触发频率高于帧率），偏移量保留
  0.1% 精度——取整成整数百分点在大画布上有 ~10px 阶梯感。
- **多段点击的持久化**：衣物类状态（如醉心可人的丝袜渐隐）靠 click 动画里的槽位
  rgba 时间线实现——清轨后槽位 alpha 留在骨骼上，idle 不重置这些槽位所以状态持久；
  `in` 入场动画带 attachment/rgba 重置，重选装束/重开壁纸才恢复初始。多段超时
  重置 stage 时必须同时清 trackTime，否则首段会瞬间"保持"在旧进度。
- **dev 服务器陷阱**：vite `/@fs/` 命中不存在的文件会 SPA-fallback 成 200+HTML，
  `resp.json()` 抛异常且 `resp.ok` 为 true——fetch 元数据必须对 json() 单独容错
  （loadActions 因此曾整个跳过 skins.json 的 actions 后备，表现为老装束没有互动）。
- **老下载数据回填**：actions.json 由 backend 下载时写入（skins.json 的 actions 字段
  即 CfgSpineAction），历史装束缺文件时从 skins.json 回填即可；spine.json 缺 ui
  （prefab pos/scale）时热区换算会错位，醉心可人的 ui 取自工坊 prefab
  （pos [-72,-2.1] scale 0.37）。运行时 debug 钩子：`opts.onDebugLog` +
  `app.getSlotAlpha(slotName)` 可用于排查互动问题。
- 只有在没有动作配置时才退回「头/身体」粗略判定（热点按 face/neck 骨骼）。
  **注意 WE 会把全局鼠标点击转发给壁纸页面**（壁纸窗口在桌面图标层下面，WE 用
  全局鼠标钩子灌 pointer 事件；`config.json` 里 `playbackfocus: run` 时切到其它
  应用也继续收），所以在别的应用里点一下也可能传进页面：
  - 有热区配置的装束，点热区外**不再**触发粗略判定（历史上代码无条件执行，
    任何落到角色范围内的点击都会冒「接触」台词，和注释写的“没有热区配置时”不符）。
  - pointerdown/pointerup 带页面焦点守卫：页面获得过焦点后只在仍有焦点时响应
    （点桌面时 WE 会把焦点给壁纸页面，切走后焦点转走）；从未获得过焦点的宿主
    保持原行为，避免互动整体失效。
  - WE 侧可选的“静音开关”：设置 → 播放 → 其他应用获得焦点时 = 暂停。
- **WE 属性（导出面板）**：语音/点击互动/台词字幕/音量/目标帧率/交互区域/开场动画。
  待机语音间隔与动画速度**不导出为 WE 属性**（用户不需要调）——运行时用固定默认值
  （idleInterval=60、speed=100，随 OPTIONS_JSON 烘进 HTML）。「交互区域」（`showareas`）
  开关切换 spine-app.js 的热区叠加层（`setAreasVisible`），activation 未开启的热区
  显示灰白虚线。
- **封面截图 captureCover 必须在主 WebGL 画布上做**：临时把 canvas.width/height 切到
  封面尺寸、按包围盒调相机渲染一帧再还原（与 PtN Live2D 的做法同思路）。开第二个
  WebGL context 离屏重画会得到**黑色剪影**——贴图对象只在创建它的 context 里有效；
  已在此踩过坑。画布 `alpha:false`，封面自带运行时画好的背景。

- 动画命名不统一：基础装束常见只有 `idle`；商店皮常有 `in` + `idle1/idle2` +
  `click1..clickN`（如赤溟·翻转课堂 14 个）。运行时会优先播 `in` 入场、`idle*` 循环、
  点击随机播 `click*`；没有点击动画时才退化成程序化弹跳/摇摆（`neck` 骨骼）。
- 点击命中：锚点骨骼优先 `face`（本作骨架没有 `head`），半径 = 包围盒高 12%；
  命中头部播「心动接触」，身体按 `接触1→2→3→友好→喜欢→挚爱` 轮换，
  6 秒内连点 4 次切「愉悦/不悦」。
- 待机每 `idleinterval` 秒（0=关）随机播「看板」。
- 字幕是壁纸内 DOM，12 秒淡出；语音/互动/字幕/音量/待机间隔都是 WE 属性。
- **WebGL 画布必须不透明**：additive 槽在透明画布上会累积出黑块；背景由运行时装
  在 WebGL 里画（palette + monet/frost/star/solid），`vivid()` 会把深色机甲配色
  提亮，否则整屏发黑。
- 封面：预览页 `captureCover()` 截 1280×720 完整立绘；后端兜底用 `cover.png`。

## 四、验收清单

```text
python tools/sync_crosscore_metadata.py     # 元数据（首次约 80s，会下载 ~10MB 配置）
python -c "import backend_server; backend_server.main(8766)"
cd frontend && npm run dev
python tests/test_crosscore_pipeline.py     # 纯离线单测
```

- 图鉴应显示 161 个可用角色 / 495 个装束（数量随游戏版本变化；游戏里 test_draw 占位角色会被隐藏）。
  同步时约探测 3000 个候选 key（首次 1~2 分钟，之后走 `probe_cache.json`）。
- 下载阿尔卑斯·常规武装：网速正常时 <5s（bundle 1.1MB + 语音 2MB），
  `resources/voice/100101/` 下应有 20 条互动/待机 wav。
- 导出后 `index.html` 点头部/身体触发不同台词；`project.json` 含
  语音/互动/字幕/音量/待机间隔/目标帧率属性。
