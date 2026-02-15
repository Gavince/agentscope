# -*- coding: utf-8 -*-
# TODO: simplify the ReActAgent class
# pylint: disable=not-an-iterable, too-many-lines
# mypy: disable-error-code="list-item"
"""agentscope 中的 ReAct agent 类"""
import asyncio
from enum import Enum
from typing import Type, Any, AsyncGenerator, Literal

from pydantic import BaseModel, ValidationError, Field

from ._utils import _AsyncNullContext
from ._react_agent_base import ReActAgentBase
from .._logging import logger
from ..formatter import FormatterBase
from ..memory import MemoryBase, LongTermMemoryBase, InMemoryMemory
from ..message import (
    Msg,
    ToolUseBlock,
    ToolResultBlock,
    TextBlock,
    AudioBlock,
)
from ..model import ChatModelBase
from ..rag import KnowledgeBase, Document
from ..plan import PlanNotebook
from ..token import TokenCounterBase
from ..tool import Toolkit, ToolResponse
from ..tracing import trace_reply
from ..tts import TTSModelBase


class _QueryRewriteModel(BaseModel):
    """用于查询重写的结构化模型"""

    rewritten_query: str = Field(
        description=(
            "重写后的查询，应该具体且简洁"
        ),
    )


class SummarySchema(BaseModel):
    """The compressed memory model, used to generate summary of old memories"""

    task_overview: str = Field(
        max_length=300,
        description=(
            "The user's core request and success criteria.\n"
            "Any clarifications or constraints they specified"
        ),
    )
    current_state: str = Field(
        max_length=300,
        description=(
            "What has been completed so far.\n"
            "File created, modified, or analyzed (with paths if relevant).\n"
            "Key outputs or artifacts produced."
        ),
    )
    important_discoveries: str = Field(
        max_length=300,
        description=(
            "Technical constraints or requirements uncovered.\n"
            "Decisions made and their rationale.\n"
            "Errors encountered and how they were resolved.\n"
            "What approaches were tried that didn't work (and why)"
        ),
    )
    next_steps: str = Field(
        max_length=200,
        description=(
            "Specific actions needed to complete the task.\n"
            "Any blockers or open questions to resolve.\n"
            "Priority order if multiple steps remain"
        ),
    )
    context_to_preserve: str = Field(
        max_length=300,
        description=(
            "User preferences or style requirements.\n"
            "Domain-specific details that aren't obvious.\n"
            "Any promises made to the user"
        ),
    )


class _MemoryMark(str, Enum):
    """The memory marks used in the ReAct agent."""

    HINT = "hint"
    """Used to mark the hint messages that will be cleared after use."""

    COMPRESSED = "compressed"
    """Used to mark the compressed messages in the memory."""


