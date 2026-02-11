import asyncio
import os
from dashscope.aigc.generation import AioGeneration

# 请提前设置环境变量：export DASHSCOPE_API_KEY=sk-xxxxxxxxxxxx
# 或者直接在这里填写（不推荐）
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "your-api-key-here")

async def non_stream_demo():
    """非流式调用：一次性返回完整结果（适合后台处理、需要完整文本的场景）"""
    print("\n=== 非流式调用演示 ===\n")
    print("用户：北京2026年天气怎么样？\n")
    print("Friday（一次性完整回复）：")

    payload = {
        "model": "qwen-max",                  # 可换成 qwen-plus、qwen-turbo 等
        "messages": [
            {"role": "system", "content": "You are a helpful assistant named Friday."},
            {"role": "user", "content": "北京2026年天气怎么样？"}
        ],
        "stream": False,                      # 关键：关闭流式
        "result_format": "message",
        "temperature": 0.7,                   # 控制创造性：0.0~1.0，越高越随机
        "top_p": 0.8,                         # 核采样，建议和 temperature 二选一调整
        "max_tokens": 512,                    # 最大输出 token 数，防止太长
    }

    response = await AioGeneration.call(
        api_key=DASHSCOPE_API_KEY,
        **payload
    )
    print(response)
    if response.status_code == 200:
        content = response.output.choices[0].message.content
        print(content)

        # 显示 token 使用情况
        usage = response.usage
        print(f"\n\nToken 使用：输入 {usage.input_tokens} + 输出 {usage.output_tokens} = 总计 {usage.total_tokens}")
    else:
        print(f"请求失败：{response.message}")

async def stream_demo():
    """流式调用：实时逐字/逐 token 返回（适合聊天界面“打字机”效果）"""
    print("\n\n=== 流式调用演示 ===\n")
    print("用户：北京2026年天气怎么样？\n")
    print("Friday（实时打字）：", end="", flush=True)

    payload = {
        "model": "qwen-max",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant named Friday."},
            {"role": "user", "content": "北京2026年天气怎么样？"}
        ],
        "stream": True,                       # 关键：开启流式
        "incremental_output": True,           # 推荐：只返回增量内容（更自然的打字效果）
        "result_format": "message",
        "temperature": 0.85,                  # 流式时可以稍微提高，回复更生动
        "max_tokens": 512,
    }

    response = await AioGeneration.call(
        api_key=DASHSCOPE_API_KEY,
        **payload
    )
    print("流式响应：", response)
    full_content = ""
    async for chunk in response:
        if chunk.status_code != 200:
            print(f"\n\n流式错误：{chunk.message}")
            return

        if chunk.output and chunk.output.choices:
            delta = chunk.output.choices[0].message.content
            if delta:
                print(delta, end="", flush=True)
                full_content += delta

    print("\n\n（流式输出结束）")
    # 流式结束后，chunk 中也会带最终的 usage
    if hasattr(response, "usage"):
        usage = response.usage
        print(f"Token 使用：输入 {usage.input_tokens} + 输出 {usage.output_tokens} = 总计 {usage.total_tokens}")

async def main():
    # 依次运行两个 demo，让你直观对比区别
    await non_stream_demo()
    await stream_demo()

    print("\n\n=== 总结对比 ===")
    print("• 非流式（stream=False）：")
    print("  - 优点：简单，一次性拿到完整结果，便于后续处理（如总结、存储）")
    print("  - 缺点：用户需要等待整个回复生成完毕，体验上有延迟")
    print("  - 适合：后台任务、批量生成、需要完整文本分析的场景")
    print("")
    print("• 流式（stream=True + incremental_output=True）：")
    print("  - 优点：实时显示“打字”效果，用户感知响应更快，体验更好")
    print("  - 缺点：代码稍复杂，需要 async for 处理")
    print("  - 适合：聊天界面、WebSocket、实时交互应用")
    print("")
    print("常用参数建议：")
    print("• temperature：0.3~0.5（更确定、严谨） vs 0.7~0.9（更有创意）")
    print("• top_p：通常设 0.8~0.95，与 temperature 互补")
    print("• max_tokens：根据上下文窗口控制，避免超限（qwen-max 支持最大 ~32k tokens）")

if __name__ == "__main__":
    asyncio.run(main())