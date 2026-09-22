"""Agent-style LLM review engine (``llm-agent``).

与 ``llm-direct``（单次 diff prompt）不同，``LLMAgentEngine`` 以多轮
function-calling 循环运行：模型先看 diff，再通过只读工具（读文件 /
目录树 / blame / 代码搜索 / 文件历史）调查提交的影响范围，最后才输出
findings JSON。目的：让模型掌握跨文件上下文，减少纯 diff 审查的误判。
"""

from engines.llm_agent.engine import LLMAgentEngine

__all__ = ["LLMAgentEngine"]
