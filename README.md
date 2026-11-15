# 交错战线动态壁纸工坊（crosscore-dynamic-wallpaper）

《交错战线》(CrossCore) 角色立绘 → Spine 动态预览 → Wallpaper Engine 壁纸导出。

从官方 CDN 拉取 Spine 动态立绘与中文语音，在图鉴式界面里浏览、预览、点哪播哪，
最后导出成 Wallpaper Engine web 壁纸项目（含封面与原生属性面板）。

> 本项目从 `dynamic-wallpaper-framework` 派生。资源协议、数据来源与踩坑记录见
> [AGENTS.md](AGENTS.md)。

## 功能特性

- **图鉴**：161 个可用角色 / 495 个装束（随游戏版本变化），按职业、稀有度、星级、
  装束系列筛选；支持按角色 / 按装束两种浏览模式。
- **Spine 4.2 动态立绘**：官方 SkeletonGraphic 骨架，双轨道动画
  （track0 待机循环、track1 点击动作），状态切换不回退。
- **按位置点击互动**：热区来自游戏配置 `CfgSpineAction`，每套商店皮 8~14 个可点击
  部位，带旋转矩形、独立动作与台词；多段点击（衣物类状态渐进）、拖拽刮动画
  （`gesture`）都按官方配置还原。
- **中文语音 + 字幕**：CriWare ACB 下载 → vgmstream 逐条解码，cue 名与 stream 序号
  正确映射；点击播对应台词，字幕 12 秒淡出。
- **交互区域可视化**：预览页与导出壁纸都能把热区叠加显示（金色呼吸光斑 /
  未激活灰白虚线），方便确认点哪里。
- **静态立绘**：官方高分辨率 draw（含表情差分），可直接导出静态壁纸。
- **预览操作**：滚轮缩放 20%~800%、拖拽平移；控制面板仿无期迷途/碧蓝航线样式。
- **导出**：自动生成 1280×720 完整立绘封面 `preview.jpg`、WE `project.json`
  原生属性（缩放 / 偏移 / 对齐 / 语音 / 互动 / 字幕 / 音量 / 帧率 / 交互区域 / 开场动画），
  支持一键应用到 Wallpaper Engine。
- **四套主题**：水彩画风（默认）/ 少女漫画风 / 赛博朋克霓虹 / 太阳朋克。

## 技术栈

- 外壳：pywebview + WebView2
- 后端：Python 3.10+，插件化（sources / extractors / exporters + 注册表）
- 前端：Vue 3 + Vite + Naive UI；Spine 用 spine-ts 4.2 WebGL 运行时
- 资源：UnityPy 解包 AssetBundle（文件头被改写过，需剥掉伪头）；vgmstream 解码 ACB

## 目录结构

```
crosscore-dynamic-wallpaper/
├── backend_server.py              # 本地后端 API（下载 / 提取 / 导出 / 取色 / 同步）
├── app_pack.py                    # 打包版入口（内置后端 8770 + 静态前端 5174 + 窗口）
├── web_main.py                    # 源码版桌面入口（加载 Vite dev server）
├── core/                          # 服务层：注册表 / 元数据 / 资源库 / 取色 / 语音 / UnityFS / WE 集成
├── plugins/
│   ├── sources/crosscore_cdn.py   # 官方 CDN 下载（curr 通道）
│   ├── extractors/                # spine / static 提取
│   └── exporters/                 # spine / static 壁纸导出
├── templates/                     # 导出模板 + wallpaper-layout.js 共享布局
├── frontend/                      # Vue 3 SPA（public/vendor 放 spine 运行时）
├── tools/                         # 元数据同步、索引重建、bundle 检查、vgmstream
├── tests/                         # 离线单测
├── scripts/start_dev.py           # 一键启动（关窗自动清理进程）
├── start_dev.bat                  # 双击启动源码版
└── crosscore.spec                 # PyInstaller 打包配置
```

## 环境要求

- Windows 10 / 11（需要 WebView2 运行时，Win11 自带）
- Python 3.10+（开发时用 3.14 验证）
- Node.js 18+
- Wallpaper Engine（仅「一键应用」需要，导出本身不需要）

