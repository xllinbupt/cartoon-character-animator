# 每动作一行的合图模式

与旧manifest共用scripts/build_animation_assets.py。request使用已有pet-action-request/v1格式，保存和复用本轮真实生成帧，不调用模型；旧sheets/sequences配置不受影响。

```json
{
  "schema": "pet-action-request/v1",
  "pet_id": "travel-frog",
  "style": "沿用角色参考的脸型、配色、背包与描边",
  "layout": {"columns": 12, "frame_size": [192,192], "padding": 12, "placement": "preserve-cell"},
  "background": {"mode": "alpha"},
  "generation_budget": {"main_calls": 1, "repair_calls": 1},
  "actions": [
    {"id":"idle", "name":"待机", "frames":12, "fps":8, "loop":true, "poses":"连续呼吸与眨眼，首尾衔接", "motion":{"type":"none"}},
    {"id":"walk", "name":"向右走", "frames":12, "fps":12, "loop":true, "next_action":"idle", "facing":"right", "poses":"严格朝右侧面；两腿完整交替步态；身体原地", "root_motion":"runtime", "motion":{"type":"translate","dx":80,"dy":0,"duration_ms":1600,"finish":"keep_position"}}
  ]
}
```

columns是源图列数，行数为actions数量；frame_size是导出尺寸，不能把放大后的尺寸说成原生细节。frames≤columns；不足列数的尾格留空。每个动作的poses写连续12帧的阶段，prepare按配置生成prompt.txt、layout-guide.png和request快照。

placement默认preserve-cell：统一缩放并保留原格坐标，不按每个角色bbox高度归一。grounded仅用于已检查的原地姿势，按共同脚底基线放置，仍保留蹲下/趴睡的自然高度。小碎片过滤须对照原图，不能悄悄删掉配饰。需要局部位置修正时配置frame_offsets为每帧[dx,dy]并记原因，不能修复画错的肢体。

alpha模式必须有真实透明像素，并检查主体不透明区域完整。chroma模式示例：{"mode":"chroma","key":"#FF00FF","threshold":40,"softness":35}；这只是洋红key示例，必须确认主体与道具没有同色。根据主体选key，不自动同时去绿色和白色。背景策略、三个阶段的透明蒙版检查与诊断输出见 [alpha-quality.md](alpha-quality.md)。

## 播放与输出

motion.type为none/translate/arc；dx/dy是逻辑像素，duration_ms是一段轨迹的时长。arc增加height参数，easing为linear或ease_in_out；finish为keep_position或return_origin。root_motion=runtime时帧只表达姿势，整体位移由播放器负责；root_motion=frames时原格坐标已表达位移，motion只能none且必须preserve-cell。

输出frames/、atlas.png、actions/<id>.json、manifest.json、qa/、qa-report.json、gif/、apng/、webp/、preview.html（交互）和animation_preview.html（动图画廊）。每帧JSON含图集x/y/w/h、duration_ms、PNG路径和源bbox；每动作含fps、loop、next_action、anchor和motion。路径相对各JSON位置。

独立动图只播放姿势序列；runtime轨迹在交互预览/应用播放器执行。GIF只有二值透明且时长量化至10ms；APNG/WebP保留alpha。连续相同帧可能由格式编码器合并，不能根据压缩文件帧数判断模型生成帧数；单帧PNG是检查依据。单次动作GIF不写循环扩展，APNG/WebP只播放一次；旧manifest的loop数值仍按旧配置处理。

## 仅修失败动作

```bash
python3 scripts/build_animation_assets.py --task review --out candidate-v1 --action walk --verdict rejected --note '腿部缺少左右交替，不能读出完整步态'
python3 scripts/build_animation_assets.py --task repair --manifest request.json --previous candidate-v1 --out repair-plan
# 用repair-plan的提示词生成一张仅含失败动作的补图后：
python3 scripts/build_animation_assets.py --manifest repair-plan/request.json --sheet source/repair.png --previous candidate-v1 --out candidate-v2
```

状态为extraction_failed/pending_visual_review/approved/rejected。待审核不算失败；补图只含结构失败或已明确拒绝的动作。repair配置重排行号；build --previous保留旧动作帧、重组图集并更新所有JSON坐标，补图失败保留旧可播放候选。布局参数与pet_id必须匹配；始终输出新目录。

方法借鉴[sprite-gen轮廓切帧](https://github.com/aldegad/sprite-gen/blob/main/docs/sheet-slicing.md)与[动作验收](https://github.com/aldegad/sprite-gen/blob/main/docs/qa-motion.md)，Pillow实现独立编写。没有其像素网格重建、专业去色或自动步态语义检测；数量正确仍须目视和播放验收。
