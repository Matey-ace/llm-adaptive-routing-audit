from __future__ import annotations
from collections import defaultdict
from typing import Dict,List,Any

def pair_outcomes(rows: List[Dict[str, Any]], queue=None) -> List[Dict[str, Any]]:
    """把逐轨迹行配成题对：每条 = {"key","source","difficulty","repeat_id","a","b","pair_id"}。

    a = adaptive，b = plan_reflect。某臂缺失（例如 not_started 未写）→ 该臂记 None，
    由统计层按"未知"处理，绝不静默丢弃。
    """
    by_pair: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
    meta: Dict[str, Dict[str, Any]] = {}
    for r in queue or []:
        by_pair[r['pair_id']]
        meta[r['pair_id']] = {'source': r['source'], 'difficulty': r['difficulty'],
                              'repeat_id': int(r['repeat_id']), 'task_id': r['task_id']}
    for r in rows:
        by_pair[r["pair_id"]][r["strategy"]] = r
        meta[r["pair_id"]] = {"source": r["source"], "difficulty": r["difficulty"],
                              "repeat_id": int(r["repeat_id"]), "task_id": r["task_id"]}
    out = []
    for pair_id, arms in by_pair.items():
        m = meta[pair_id]
        item = {"key": pair_id, "source": m["source"], "difficulty": m["difficulty"],
                "repeat_id": int(m["repeat_id"]), "task_id": m["task_id"],
                "a": arms.get("adaptive"), "b": arms.get("plan_reflect")}
        # 逐臂策略名显式带上，供重复一致性/资源统计使用（缺失臂为 None）
        item["a_strategy"] = "adaptive" if "adaptive" in arms else None
        item["b_strategy"] = "plan_reflect" if "plan_reflect" in arms else None
        out.append(item)
    out.sort(key=lambda p: (p["source"], p["repeat_id"], p["difficulty"], p["key"]))
    return out
