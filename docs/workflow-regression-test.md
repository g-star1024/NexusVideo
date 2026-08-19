# 工作流回归测试清单

> NexusVideo MVP · 工作流翻译与验证回归测试
> 最后更新：2026-08-19

---

## 1. 基础翻译测试

验证 `workflow_translator.py` 将前端请求正确映射为 ComfyUI 工作流 JSON。

| # | 测试项 | 输入 | 预期输出 | 验证方式 |
|---|--------|------|----------|----------|
| 1.1 | 空 prompt | `prompt=""` | 工作流生成成功，KSampler 的 conditioning text 为空字符串 | 执行脚本检查 `conditioning` 节点 |
| 1.2 | 超长 prompt（2000字） | `prompt="x" * 2000` | 工作流正常生成，prompt 完整写入 | JSON 解析 + 长度比对 |
| 1.3 | 特殊字符 prompt | `prompt='Hello "World" & <tag> [bracket]'` | 特殊字符原样保留，JSON 合法 | `json.loads()` 不抛异常 |
| 1.4 | 含换行符 prompt | `prompt="第一行\n第二行"` | 换行符在 JSON 中转义为 `\n` | 检查 JSON 中转义正确 |
| 1.5 | seed=0（随机种子） | `seed=0` | KSampler seed 字段为 0 | JSON 字段检查 |
| 1.6 | seed=负数 | `seed=-1` | 拒绝请求或转换为 0 | 检查 error handler |
| 1.7 | negative_prompt | `negative_prompt="blurry"` | 负向编码节点 text 正确 | JSON 检查 |

---

## 2. 参数边界测试

验证 `workflow_translator.py` 对参数边界的处理，确保输出合法。

| # | 参数 | 极值 | 预期行为 |
|---|------|------|----------|
| 2.1 | `width` | 最小 128 | 输出 width=128（对齐到 16 倍数 → 128） |
| 2.2 | `width` | 最大 1920 | 输出 width=1920 |
| 2.3 | `width` | 0 | 拒绝或降级为默认 832 |
| 2.4 | `width` | 非 16 倍数（如 800） | 自动对齐到 16 倍数（800→800，若 832 则→832） |
| 2.5 | `height` | 最小 64 | 对齐到 16 倍数 → 64 |
| 2.6 | `height` | 最大 1088 | 输出 height=1088 |
| 2.7 | `frames` | 最小 17 | 输出 frames=17（4n+1=17, n=4） |
| 2.8 | `frames` | 最大 121 | 输出 frames=121（4n+1=121, n=30） |
| 2.9 | `frames` | 非 4n+1（如 82） | 自动降级为最近 4n+1（81 或 85，取 81） |
| 2.10 | `steps` | 最小 1 | 输出 steps=1 |
| 2.11 | `steps` | 最大 100 | 输出 steps=100 |
| 2.12 | `cfg` | 最小 0.5 | 输出 cfg=0.5 |
| 2.13 | `cfg` | 最大 20.0 | 输出 cfg=20.0 |
| 2.14 | `cfg` | 负数 | 拒绝或降级为默认 6.0 |

---

## 3. 降级链测试

验证 `workflow_translator.py` 的显存降级逻辑。

| # | 触发条件 | 预期降级步骤 | 验证方式 |
|---|----------|-------------|----------|
| 3.1 | 显存 < 8GB | frames→81, width→720, steps→20, cfg→4.0 | 检查输出参数 |
| 3.2 | 显存 < 6GB | frames→49, width→576, steps→15, cfg→3.5 | 检查输出参数 |
| 3.3 | 显存 < 4GB | 触发云端切换 flag | 检查 `use_cloud` 标记 |
| 3.4 | 4 级降级后工作流合法性 | 所有参数在合法范围，JSON 合法 | 运行 validate_workflow.py |
| 3.5 | 帧数始终对齐 4n+1 | 任何降级后的 frames 均满足 `frames % 4 == 1` | `(frames-1) % 4 == 0` |
| 3.6 | 分辨率始终对齐 16 倍数 | width 和 height 均满足 `% 16 == 0` | `w % 16 == 0 and h % 16 == 0` |

### 降级阶梯参数表