## 快速开始（源码版）

```bat
:: 1. Python 依赖
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

:: 2. 前端依赖
cd frontend
npm install
cd ..

:: 3. 一键启动（后端 8766 + Vite 5173 + 桌面窗口）
start_dev.bat
```

关闭窗口后脚本会自动终止后端与 Vite；也可随时用 `start_dev.bat --stop` 清理整个开发栈。

首次使用请在「设置 → 数据更新」点 **更新资源清单**（约 5~10 分钟，会下载约 10MB
配置并全量探测 CDN 上的 ~5000 个资源 key），完成后图鉴即可用。

单独调试：

```bat
python -c "import backend_server; backend_server.main(8766)"
cd frontend && npm run dev
```

浏览器打开 http://127.0.0.1:5173 即是图鉴。

## 语音解码（可选但推荐）

语音依赖 [vgmstream](https://github.com/vgmstream/vgmstream)。把 `vgmstream-cli.exe`
及同目录 DLL 放进 `tools/vgmstream/`（本机工具，不入库，约 11MB）。

缺失时语音功能降级：只显示台词文本，不播音频。

## 元数据同步

```bat
python tools/sync_crosscore_metadata.py            :: 缺文件才下载（快速）
python tools/sync_crosscore_metadata.py --refresh  :: 强制重下配置与全量重探（游戏更新后用这个）
python tools/build_local_index.py                  :: 重建本地索引
```

数据来源：

| 内容 | 来源 |
|---|---|
| 角色 / 装束 / 语音台词 / 互动热区配置 | 社区整理 Lua 表 `AXiX-official/CrossCoreLuascripts` |
| CDN 资源清单（缓存） | 镜像 `R2iCecho/Crosscore_Assets_curr_1` |
| 反和谐 / 国际服变体清单 | `AXiX-official/CrossCore-Internationalizer` |
| 立绘 / Spine / 语音本体 | 官方 CDN（HEAD 探测确认真实存在） |

**游戏更新后**：设置页点「更新资源清单」即可（后端已固定带 `--refresh`，跑完自动
重建索引并热重载）。新角色能否出现取决于社区配置仓库是否已跟进当天版本。

## 打包发布

```bat
python scripts/build_pack.py
```

流程：强制 `--mode packaged` 构建前端 → 校验 API 端口指向 8770（避免打包版误连
开发后端 8766）→ PyInstaller → 产物自检。

产物位于 `dist/crosscore-wallpaper/`，入口 `crosscore-wallpaper.exe`
（内置后端 8770 + 静态前端 5174）。

> 端口约定：源码版后端 8766 / Vite 5173；打包版后端 8770 / 静态前端 5174。

## 测试

```bat
python -m unittest tests.test_crosscore_pipeline   :: Lua 解析 / 伪头剥离 / 元数据一致性
python tests/test_preview_cover.py                 :: 封面合成与兜底
```

## 资源与合规

- 仅从游戏官方通道拉取资源并在本地处理；仓库**不内置、不上传、不传播**任何游戏素材。
- 仓库内的 `resources/` 被 `.gitignore` 排除：立绘、语音、解包产物、导出壁纸都在本地生成。
- 元数据仅包含文本（角色名、装束名、台词），来自社区整理的配置表。
- 请遵守游戏服务条款；本工具定位为个人学习与壁纸创作用途。

## 致谢

- [UnityPy](https://github.com/K0lb3/UnityPy) — AssetBundle 解包
- [spine-runtimes](https://github.com/EsotericSoftware/spine-runtimes) — Spine WebGL 运行时
- [vgmstream](https://github.com/vgmstream/vgmstream) — CriWare ACB 解码
- [AXiX-official/CrossCoreLuascripts](https://github.com/AXiX-official/CrossCoreLuascripts) — 社区 Lua 配置
- [R2iCecho/Crosscore_Assets_curr_1](https://github.com/R2iCecho/Crosscore_Assets_curr_1) — CDN 资源清单镜像
- 界面风格参考《无期迷途》《碧蓝航线》的动态立绘预览交互
