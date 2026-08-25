"""Model -> weight-family registry. The single source of truth for gate G1.

Grouping by **vendor** is the mistake this file exists to prevent. Three
platforms serving the same open checkpoint are one family, not three; a
gateway that de-duplicates by vendor will let shopscout's three-lab jury
collapse into three copies of the same model while every dashboard shows a
compliant, diverse jury. Nothing raises. The books look right.

Consequently:
  - keys are full model ids as tenants actually write them, vendor prefix
    included, because that is what arrives on the wire;
  - values name the *checkpoint line*, not the serving platform;
  - `basis` is mandatory — a table nobody can audit is a table nobody will
    maintain, and a stale entry here silently weakens G1;
  - anything not listed is UNKNOWN_FAMILY. Guessing is worse than not
    knowing: a wrong guess can make two identical models look distinct,
    which is precisely the failure G1 is supposed to catch.

This table lives in code, not in Settings or a YAML file, for the same
reason medscope keeps CRITICAL_LABELS out of its config: two copies drift
in spelling, and the drift disables a gate without failing anything.
"""

from dataclasses import dataclass

UNKNOWN_FAMILY = "unknown"


@dataclass(frozen=True)
class FamilyRecord:
    family: str
    basis: str


FAMILIES: dict[str, FamilyRecord] = {
    "zai/glm-4.6": FamilyRecord(
        family="glm",
        basis="Zhipu GLM line, served from z.ai's own OpenAI-compatible endpoint",
    ),
    "zai/glm-4.7": FamilyRecord(
        family="glm",
        basis="same GLM checkpoint line as glm-4.6, later release",
    ),
    "siliconflow/Qwen/Qwen3-8B": FamilyRecord(
        family="qwen3",
        basis="Alibaba Qwen3 open weights, hosted by SiliconFlow",
    ),
    "siliconflow/Qwen/Qwen3-235B-A22B": FamilyRecord(
        family="qwen3",
        basis="Alibaba Qwen3 open weights (MoE), hosted by SiliconFlow",
    ),
    "dashscope/qwen3-235b-a22b": FamilyRecord(
        family="qwen3",
        basis="the same Qwen3 checkpoint as the SiliconFlow entry, first-party hosting — "
        "the pair that makes vendor-based grouping wrong",
    ),
    "siliconflow/deepseek-ai/DeepSeek-V3": FamilyRecord(
        family="deepseek-v3",
        basis="DeepSeek-V3 open weights, hosted by SiliconFlow",
    ),
}


def family_of(model: str) -> str:
    """Weight family for a model id, or UNKNOWN_FAMILY if we do not know."""
    record = FAMILIES.get(model)
    return record.family if record else UNKNOWN_FAMILY


def distinct_families(models: list[str]) -> int:
    """How many *known* distinct weight families these models span.

    Unknowns are excluded rather than counted as one-each: two unrecognised
    model ids are not evidence of two families, and counting them that way
    would let an unmaintained registry satisfy a diversity requirement.
    """
    families = {family_of(m) for m in models}
    families.discard(UNKNOWN_FAMILY)
    return len(families)
