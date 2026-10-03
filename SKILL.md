---
name: cartoon-character-animator
description: Generate consistent cartoon character or pet animation assets, multi-action sheets with direct Seedream 5.0 Pro generation and PNG/GIF/APNG/WebP exports. Use for 12-frame pet actions, transparent-asset QA, or a web expression studio with image uploads, custom named expressions, batch generation and WeChat GIF downloads.
---

# Cartoon Character Animator

复用角色参考，一张合图生成多个动作，保留原有 PNG/GIF/APNG/WebP 导出。按真实动作效果评估：同样调用次数与帧数下，合格动作更多、补图更少才算生成效果改善；脚本输出成功不等于动画通过。

## 生成

指定Seedream 5.0或需要本机直接生图时，读取 [Seedream接入](references/seedream.md)，使用 scripts/seedream.py；网页和CLI共用该适配器，凭证只从本机环境变量或密钥文件读取，不依赖其他skill。

用户要通过网页上传角色、填写表情名称/描述并批量生成和下载时，使用 [表情工坊网页](references/emoji-studio.md)。本机入口 http://127.0.0.1:4195/；先查健康接口再启动，不把本机版说成公网网站。网页通过服务端Seedream接口生成，内置imagegen仍用于对话内出图；不要混称两种能力。

- 先检查已有角色与动作，不重复生图。固定角色 bible：脸型、颜色、服装、配饰、比例、描边与视角规则；已有角色图直接复用，没有角色图可让首张合图试形象，不强制单独生母版。
- 宠物动作默认每行一个动作、每行动作12帧，按用户规格调整。一次合并多个动作省调用，不擅自减帧或复制少量姿势补数量。只有已有失败证据表明拆分能降低总成本时，才按视角/复杂度拆图。
- 写 request 前选择背景策略：已有实测可用的原生alpha可沿用；能力未验证或已有主体透明缺口时，下一轮优先选主体及道具中不存在的纯色色键。绿色宠物不用绿幕、米白宠物不用白底抠图。参数传了transparent不等于返回alpha可靠。详见 [背景与透明验收](references/alpha-quality.md)。保持一张多动作合图的调用预算，不为选背景额外生测试图。
- 写 request，使用同一配置产生提示词和布局guide；见 [合图配置与修复](references/action-sheet.md)。提示词按 [动作编排](references/prompting.md) 描述连续阶段、同一只手、道具持续性、步态交替和恢复姿势。参考图锁身份，guide仅锁布局，不照画网格。
- 默认用本机Seedream 5.0 Pro适配器；用户明确指定内置imagegen或其他模型时遵从，并记录实际请求/返回型号，不由工具名称推断。未配置密钥时交付prepare结果和本机配置方法，不自动切换平台。原图、精确提示词、参考图与实际调用数保存到本轮source和generation-log.json。默认先生成1张，已知失败动作最多集中补1张，用户预算优先；不要自动增加付费调用。直接生图，不走视频。公开发布前检查暂存区与提交历史；真实key、私有接口配置、上传原图、任务记录及输出素材不提交Git。

## 整理与验收

- alpha既要有透明背景，也要保住主体。build自动保存原始RGB/深底/alpha对照、导出浅底/深底拼图和alpha-qa.json；检查腹部、白色斑纹、脚趾、描边及手持道具。自动警告仅定位疑点；与外部连通的缺口可能漏检，必须看全部帧。未复核警告与主体完整性不得标approved。旧manifest的透明资产仍保留主体绿色与白色。
- 先对照原图RGB、原图alpha、处理后alpha定位责任：下载文件已有缺口属于上游输出，处理后才出现属于抠图/切帧/缩放；不能仅凭下载PNG归咎于模型绘制。保留原文件和隐藏RGB，禁止用“全局去白”、膨胀填洞或整体侵蚀描边掩盖问题。仅处理问题应先调整处理方法，不能直接计入生图补图；确需新图仍受本轮预算限制。
- 合图用全图轮廓分配格子并统一缩放，保留蹲下/趴睡的高度变化；不逐帧放到相同bbox高度。缺帧或跨格粘连逐动作失败，不能按错误网格强切成正确数量。
- 导出每动作JSON、透明单帧、图集、GIF/APNG/WebP与交互preview。提取成功仅标pending_visual_review。检查全部帧、浅色/深色底、手脚、配饰、动作阶段与首尾；走路必须有交替步态，不能把单姿势晃动当成真走路。
- 用review记录通过或失败及具体依据；仅已知失败动作生成repair配置，合格动作原帧保留。再次失败先诊断，预算用完交付候选与原因。
- PNG姿势与场景位移分开：JSON的root_motion明确由runtime或frames负责，避免双重移动。挥手等单次动作结束返回idle；走路按速度/轨迹移动，小跳按弧线移动。播放与轨迹可分别检查。
- 先启动正确目录的HTTP预览，检查页面和图片URL，再交付可打开的网址；本地文件链接不是唯一试用入口。实际点击动作、暂停、重播、逐帧和背景切换；无法操作浏览器时明确只验证了文件/脚本/HTTP。preview包含首帧兜底，加载失败不空白。

## 同一个入口

```bash
# 12帧动作合图：准备提示词/guide，不调用模型
python3 scripts/build_animation_assets.py --task prepare --manifest request.json --out prepared
# Seedream 5.0 Pro：本机配置凭证后，只生成一张合图
python3 scripts/seedream.py --prompt prepared/prompt.txt --reference character.png --guide prepared/layout-guide.png --out outputs/source-v1
# 已生成的整张合图：切帧并导出所有格式
python3 scripts/build_animation_assets.py --manifest request.json --sheet outputs/source-v1/source.png --out outputs/candidate-v1
# 仅诊断既有合图，不改原图、不补图、不改已有预览
python3 scripts/build_animation_assets.py --task audit --manifest request.json --sheet outputs/source-v1/source.png --out outputs/alpha-audit
# 原来的manifest和用法继续有效
python3 scripts/build_animation_assets.py --manifest manifest.json --out legacy-output
```

旧版配置与breathe/cycle/jump/smooth_locomotion模式见 [原配置](references/manifest.md)。这些程序动画可按用户需要使用，但要标明单姿势变换；不默认替代本次要求的12帧姿势动作。独立序列导出姿势，场景位置轨迹在JSON/播放器里执行；GIF/APNG/WebP不是游戏状态机。