| 级别 | 触发条件 | frames | width | height | steps | cfg | 备注 |
|------|---------|--------|-------|--------|-------|-----|------|
| 原始 | 无 | 81 | 832 | 480 | 30 | 6.0 | 默认 |
| 降级1 | VRAM < 12GB | 81 | 720 | 480 | 24 | 5.0 | |
| 降级2 | VRAM < 8GB | 49 | 720 | 480 | 20 | 4.0 | |
| 降级3 | VRAM < 6GB | 49 | 576 | 480 | 15 | 3.5 | |
| 云端切换 | VRAM < 4GB | - | - | - | - | - | 设置 `use_cloud=True` |

---

## 4. 工作流模板完整性测试

验证 `workflows/*.json` 模板文件的静态完整性。

| # | 测试项 | 验证方法 | 预期结果 |
|---|--------|----------|----------|
| 4.1 | JSON 语法 | `python -c "import json; json.load(open('xxx.json'))"` | 不抛异常 |
| 4.2 | 节点 ID 唯一性 | validate_workflow.py 自动检查 | 无重复 ID |
| 4.3 | 节点引用完整性 | validate_workflow.py 自动检查 | 无悬空引用 |
| 4.4 | class_type 白名单 | validate_workflow.py 自动检查 | 无未知类型（或已知为自定义） |
| 4.5 | 占位符可替换性 | 统计占位符数量 | txt2video.json 应含 prompt/width/height/frames/seed/steps/cfg 等占位符 |
| 4.6 | txt2video.json 节点数 | validate_workflow.py | 10 节点 |
| 4.7 | img2video.json 节点数 | validate_workflow.py | 按模板实际节点数 |
| 4.8 | video2video.json 节点数 | validate_workflow.py | 按模板实际节点数 |
| 4.9 | FFMPEG_VideoCombine 节点存在 | 检查输出合成节点 | 必须包含该节点 |
| 4.10 | 自定义节点标记 | validate_workflow.py 警告 | WanVideoWrapper 等标记为自定义 |

### 运行方式

```bash
python backend/scripts/validate_workflow.py
```

预期输出示例：
```
验证: txt2video.json
  节点数: 10
  字符串占位符: 2
  类型占位符: 7
  状态: ✅ 通过
```

---

## 5. ComfyUI 交互测试

验证后端与 ComfyUI 的 API/WS 交互是否正常。

| # | 测试项 | 验证方法 | 预期结果 |
|---|--------|----------|----------|
| 5.1 | ComfyUI 可达性 | 发送 GET /system_stats | 返回 200，含 device/ram 信息 |
| 5.2 | 工作流提交 | POST /prompt 带生成后的 JSON | 返回 200，含 prompt_id |
| 5.3 | WebSocket 连接 | 连接 ws://localhost:8188/ws?clientId=xxx | 成功建立连接 |
| 5.4 | 进度事件监听 | 提交任务后监听 WS | 收到 executing / progress / executed 事件 |
| 5.5 | 输出文件获取 | GET /view?filename=xxx.mp4 | 返回视频二进制流 |
| 5.6 | 错误处理-显存不足 | 使用超大参数提交 | ComfyUI 返回错误，后端正确捕获并返回用户可读错误 |
| 5.7 | 错误处理-模型未加载 | 使用不存在的 model_name | ComfyUI 返回错误，后端正确捕获 |
| 5.8 | 超时处理 | 提交任务后模拟 ComfyUI 无响应 | 后端在 300s 后返回超时错误 |
| 5.9 | 队列并发 | 同时提交 3 个任务 | 3 个 prompt_id 均返回，互不冲突 |
| 5.10 | 中断任务 | 发送 POST /interrupt | ComftyUI 停止当前任务 |

---

## 6. 快速验证命令

```bash
# 工作流模板静态验证
python backend/scripts/validate_workflow.py

# 翻译器单元测试（如已创建 pytest）
pytest backend/tests/test_workflow_translator.py -v

# 集成测试（需 ComfyUI 运行）
python integration_test.py
```

---

## 7. 回归测试执行记录

| 日期 | 提交 | 测试范围 | 结果 |
|------|------|----------|------|
| 2026-08-19 | be3b94e | txt2video.json 模板修复 | ✅ |
| 2026-08-19 | (待填) | validate_workflow.py + 回归测试清单 | ⬜ |

---

## 8. 注意事项

- 所有测试在提交 PR 前必须通过。
- 降级链测试需配合 `nvidia-smi` 实际输出进行验证。
- ComfyUI 交互测试需要在 ComfyUI 服务运行的环境下执行。
- 帧数降级必须保证 `4n+1` 对齐，否则 HunYuan 模型报错。
- 分辨率降级必须保证 `16` 倍数对齐，否则 VAE decode 报错。