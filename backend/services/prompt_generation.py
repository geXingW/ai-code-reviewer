"""负样本提示词生成（application service）。

职责：把"已确认的误报样本"转成一段可注入审查 system prompt 的负样本说明。

放在 service 层而不是路由里，是为了让 HTTP 层只负责鉴权 / 校验 / 响应映射：
提示词模板、样本分组渲染、LLM 调用与失败语义都属于业务逻辑。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from engines import ProviderConfig
from llm import ChatMessage, LLMError, build_provider
from models.negative_example import NegativeExample
from models.provider import Provider

logger = logging.getLogger(__name__)

NEGATIVE_PROMPT_SYSTEM_PROMPT = """\
你是专业的代码审查提示词工程师。你的任务是根据已确认的误报样本，生成一段精准、专业的"负样本提示词"。

这段提示词将被注入到代码审查 LLM 的 system prompt 中，用于告诉审查模型
"哪些模式是已经确认的误报，不要再报"。

要求：
1. 按规则（rule_id）分组归纳，每组提炼出该规则下的误报模式特征
2. 语言专业、精准，描述"什么场景/什么形态的代码属于误报"而非"某条具体代码是误报"
3. 结构清晰，使用"规则名 + 误报模式描述"的条目化格式
4. 直接输出提示词正文，不要包含解释、开场白、结束语等多余内容
5. 输出中文
"""


class PromptGenerationError(RuntimeError):
    """负样本提示词生成失败（上游 LLM 异常或返回空内容）。"""

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        # 面向用户的补充说明（可能含上游错误摘要），由路由层拼进 5xx detail。
        self.detail = detail


def format_negative_examples_by_rule(examples: Sequence[NegativeExample]) -> str:
    """把负样本按 ``rule_id`` 分组渲染成 LLM 输入文本。

    分组保持首次出现顺序（样本本身已按 ``approved_at DESC`` 排序），
    每组内逐条输出 ``code_snippet`` 与误报说明。
    """

    grouped: dict[str, list[NegativeExample]] = {}
    for example in examples:
        grouped.setdefault(example.rule_id, []).append(example)

    blocks: list[str] = []
    for rule_id, items in grouped.items():
        lines = [f"## 规则：{rule_id}"]
        for index, item in enumerate(items, start=1):
            lines.extend(
                [
                    "",
                    f"### 样本 {index}",
                    "代码片段：",
                    "```",
                    item.code_snippet,
                    "```",
                    f"误报说明：{item.explanation or '无'}",
                ]
            )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


async def generate_negative_prompt(
    *,
    examples: Sequence[NegativeExample],
    provider: Provider,
) -> str:
    """根据已批准的负样本生成项目级负样本提示词正文。

    Args:
        examples: 该项目已批准的负样本（调用方负责过滤 project_id / 条数）。
        provider: 用于生成文本的 LLM Provider。

    Returns:
        提示词正文（已 strip，保证非空）。

    Raises:
        PromptGenerationError: LLM 调用失败或返回空内容。
    """

    user_prompt = (
        "以下是已确认的误报样本列表（按规则分组）：\n\n"
        f"{format_negative_examples_by_rule(examples)}\n\n"
        "请根据以上样本生成负样本提示词。"
    )
    # 负样本提示词是纯文本输出：不启用 JSON 模式；走非流式调用，
    # 显式关闭思考模式（qwen3 混合思考模型非流式默认思考会被网关 400 拒绝）。
    provider_config = ProviderConfig(
        provider_id=provider.id,
        provider_type=provider.protocol,
        base_url=provider.base_url,
        model=provider.model,
        api_key=provider.api_key,
        temperature=provider.temperature,
        max_tokens=provider.max_tokens,
        extra={
            "default_json_mode": False,
            "extra_body": {"enable_thinking": False},
        },
    )
    try:
        llm_provider = build_provider(provider_config)
        response = await llm_provider.chat(
            [
                ChatMessage(role="system", content=NEGATIVE_PROMPT_SYSTEM_PROMPT),
                ChatMessage(role="user", content=user_prompt),
            ]
        )
    except LLMError as exc:
        logger.warning("negative prompt generation failed: %s", exc)
        raise PromptGenerationError("negative prompt generation failed", detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - 其它未预期异常同样归为生成失败
        logger.warning("negative prompt generation failed (unexpected)", exc_info=True)
        raise PromptGenerationError("negative prompt generation failed") from exc

    content = response.content.strip()
    if not content:
        raise PromptGenerationError(
            "negative prompt generation returned empty content",
            detail="模型返回空内容",
        )
    return content
