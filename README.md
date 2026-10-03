# Cartoon Character Animator

卡通角色动作与表情生成技能。直接支持 **Seedream 5.0 Pro**，一张合图承载多个动作，并提供切帧、透明质量检查、动作JSON及PNG/GIF/APNG/WebP导出。

附带本机表情工坊：上传角色图片，编辑默认10个表情的名称和动作描述，批量生成、逐帧预览并下载微信GIF或ZIP。网页采用圆润的浅色卡片，不使用emoji装饰。

## 安装

需要Python **3.10及以上**。

```bash
git clone https://github.com/xllinbupt/cartoon-character-animator.git ~/.codex/skills/cartoon-character-animator
cd ~/.codex/skills/cartoon-character-animator
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

已有安装时先备份本机改动，再更新；不要覆盖自己的凭证和生成任务。其他支持`SKILL.md`的工具也可将整个目录放入对应的skills目录。

## 配置Seedream

自行在本机设置`ARK_API_KEY`，或设置`ARK_API_KEY_FILE`指向仅自己可读的密钥文件。也可使用本机默认文件`~/.config/cartoon-character-animator/ark.key`。

仓库**不包含API Key、私有服务配置或生成记录**。`.env.example`只有空变量名，脚本不会自动加载它。密钥仅由后端或CLI读取，不进入网页、模型日志或Git。

## 使用

在Codex中：

> 使用 $cartoon-character-animator，以Seedream 5.0生成我的角色的10个动作，每个12帧；一张合图，导出GIF与每动作JSON。

直接调用命令行的方法见[Seedream接入说明](references/seedream.md)。支持`--dry-run`免费验证输入，正式调用只发一次生成POST、不自动重试。

启动表情工坊：

```bash
python scripts/emoji_studio.py --port 4195
```

打开 **http://127.0.0.1:4195/**。只在本机监听；网页代码已发布不代表网页已公网托管。上传自己的卡通图开始使用，仓库不包含本机演示图片或历史作品。完整步骤见[表情工坊说明](references/emoji-studio.md)。

微信导出制作目标为240×240 GIF、120×120 PNG缩略图；100KB为保守制作目标，实际客户端导入或表情商店审核仍以平台要求为准。

## 验证

```bash
python -m unittest discover -s scripts -p 'test_*.py' -v
node --check assets/emoji-studio/app.js
python scripts/check_public_bundle.py --path . --staged --history
```

测试使用本机图片、模拟接口和虚拟凭证，不调用付费模型。源图切分成功仍需检查角色一致性、实际动作、透明边缘及首尾衔接；不会强切错误布局或复制姿势凑帧。
