# Seedream 5.0 直接生图

独立命令与表情工坊共用 `scripts/seedream.py`。默认请求模型为 `doubao-seedream-5-0-pro-260628`，通过火山方舟的公开图片生成接口调用；不依赖其他技能、公司内部服务或内置 imagegen。

## 本机凭证

凭证读取顺序：环境变量 `ARK_API_KEY` → 环境变量 `ARK_API_KEY_FILE` 指向的本机文件 → `~/.config/cartoon-character-animator/ark.key`。

自行在本机配置其中一种。使用文件时，将文件权限设置为仅自己可读写（macOS/Linux：`chmod 600`）。仓库里的 `.env.example` 仅列变量名，不含密钥；脚本不会自动加载 `.env`。

不得将真实密钥写入提示词、manifest、网页、示例、生成记录或 Git 提交。不要通过命令行参数传 key。公共接口地址不是凭证；用户私有接口地址、签名下载链接和令牌均不得进入公开仓库。

## 动作合图

先按 [action-sheet.md](action-sheet.md) 准备 request。默认每个动作12帧，一行一个动作；背景色键必须避开角色和道具颜色。

```bash
python3 scripts/build_animation_assets.py --task prepare --manifest request.json --out outputs/prepared

# 免费验证：不读取密钥，不联网，也不创建生成任务
python3 scripts/seedream.py \
  --prompt outputs/prepared/prompt.txt \
  --reference character.png \
  --guide outputs/prepared/layout-guide.png \
  --out outputs/source-v1 --dry-run

# 实际生图：只向模型发一个 POST，多个动作画进一张合图
python3 scripts/seedream.py \
  --prompt outputs/prepared/prompt.txt \
  --reference character.png \
  --guide outputs/prepared/layout-guide.png \
  --out outputs/source-v1

python3 scripts/build_animation_assets.py \
  --manifest request.json --sheet outputs/source-v1/source.png --out outputs/candidate-v1
```

`--reference` 可多次传入；与 `--guide` 合计最多10张。参考图保持原文件不变；不透明模式下，透明参考图只在提交副本中合成白底，避免隐藏RGB干扰角色身份。

输出 `source.png`、精确提示词 `prompt.txt` 与 `generation-log.json`。记录请求/返回模型、实际返回尺寸、文件哈希、用量和实际尝试次数，不保存认证头、key、参考图base64或临时签名URL。`generated_pending_review`表示已拿到原图，不代表动画或透明质量通过。

已有生成记录的输出目录不能再次提交，避免误重跑。超时可能已计费，不自动重试；检查旧任务后，自行选择新目录。CLI失败退出非零，源图若未成功下载则不能继续切帧。

## 官方参数边界

- 默认 `--size auto` 按guide宽高比选择约440万像素的尺寸；没有guide则为2K。可手动传 `1K`、`1.5K`、`2K` 或 `宽x高`。
- Pro的显式尺寸总像素范围为921600–4624220、宽高比为1:16–16:1。不把放大后的单帧说成原生4K；该适配器拒绝4K档位。
- 不配置组图、流式输出或联网搜索。节省调用靠一张合图承载多动作，不是调用模型生成多张图后声称只有一次生成。
- 默认不透明色键策略，可同时提供身份与布局参考。原生透明模式 `--background transparent` 只支持一张带透明通道的参考图，不能同时传guide；未经实际检查不能声称主体alpha可靠。

参数核对日期：2026-10-03。以[火山方舟官方图片生成API](https://docs.volcengine.com/docs/ark/image-generation-api?lang=zh)和[Seedream官方说明](https://docs.volcengine.com/docs/ark/seedream-4-0-5-0?lang=zh)为准。账号需自行开通模型权限和额度。
