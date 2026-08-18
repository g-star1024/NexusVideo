"""
NexusVideo 端到端集成测试 v2
============================
基于实际代码 API 签名编写。
运行方式：python integration_test.py
依赖：python -m pip install fastapi uvicorn psutil websockets python-multipart pydantic-settings httpx websockets bcrypt loguru
"""

import sys, pathlib, uuid, time

sys.path.insert(0, str(pathlib.Path(__file__).parent / "backend"))

test_suffix = str(int(time.time() * 1000) % 100000000)  # 8-digit unique number
test_phone = f"138{test_suffix}"  # 11-digit Chinese phone
test_username = f"testuser_{uuid.uuid4().hex[:8]}"

from fastapi.testclient import TestClient
from local_server import app
from pydantic import BaseModel

client = TestClient(app)

results = {"passed": 0, "failed": 0, "details": []}


def check(name, ok, msg=""):
    status = "PASS" if ok else "FAIL"
    results["passed" if ok else "failed"] += 1
    details = f"  [{status}] {name}" + (f" — {msg}" if msg else "")
    results["details"].append(details)
    print(details)


print("=" * 60)
print(" NexusVideo 端到端集成测试 v2")
print("=" * 60)

# ---- 1. 健康检查 ----
print("\n📡 1. 健康检查")
r = client.get("/health")
check("GET /health 返回 200", r.status_code == 200,
      f"status={r.status_code}")

# ---- 2. 未认证访问 /generate（依赖 Pydantic 校验，期望 422） ----
print("\n🔐 2. API 请求校验")
r = client.post("/generate", json={})
check("未认证 /generate 返回错误（422 或 401）",
      r.status_code in (401, 422),
      f"status={r.status_code}")

# ---- 3. 注册 + 获取 Token ----
print("\n📝 3. 用户注册（含 Token 返回）")
reg = client.post("/api/v1/auth/register", json={
    "username": test_username, "password": "test123",
    "phone": test_phone, "nickname": "测试用户"
})
check("注册成功", reg.status_code == 201,
      f"status={reg.status_code} body={reg.json()}")
token = None
if reg.status_code == 201:
    data = reg.json().get("data", {})
    token = data.get("token")
    check("注册返回 JWT Token", token is not None)

# ---- 4. JWT Token 加密/解密验证 ----
print("\n🔐 4. JWT Token 加密/解密")
from core.auth import create_access_token, verify_token as auth_verify
try:
    jwt = create_access_token(user_id="999")
    check("JWT 创建成功", jwt is not None)
    payload = auth_verify(jwt)
    check("JWT 解密通过", payload.get("user_id") == "999",
          f"user_id={payload.get('user_id')}")
except Exception as e:
    check("JWT 加密/解密", False, str(e))

# ---- 5. Token 鉴权 ----
print("\n🔑 5. Token 鉴权")
if token:
    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    check("已认证 GET /auth/me 通过", r.status_code == 200,
          f"status={r.status_code}")
    r = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer invalid_token"})
    check("无效 Token 被拒绝", r.status_code in (401, 422),
          f"status={r.status_code}")

# ---- 6. 工作流翻译器 ----
print("\n🔄 6. 工作流翻译器（txt2video）")
from core.workflow_translator import WorkflowTranslator
from models.schemas import GenerateRequest

translator = WorkflowTranslator()
try:
    req = GenerateRequest(
        prompt="a cat walking in the park",
        model_type="txt2video",
        strength=0.6,
        duration=5,
        width=1280,
        height=720,
    )
    wf, model_size = translator.translate(req)
    n = len(wf)  # ComfyUI native: string keys = node IDs
    check("txt2video 翻译成功", n > 0,
          f"nodes={n}, model_size={model_size}MB")
except Exception as e:
    check("txt2video 翻译成功", False, str(e))

# ---- 7. 工作流翻译器（img2video） ----
print("\n🔄 7. 工作流翻译器（img2video）")
try:
    req = GenerateRequest(
        prompt="a cat walking",
        model_type="img2video",
        strength=0.6,
        duration=5,
        width=1280,
        height=720,
        image_path="/tmp/test.png",
    )
    wf, _ = translator.translate(req)
    n = len(wf)
    check("img2video 翻译成功", n > 0, f"nodes={n}")
except Exception as e:
    check("img2video 翻译成功", False, str(e))

# ---- 8. 进度文案翻译器 ----
print("\n📊 8. 进度文案翻译器")
from core.progress_translator import ProgressTranslator
pt = ProgressTranslator()

try:
    texts = pt.get_phase_messages(0.15)  # 第一阶段
    check("进度文案翻译（0.15 阶段）", len(texts) > 0,
          f"texts={texts[:2]}")

    texts = pt.get_phase_messages(0.50)  # 第三阶段
    check("进度文案翻译（0.50 阶段）", len(texts) > 0,
          f"texts={texts[:2]}")

    texts = pt.get_phase_messages(0.95)  # 最后阶段
    check("进度文案翻译（0.95 阶段）", len(texts) > 0,
          f"texts={texts[:2]}")

    # 验证所有文案为中文
    all_chinese = all(
        any("\u4e00" <= c <= "\u9fff" for c in t)
        for t in texts
    )
    check("文案包含中文字符", all_chinese)

    # 预估耗时
    est = pt.get_estimated_text(0.5, 120)
    check("预估耗时文案", len(est) > 0, f"text='{est}'")
except Exception as e:
    check("进度文案翻译器", False, str(e))

# ---- 9. 任务管理器 ----
print("\n📋 9. 任务管理器")
from core.task_manager import task_manager, get_stage_message

try:
    # 测试文案化进度
    msg = get_stage_message(0.0)
    check("阶段文案（0%）", len(msg) > 0, f"msg='{msg}'")
    msg = get_stage_message(0.5)
    check("阶段文案（50%）", len(msg) > 0, f"msg='{msg}'")
    msg = get_stage_message(1.0)
    check("阶段文案（100%）", len(msg) > 0, f"msg='{msg}'")

    # 测试任务查询
    result = task_manager.get_task_status("nonexistent-task")
    check("查询不存在的 Task 返回 None", result is None)

    # 测试 active_count（属性，非方法）
    count = task_manager.active_count
    check("active_count 返回非负数", count >= 0,
          f"count={count}")
except Exception as e:
    check("任务管理器", False, str(e))

# ---- 10. 工作流 JSON 文件结构 ----
print("\n📄 10. ComfyUI 工作流 JSON 文件")
import json
wf_dir = pathlib.Path(__file__).parent / "workflows"
for wf_path in sorted(wf_dir.glob("*.json")):
    data = json.loads(wf_path.read_text())
    # ComfyUI native format: string keys = node IDs (not a "nodes" array)
    node_count = len(data)
    check(f"工作流 {wf_path.name} 结构有效", node_count > 0,
          f"nodes={node_count}")

# ---- 汇总 ----
print("\n" + "=" * 60)
total = results["passed"] + results["failed"]
print(f"📊 结果：{results['passed']}/{total} 通过，{results['failed']} 失败")
if results["failed"] > 0:
    print("\n🔴 失败项详情：")
    for d in results["details"]:
        if "FAIL" in d:
            print(d)
    sys.exit(1)
else:
    print("🟢 全部集成测试通过！")