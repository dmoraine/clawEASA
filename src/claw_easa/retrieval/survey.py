from __future__ import annotations


def shape_survey_results(
    rows: list[dict],
    core_threshold: float = 0.3,
    max_core: int = 10,
    max_supporting: int = 10,
) -> dict:
    if not rows:
        return {"core_refs": [], "supporting_refs": []}

    core_refs: list[dict] = []
    supporting_refs: list[dict] = []

    for row in rows:
        score = float(row.get("hybrid_score", 0))
        if score >= core_threshold and len(core_refs) < max_core:
            core_refs.append(row)
        elif len(supporting_refs) < max_supporting:
            supporting_refs.append(row)

    return {"core_refs": core_refs, "supporting_refs": supporting_refs}