class ReActAgent(ReActAgentBase):
    """AgentScope 中的 ReAct agent 实现，支持以下特性：

    - 实时引导
    - 基于 API 的（并行）工具调用
    - 在推理、行动、回复、观察和打印函数周围的钩子
    - 结构化输出生成
    """

    class CompressionConfig(BaseModel):
        """The compression related configuration in AgentScope"""

        model_config = {"arbitrary_types_allowed": True}
        """Allow arbitrary types in the pydantic model."""

        enable: bool
        """Whether to enable the auto compression feature."""

        agent_token_counter: TokenCounterBase
        """The token counter for the agent's model, which must be consistent
        with the model used in the agent."""

        trigger_threshold: int
        """The token threshold to trigger the compression process. When the
        total token count in the memory exceeds this threshold, the
        compression will be activated."""

        keep_recent: int = 3
        """The number of most recent messages to keep uncompressed in the
        memory to preserve the recent context."""

        compression_prompt: str = (
            "<system-hint>You have been working on the task described above "
            "but have not yet completed it. "
            "Now write a continuation summary that will allow you to resume "
            "work efficiently in a future context window where the "
            "conversation history will be replaced with this summary. "
            "Your summary should be structured, concise, and actionable."
            "</system-hint>"
        )
        """The prompt used to guide the compression model to generate the
        compressed summary, which will be wrapped into a user message and
        attach to the end of the current memory."""

        summary_template: str = (
            "<system-info>Here is a summary of your previous work\n"
            "# Task Overview\n"
            "{task_overview}\n\n"
            "# Current State\n"
            "{current_state}\n\n"
            "# Important Discoveries\n"
            "{important_discoveries}\n\n"
            "# Next Steps\n"
            "{next_steps}\n\n"
            "# Context to Preserve\n"
            "{context_to_preserve}"
            "</system-info>"
        )
        """The string template to present the compressed summary to the agent,
        which will be formatted with the fields from the
        `compression_summary_model`."""

        summary_schema: Type[BaseModel] = SummarySchema
        """The structured model used to guide the agent to generate the
        structured compressed summary."""

        compression_model: ChatModelBase | None = None
        """The compression model used to generate the compressed summary. If
        not provided, the agent's model will be used."""

        compression_formatter: FormatterBase | None = None
        """The corresponding formatter form the compression model, when the
        `compression_model` is provided, the `compression_formatter` must also
        be provided."""

    finish_function_name: str = "generate_response"
    """用于生成结构化输出的函数名称。仅在 reply 调用中提供结构化输出模型时注册"""

    def __init__(
        self,
        name: str,
        sys_prompt: str,
        model: ChatModelBase,
        formatter: FormatterBase,
        toolkit: Toolkit | None = None,
        memory: MemoryBase | None = None,
        long_term_memory: LongTermMemoryBase | None = None,
        long_term_memory_mode: Literal[
            "agent_control",
            "static_control",
            "both",
        ] = "both",
        enable_meta_tool: bool = False,
        parallel_tool_calls: bool = False,
        knowledge: KnowledgeBase | list[KnowledgeBase] | None = None,
        enable_rewrite_query: bool = True,
        plan_notebook: PlanNotebook | None = None,
        print_hint_msg: bool = False,
        max_iters: int = 10,
        tts_model: TTSModelBase | None = None,
        compression_config: CompressionConfig | None = None,
    ) -> None:
        """初始化 ReAct agent

        Args:
            name (`str`):
                agent 的名称
            sys_prompt (`str`):
                agent 的系统提示词
            model (`ChatModelBase`):
                agent 使用的聊天模型
            formatter (`FormatterBase`):
                用于将消息格式化为模型 API 提供商所需格式的格式化器
            toolkit (`Toolkit | None`, optional):
                包含工具函数的 `Toolkit` 对象。如果未提供，将创建一个默认的空 `Toolkit`
            memory (`MemoryBase | None`, optional):
                用于存储对话历史的记忆。如果未提供，将创建一个默认的 `InMemoryMemory`，
                它在内存中的列表中存储消息
            long_term_memory (`LongTermMemoryBase | None`, optional):
                可选的长期记忆，它将提供两个工具函数：`retrieve_from_memory` 和 `record_to_memory`，
                并在每次回复之前将检索到的信息附加到系统提示词中
            enable_meta_tool (`bool`, defaults to `False`):
                如果为 `True`，将向工具包添加一个元工具函数 `reset_equipped_tools`，
                允许 agent 动态管理其装备的工具
            long_term_memory_mode (`Literal['agent_control', 'static_control',\
              'both']`, defaults to `both`):
                长期记忆的模式。如果为 `agent_control`，将在工具包中注册两个工具函数
                `retrieve_from_memory` 和 `record_to_memory`，允许 agent 管理长期记忆。
                如果为 `static_control`，检索和记录将分别在每次回复的开始和结束时发生
            parallel_tool_calls (`bool`, defaults to `False`):
                当 LLM 生成多个工具调用时，是否并行执行它们
            knowledge (`KnowledgeBase | list[KnowledgeBase] | None`, optional):
                agent 用于在每次回复开始时检索相关文档的知识对象
            enable_rewrite_query (`bool`, defaults to `True`):
                是否在从知识库检索之前要求 agent 重写用户输入查询，
                例如将 "我是谁" 重写为 "{用户名}" 以获得更相关的文档。
                仅在提供知识库时有效
            plan_notebook (`PlanNotebook | None`, optional):
                计划笔记本实例，允许 agent 通过将复杂任务分解为一系列子任务来完成
            print_hint_msg (`bool`, defaults to `False`):
                是否打印提示消息，包括来自计划笔记本的推理提示、
                从长期记忆和知识库检索到的信息
            max_iters (`int`, defaults to `10`):
                推理-行动循环的最大迭代次数
            tts_model (`TTSModelBase | None` optional):
                The TTS model used by the agent.
        """
        super().__init__()

        assert long_term_memory_mode in [
            "agent_control",
            "static_control",
            "both",
        ]

        # agent 中的静态变量
        self.name = name
        self._sys_prompt = sys_prompt
        self.max_iters = max_iters
        self.model = model
        self.formatter = formatter
        self.tts_model = tts_model
        self.compression_config = compression_config

        # -------------- 记忆管理 --------------
        # 在记忆中记录对话历史
        self.memory = memory or InMemoryMemory()
        # 如果提供了长期记忆，它将用于在每次回复开始时检索信息，
        # 并将结果添加到系统提示词中
        self.long_term_memory = long_term_memory

        # 长期记忆模式
        self._static_control = long_term_memory and long_term_memory_mode in [
            "static_control",
            "both",
        ]
        self._agent_control = long_term_memory and long_term_memory_mode in [
            "agent_control",
            "both",
        ]

        # -------------- 工具管理 --------------
        # 如果为 None，将创建一个默认的 Toolkit
        self.toolkit = toolkit or Toolkit()
        if self._agent_control:
            # 向工具包添加两个工具函数以允许自我控制
            self.toolkit.register_tool_function(
                long_term_memory.record_to_memory,
            )
            self.toolkit.register_tool_function(
                long_term_memory.retrieve_from_memory,
            )
        # 添加元工具函数以允许 agent 控制的工具管理
        if enable_meta_tool:
            self.toolkit.register_tool_function(
                self.toolkit.reset_equipped_tools,
            )

        self.parallel_tool_calls = parallel_tool_calls

        # -------------- RAG 管理 --------------
        # agent 使用的知识库
        if isinstance(knowledge, KnowledgeBase):
            knowledge = [knowledge]
        self.knowledge: list[KnowledgeBase] = knowledge or []
        self.enable_rewrite_query = enable_rewrite_query

        # -------------- 计划管理 --------------
        # 将计划笔记本提供的计划相关工具作为名为 "plan_related" 的工具组装备。
        # 这样 agent 就可以通过元工具函数激活计划工具
        self.plan_notebook = None
        if plan_notebook:
            self.plan_notebook = plan_notebook
            # 当 enable_meta_tool 为 True 时，计划工具在 plan_related 组中，
            # 由 agent 激活。否则，计划工具在 basic 组中并始终激活
            if enable_meta_tool:
                self.toolkit.create_tool_group(
                    "plan_related",
                    description=self.plan_notebook.description,
                )
                for tool in plan_notebook.list_tools():
                    self.toolkit.register_tool_function(
                        tool,
                        group_name="plan_related",
                    )
            else:
                for tool in plan_notebook.list_tools():
                    self.toolkit.register_tool_function(
                        tool,
                    )

        # 是否打印推理提示消息
        self.print_hint_msg = print_hint_msg

        # 推理-行动循环的最大迭代次数
        self.max_iters = max_iters

        # The hint messages that will be attached to the prompt to guide the
        # agent's behavior before each reasoning step, and cleared after
        # each reasoning step, meaning the hint messages is one-time use only.
        # We use an InMemoryMemory instance to store the hint messages
        self._reasoning_hint_msgs = InMemoryMemory()

        # 记录中间状态的变量

        # 如果提供了所需的结构化输出模型
        self._required_structured_model: Type[BaseModel] | None = None

        # -------------- 状态注册和钩子 --------------
        # 注册状态变量
        self.register_state("name")
        self.register_state("_sys_prompt")

    @property
    def sys_prompt(self) -> str:
        """agent 的动态系统提示词"""
        agent_skill_prompt = self.toolkit.get_agent_skill_prompt()
        if agent_skill_prompt:
            return self._sys_prompt + "\n\n" + agent_skill_prompt
        else:
            return self._sys_prompt

    @trace_reply
    async def reply(  # pylint: disable=too-many-branches
        self,
        msg: Msg | list[Msg] | None = None,
        structured_model: Type[BaseModel] | None = None,
    ) -> Msg:
        """基于当前状态和输入参数生成回复

        Args:
            msg (`Msg | list[Msg] | None`, optional):
                输入给 agent 的消息
            structured_model (`Type[BaseModel] | None`, optional):
                所需的结构化输出模型。如果提供，agent 将在输出消息的
                `metadata` 字段中生成结构化输出

        Returns:
            `Msg`:
                agent 生成的输出消息
        """
        # 在记忆中记录输入消息
        await self.memory.add(msg)

        # -------------- 检索过程 --------------
        # 如果激活，从长期记忆中检索相关记录
        await self._retrieve_from_long_term_memory(msg)
        # 如果有，从知识库中检索相关文档
        await self._retrieve_from_knowledge(msg)

        # 控制 LLM 在每个推理步骤中是否生成工具调用
        tool_choice: Literal["auto", "none", "required"] | None = None

        # -------------- 结构化输出管理 --------------
        self._required_structured_model = structured_model
        # 如果提供，记录结构化输出模型
        if structured_model:
            # 仅在需要结构化输出时注册 generate_response 工具
            if self.finish_function_name not in self.toolkit.tools:
                self.toolkit.register_tool_function(
                    getattr(self, self.finish_function_name),
                )

            # 设置结构化输出模型
            self.toolkit.set_extended_model(
                self.finish_function_name,
                structured_model,
            )
            tool_choice = "required"
        else:
            # 如果不需要结构化输出，移除 generate_response 工具
            self.toolkit.remove_tool_function(self.finish_function_name)

        # -------------- 推理-行动循环 --------------
        # 缓存在 finish 函数调用中生成的结构化输出
        structured_output = None
        reply_msg = None
        for _ in range(self.max_iters):
            # -------------- The reasoning process --------------
            msg_reasoning = await self._reasoning(tool_choice)

            # -------------- 行动过程 --------------
            futures = [
                self._acting(tool_call)
                for tool_call in msg_reasoning.get_content_blocks(
                    "tool_use",
                )
            ]
            # 是否并行调用工具
            if self.parallel_tool_calls:
                structured_outputs = await asyncio.gather(*futures)
            else:
                # 顺序调用工具
                structured_outputs = [await _ for _ in futures]

            # -------------- 检查退出条件 --------------
            # 如果结构化输出仍未满足
            if self._required_structured_model:
                # 移除 None 结果
                structured_outputs = [_ for _ in structured_outputs if _]

                msg_hint = None
                # 如果行动步骤生成了结构化输出
                if structured_outputs:
                    # 缓存结构化输出数据
                    structured_output = structured_outputs[-1]

                    # 准备文本响应
                    if msg_reasoning.has_content_blocks("text"):
                        # 如果有现有的文本响应，重用它以避免重复的文本生成
                        reply_msg = Msg(
                            self.name,
                            msg_reasoning.get_content_blocks("text"),
                            "assistant",
                            metadata=structured_output,
                        )
                        break

                    # 在下一次迭代中生成文本响应
                    msg_hint = Msg(
                        "user",
                        "<system-hint>现在根据当前情况生成文本响应"
                        "</system-hint>",
                        "user",
                    )
                    await self.memory.add(
                        msg_hint,
                        marks=_MemoryMark.HINT,
                    )

                    # 在下一个推理步骤中只生成文本响应
                    tool_choice = "none"
                    # 结构化输出已成功生成
                    self._required_structured_model = None

                elif not msg_reasoning.has_content_blocks("tool_use"):
                    # 如果需要结构化输出但没有进行工具调用，
                    # 提醒 llm 继续任务
                    msg_hint = Msg(
                        "user",
                        "<system-hint>需要结构化输出，"
                        f"继续完成任务或调用 "
                        f"'{self.finish_function_name}' 来生成所需的结构化输出。"
                        "</system-hint>",
                        "user",
                    )
                    await self._reasoning_hint_msgs.add(msg_hint)
                    # Require tool call in the next reasoning step
                    tool_choice = "required"

                if msg_hint and self.print_hint_msg:
                    await self.print(msg_hint)

            elif not msg_reasoning.has_content_blocks("tool_use"):
                # 当不需要结构化输出（或已满足）且仅生成文本响应时退出循环
                msg_reasoning.metadata = structured_output
                reply_msg = msg_reasoning
                break

        # 当达到最大迭代次数且没有生成回复消息时
        if reply_msg is None:
            reply_msg = await self._summarizing()
            reply_msg.metadata = structured_output
            await self.memory.add(reply_msg)

        # 后处理记忆、长期记忆
        if self._static_control:
            await self.long_term_memory.record(
                [
                    *await self.memory.get_memory(
                        exclude_mark=_MemoryMark.COMPRESSED,
                    ),
                ],
            )

        return reply_msg

    # pylint: disable=too-many-branches
    async def _reasoning(
        self,
        tool_choice: Literal["auto", "none", "required"] | None = None,
    ) -> Msg:
        """执行推理过程"""

        if self.plan_notebook:
            # 从计划笔记本插入推理提示
            hint_msg = await self.plan_notebook.get_current_hint()
            if self.print_hint_msg and hint_msg:
                await self.print(hint_msg)
            await self.memory.add(hint_msg, marks=_MemoryMark.HINT)

        # 将 Msg 对象转换为模型 API 所需的格式
        # 这里调用的是 formatter.format 异步方法，它是格式化消息列表的方法，
        # 必须由子类实现（FormatterBase 定义为 abstractmethod）。
        # 该方法需要返回格式化后的 prompt，用于后续模型调用。此处接收其返回值 prompt。
        prompt = await self.formatter.format(
            msgs=[
                Msg("system", self.sys_prompt, "system"),
                *await self.memory.get_memory(),
                # The hint messages to guide the agent's behavior, maybe empty
                *await self._reasoning_hint_msgs.get_memory(),
            ],
        )
        # Clear the hint messages after use
        await self._reasoning_hint_msgs.clear()

        res = await self.model(
            prompt,
            tools=self.toolkit.get_json_schemas(),
            tool_choice=tool_choice,
        )

        # 处理模型的输出
        interrupted_by_user = False
        msg = None

        # TTS 模型上下文管理器
        tts_context = self.tts_model or _AsyncNullContext()
        speech: AudioBlock | list[AudioBlock] | None = None

        try:
            async with tts_context:
                msg = Msg(name=self.name, content=[], role="assistant")
                if self.model.stream:
                    async for content_chunk in res:
                        msg.content = content_chunk.content

                        # 从多模态（音频）模型生成的语音
                        # 例如 Qwen-Omni 和 GPT-AUDIO
                        speech = msg.get_content_blocks("audio") or None

                        # 如果可用，推送到 TTS 模型
                        if (
                            self.tts_model
                            and self.tts_model.supports_streaming_input
                        ):
                            tts_res = await self.tts_model.push(msg)
                            speech = tts_res.content

                        await self.print(msg, False, speech=speech)

                else:
                    msg.content = list(res.content)

                if self.tts_model:
                    # 推送到 TTS 模型并阻塞以接收完整的语音合成结果
                    tts_res = await self.tts_model.synthesize(msg)
                    if self.tts_model.stream:
                        async for tts_chunk in tts_res:
                            speech = tts_chunk.content
                            await self.print(msg, False, speech=speech)
                    else:
                        speech = tts_res.content

                await self.print(msg, True, speech=speech)

                # 添加微小的延时以让出消息队列中的最后一个消息对象
                await asyncio.sleep(0.001)

        except asyncio.CancelledError as e:
            interrupted_by_user = True
            raise e from None

        finally:
            # None 将被记忆忽略
            await self.memory.add(msg)

            # 用户中断的后处理
            if interrupted_by_user and msg:
                # 伪造工具结果
                tool_use_blocks: list = msg.get_content_blocks(
                    "tool_use",
                )
                for tool_call in tool_use_blocks:
                    msg_res = Msg(
                        "system",
                        [
                            ToolResultBlock(
                                type="tool_result",
                                id=tool_call["id"],
                                name=tool_call["name"],
                                output="工具调用已被用户中断",
                            ),
                        ],
                        "system",
                    )
                    await self.memory.add(msg_res)
                    await self.print(msg_res, True)
        return msg

    async def _acting(self, tool_call: ToolUseBlock) -> dict | None:
        """执行行动过程，如果在 finish 函数调用中生成并验证了结构化输出，则返回它

        Args:
            tool_call (`ToolUseBlock`):
                要执行的工具使用块

        Returns:
            `Union[dict, None]`:
                如果在 finish 函数调用中验证了结构化输出，则返回它，否则返回 None
        """

        tool_res_msg = Msg(
            "system",
            [
                ToolResultBlock(
                    type="tool_result",
                    id=tool_call["id"],
                    name=tool_call["name"],
                    output=[],
                ),
            ],
            "system",
        )
        try:
            # 执行工具调用
            tool_res = await self.toolkit.call_tool_function(tool_call)

            # 异步生成器处理
            async for chunk in tool_res:
                # 转换为工具结果块
                tool_res_msg.content[0][  # type: ignore[index]
                    "output"
                ] = chunk.content

                await self.print(tool_res_msg, chunk.is_last)

                # 抛出 CancelledError 以在 handle_interrupt 函数中处理中断
                if chunk.is_interrupted:
                    raise asyncio.CancelledError()

                # 如果成功调用 generate_response，则返回消息
                if (
                    tool_call["name"] == self.finish_function_name
                    and chunk.metadata
                    and chunk.metadata.get("success", False)
                ):
                    # 只返回结构化输出
                    return chunk.metadata.get("structured_output")

            return None

        finally:
            # 在记忆中记录工具结果消息
            await self.memory.add(tool_res_msg)

    async def observe(self, msg: Msg | list[Msg] | None) -> None:
        """接收观察消息而不生成回复

        Args:
            msg (`Msg | list[Msg] | None`):
                要观察的一条或多条消息
        """
        await self.memory.add(msg)

    async def _summarizing(self) -> Msg:
        """当 agent 在最大迭代次数内未能解决问题时生成响应"""

        hint_msg = Msg(
            "user",
            "你未能在最大迭代次数内生成响应。现在通过总结当前情况直接回复。",
            role="user",
        )

        # 通过总结当前情况生成回复
        prompt = await self.formatter.format(
            [
                Msg("system", self.sys_prompt, "system"),
                *await self.memory.get_memory(
                    exclude_mark=_MemoryMark.COMPRESSED
                    if self.compression_config
                    and self.compression_config.enable
                    else None,
                ),
                hint_msg,
            ],
        )
        # TODO: 在这里处理结构化输出，也许在这里强制调用 finish_function
        res = await self.model(prompt)

        # TTS 模型上下文管理器
        tts_context = self.tts_model or _AsyncNullContext()
        speech: AudioBlock | list[AudioBlock] | None = None

        async with tts_context:
            res_msg = Msg(self.name, [], "assistant")
            if isinstance(res, AsyncGenerator):
                async for chunk in res:
                    res_msg.content = chunk.content

                    # 从多模态（音频）模型生成的语音
                    # 例如 Qwen-Omni 和 GPT-AUDIO
                    speech = res_msg.get_content_blocks("audio") or None

                    # 如果可用，推送到 TTS 模型
                    if (
                        self.tts_model
                        and self.tts_model.supports_streaming_input
                    ):
                        tts_res = await self.tts_model.push(res_msg)
                        speech = tts_res.content

                    await self.print(res_msg, False, speech=speech)

            else:
                res_msg.content = res.content

            if self.tts_model:
                # 推送到 TTS 模型并阻塞以接收完整的语音合成结果
                tts_res = await self.tts_model.synthesize(res_msg)
                if self.tts_model.stream:
                    async for tts_chunk in tts_res:
                        speech = tts_chunk.content
                        await self.print(res_msg, False, speech=speech)
                else:
                    speech = tts_res.content

            await self.print(res_msg, True, speech=speech)

            return res_msg

    # pylint: disable=unused-argument
    async def handle_interrupt(
        self,
        msg: Msg | list[Msg] | None = None,
        structured_model: Type[BaseModel] | None = None,
    ) -> Msg:
        """当回复被用户或其他因素中断时的后处理逻辑

        Args:
            msg (`Msg | list[Msg] | None`, optional):
                输入给 agent 的消息
            structured_model (`Type[BaseModel] | None`, optional):
                所需的结构化输出模型
        """

        response_msg = Msg(
            self.name,
            "我注意到你打断了我。我能为你做什么？",
            "assistant",
            metadata={
                # 暴露此字段以指示中断
                "_is_interrupted": True,
            },
        )

        await self.print(response_msg, True)
        await self.memory.add(response_msg)
        return response_msg

    def generate_response(
        self,
        **kwargs: Any,
    ) -> ToolResponse:
        """
        通过此函数生成所需的结构化输出并返回
        """

        structured_output = None
        # 准备结构化输出
        if self._required_structured_model:
            try:
                # 使用消息的 metadata 字段存储结构化输出
                structured_output = (
                    self._required_structured_model.model_validate(
                        kwargs,
                    ).model_dump()
                )

            except ValidationError as e:
                return ToolResponse(
                    content=[
                        TextBlock(
                            type="text",
                            text=f"参数验证错误: {e}",
                        ),
                    ],
                    metadata={
                        "success": False,
                        "structured_output": {},
                    },
                )
        else:
            logger.warning(
                "在不需要结构化输出模型时调用了 generate_response 函数",
            )

        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text="成功生成响应",
                ),
            ],
            metadata={
                "success": True,
                "structured_output": structured_output,
            },
            is_last=True,
        )

    async def _retrieve_from_long_term_memory(
        self,
        msg: Msg | list[Msg] | None,
    ) -> None:
        """将从长期记忆中检索到的信息作为 Msg 对象插入短期记忆

        Args:
            msg (`Msg | list[Msg] | None`):
                输入给 agent 的消息
        """
        if self._static_control and msg:
            # 如果可用，从长期记忆中检索信息
            retrieved_info = await self.long_term_memory.retrieve(msg)
            if retrieved_info:
                retrieved_msg = Msg(
                    name="long_term_memory",
                    content="<long_term_memory>以下内容从长期记忆中检索，"
                    f"可能有用:\n{retrieved_info}</long_term_memory>",
                    role="user",
                )
                if self.print_hint_msg:
                    await self.print(retrieved_msg, True)
                await self.memory.add(retrieved_msg)

    async def _retrieve_from_knowledge(
        self,
        msg: Msg | list[Msg] | None,
    ) -> None:
        """如果可用，从 RAG 知识库中插入检索到的文档

        Args:
            msg (`Msg | list[Msg] | None`):
                输入给 agent 的消息
        """
        if self.knowledge and msg:
            # 准备用户输入查询
            query = None
            if isinstance(msg, Msg):
                query = msg.get_text_content()
            elif isinstance(msg, list):
                texts = []
                for m in msg:
                    text = m.get_text_content()
                    if text:
                        texts.append(text)
                query = "\n".join(texts)

            # 如果查询为空则跳过
            if not query:
                return

            # 如果启用，由 LLM 重写查询
            if self.enable_rewrite_query:
                stream_tmp = self.model.stream
                try:
                    rewrite_prompt = await self.formatter.format(
                        msgs=[
                            Msg("system", self.sys_prompt, "system"),
                            *await self.memory.get_memory(
                                exclude_mark=_MemoryMark.COMPRESSED
                                if self.compression_config
                                and self.compression_config.enable
                                else None,
                            ),
                            Msg(
                                "user",
                                "<system-hint>现在你需要重写上述用户查询，"
                                "使其更具体和简洁，以便知识检索。"
                                "例如，将查询 'what happened last day' 重写为 "
                                "'what happened on 2023-10-01'（假设今天是 2023-10-02）。"
                                "</system-hint>",
                                "user",
                            ),
                        ],
                    )
                    self.model.stream = False
                    res = await self.model(
                        rewrite_prompt,
                        structured_model=_QueryRewriteModel,
                    )
                    if res.metadata and res.metadata.get("rewritten_query"):
                        query = res.metadata["rewritten_query"]

                except Exception as e:
                    logger.warning(
                        "由于错误跳过查询重写: %s",
                        str(e),
                    )
                finally:
                    self.model.stream = stream_tmp

            docs: list[Document] = []
            for kb in self.knowledge:
                # 检索用户输入查询
                docs.extend(
                    await kb.retrieve(query=query),
                )
            if docs:
                # 按相关性得分重新排序
                docs = sorted(
                    docs,
                    key=lambda doc: doc.score or 0.0,
                    reverse=True,
                )
                # 准备检索到的知识字符串
                retrieved_msg = Msg(
                    name="user",
                    content=[
                        TextBlock(
                            type="text",
                            text=(
                                "<retrieved_knowledge>如果有帮助，"
                                "请使用知识库中的以下内容:\n"
                            ),
                        ),
                        *[_.metadata.content for _ in docs],
                        TextBlock(
                            type="text",
                            text="</retrieved_knowledge>",
                        ),
                    ],
                    role="user",
                )
                if self.print_hint_msg:
                    await self.print(retrieved_msg, True)
                await self.memory.add(retrieved_msg)

    async def _compress_memory_if_needed(self) -> None:
        """Compress the memory content if needed."""
        if (
            self.compression_config is None
            or not self.compression_config.enable
        ):
            return

        # Obtain the messages that have not been compressed yet
        to_compressed_msgs = await self.memory.get_memory(
            exclude_mark=_MemoryMark.COMPRESSED,
        )

        # keep the recent n messages uncompressed, note messages with tool
        #  use and result pairs should be kept together
        n_keep = 0
        accumulated_tool_call_ids = set()
        for i in range(len(to_compressed_msgs) - 1, -1, -1):
            msg = to_compressed_msgs[i]
            for block in msg.get_content_blocks("tool_result"):
                accumulated_tool_call_ids.add(block["id"])

            for block in msg.get_content_blocks("tool_use"):
                if block["id"] in accumulated_tool_call_ids:
                    accumulated_tool_call_ids.remove(block["id"])

            # Handle the tool use/result pairs
            if len(accumulated_tool_call_ids) == 0:
                n_keep += 1

            # Break if reach the number of messages to keep
            if n_keep >= self.compression_config.keep_recent:
                # Remove the messages that should be kept uncompressed
                to_compressed_msgs = to_compressed_msgs[:i]
                break

        # Skip compression if no messages to compress
        if not to_compressed_msgs:
            return

        # Calculate the token
        prompt = await self.formatter.format(
            [
                Msg("system", self.sys_prompt, "system"),
                *to_compressed_msgs,
            ],
        )
        n_tokens = await self.compression_config.agent_token_counter.count(
            prompt,
        )

        if n_tokens > self.compression_config.trigger_threshold:
            logger.info(
                "Memory compression is triggered (%d > "
                "threshold %d) for agent %s.",
                n_tokens,
                self.compression_config.trigger_threshold,
                self.name,
            )

            # The formatter used for compression
            compression_formatter = (
                self.compression_config.compression_formatter or self.formatter
            )

            # Prepare the prompt used to compress the memories
            compression_prompt = await compression_formatter.format(
                [
                    Msg("system", self.sys_prompt, "system"),
                    *to_compressed_msgs,
                    Msg(
                        "user",
                        self.compression_config.compression_prompt,
                        "user",
                    ),
                ],
            )

            # TODO: What if the compressed messages include multimodal blocks?
            # Use the specified compression model if provided
            compression_model = (
                self.compression_config.compression_model or self.model
            )
            res = await compression_model(
                compression_prompt,
                structured_model=(self.compression_config.summary_schema),
            )

            # Obtain the structured output from the model response
            last_chunk = None
            if compression_model.stream:
                async for chunk in res:
                    last_chunk = chunk
            else:
                last_chunk = res

            # Format the compressed memory summary
            if last_chunk.metadata:
                # Update the compressed summary in the memory storage
                await self.memory.update_compressed_summary(
                    self.compression_config.summary_template.format(
                        **last_chunk.metadata,
                    ),
                )

                # Mark the compressed messages in the memory storage
                await self.memory.update_messages_mark(
                    msg_ids=[_.id for _ in to_compressed_msgs],
                    new_mark=_MemoryMark.COMPRESSED,
                )

                logger.info(
                    "Finished compressing %d messages in agent %s.",
                    len(to_compressed_msgs),
                    self.name,
                )

            else:
                logger.warning(
                    "Failed to obtain compression summary from the model "
                    "structured output in agent %s.",
                    self.name,
                )
