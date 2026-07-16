"""
scorer_cn.py — scores Nowcoder job postings against Althea's China-market
profile, via the same local Claude Code CLI mechanism as scorer.py (see
claude_code_client.py). Kept as a separate module (rather than parameterizing
scorer.py) so the US pipeline's prompt/profile can't accidentally regress
while this is built out.
"""

import logging
from pathlib import Path

from claude_code_client import run_prompt, ClaudeCodeError

logger = logging.getLogger(__name__)

_profile_path = Path(__file__).parent.parent / "config" / "candidate_profile_cn.txt"
CANDIDATE_PROFILE = _profile_path.read_text(encoding="utf-8")

CHUNK_SIZE = 40
MAX_DESCRIPTION_CHARS = 3000

SYSTEM_PROMPT = """你是一名帮候选人评估校招岗位匹配度的猎头顾问。
你会收到候选人档案和一批中国校园招聘/实习转正岗位。请为每个岗位打分，并按给定的JSON schema返回结果。

打分标准：
90-100：极佳匹配 —— 岗位方向与候选人技能、经验高度吻合
75-89： 很好匹配 —— 大部分要求符合，只有小差距
60-74： 一般匹配 —— 有相关背景但存在明显差距
40-59： 较弱匹配 —— 有一些重叠但方向明显不符
0-39：  很差匹配 —— 岗位类型或资历层级完全不对

关键判断因素：
- 严重扣分：需要3年以上工作经验的岗位、明确写"社招"的岗位、高级/资深/专家/总监级别岗位
  —— 候选人是应届毕业生，只适合校招/实习(可转正)类岗位。
- 严重扣分：产品经理(PM)类岗位 —— 候选人有独立的PM求职渠道，不需要这条自动化流程重复覆盖，
  即使岗位描述里有一些AI/数据相关内容，只要核心职责是产品经理，也应给低分。
- 最高奖励(90+)：AI应用开发/AI Agent开发/大模型应用工程师类岗位 —— 与候选人在Whelix的
  多智能体系统开发经验（Gemini SDK、工具调用、Agent架构）直接对口。
- 强烈奖励(75-89)：数据分析师/数据科学家/数据挖掘工程师类岗位 —— 与候选人的因果推断、
  增量建模、决策树/随机森林建模背景直接对口。
- 中等奖励(60-74)：偏应用落地方向的算法工程师岗位（推荐算法、NLP应用、大模型算法）——
  候选人背景是应用型而非纯理论研究型，纯底层算法研究/传统CV岗位酌情降低匹配度。
- 中性：其他技术类岗位（后端、前端、测试等）——候选人有一定编程基础但这不是核心优势方向。

输入列表中的每个岗位都必须在"results"中出现恰好一次，用其"index"标识。
"""

RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "score": {"type": "integer"},
                    "match_reasons": {"type": "array", "items": {"type": "string"}},
                    "concerns": {"type": "array", "items": {"type": "string"}},
                    "summary": {"type": "string"},
                },
                "required": ["index", "score", "match_reasons", "concerns", "summary"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


def _build_prompt(jobs: list[dict]) -> str:
    postings = []
    for i, job in enumerate(jobs):
        description = job.get("description", "无岗位描述")
        if len(description) > MAX_DESCRIPTION_CHARS:
            description = description[:MAX_DESCRIPTION_CHARS] + "...[截断]"
        postings.append(
            f"[{i}] 公司：{job['company']}\n"
            f"职位：{job['title']}\n"
            f"岗位类别：{job.get('career_job_name', '')}\n"
            f"地点：{job['location']}\n"
            f"描述：{description}"
        )
    jobs_block = "\n\n---\n\n".join(postings)
    return (
        f"候选人档案：\n{CANDIDATE_PROFILE}\n\n"
        f"===\n\n岗位列表（共{len(jobs)}个）：\n\n{jobs_block}"
    )


def _score_chunk(jobs: list[dict]) -> None:
    try:
        data = run_prompt(_build_prompt(jobs), SYSTEM_PROMPT, RESULT_SCHEMA)
        results_by_index = {r["index"]: r for r in data.get("results", [])}
    except ClaudeCodeError as e:
        logger.warning(f"Scoring chunk of {len(jobs)} jobs failed: {e}")
        results_by_index = {}

    for i, job in enumerate(jobs):
        result = results_by_index.get(i)
        if result is None:
            job["score"] = 0
            job["match_reasons"] = []
            job["concerns"] = ["评分失败——需人工检查"]
            job["summary"] = "无法为该岗位评分。"
        else:
            job["score"] = int(result.get("score", 0))
            job["match_reasons"] = result.get("match_reasons", [])
            job["concerns"] = result.get("concerns", [])
            job["summary"] = result.get("summary", "")


def score_jobs_batch(jobs: list[dict], chunk_size: int = CHUNK_SIZE) -> list[dict]:
    for start in range(0, len(jobs), chunk_size):
        chunk = jobs[start:start + chunk_size]
        logger.info(
            f"Scoring jobs {start + 1}-{start + len(chunk)} of {len(jobs)} "
            f"(chunk of {len(chunk)})..."
        )
        _score_chunk(chunk)
    return jobs
