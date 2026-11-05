"""统计层：与主实验同一套已冻结方法，输入换成验证运行的结果。

方法来源（冻结，不得新调）：
- 分层 discordant CP 联合界 —— ``scripts/sncs_p0_statistics_20261020.py`` 的 ``cp_envelope``，
  逐位复刻（同 H = 层数、同 α/4H 单侧尾、同"未知按最不利/最有利两向"的包络），
  并在 ``method_validation.json`` 里用 Newcombe(1998) Table III 的 6 个已发表区间自检。
- Newcombe10（method 10, score-type）—— 同文件 ``newcombe10``，同样先用已发表表自检。
- 配对精确检验 —— McNemar exact（binomtest on discordant pairs）。
- 资源：按题配对、难度分层的 cluster bootstrap，2,000 次、seed 20261020（方案 §5）。
- 重复子集：600 题 × 3 次/策略，报告逐次与题内一致性、按题聚类的策略差。

刻意不重写"更有利"的估计量：未知只走"全 0 / 最不利 / 最有利"三种预列口径，
且所有口径一起输出，调用方无法只取其中较窄的一条（方案 §5 末段）。
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
from scipy.stats import beta, binomtest
from statistics import NormalDist

SEED = 20261020
DRAWS = 2000
DELTA_MARGIN = -0.03


# ---------------------------------------------------------------------- 基础方法
def wilson(k, n):
    k = np.asarray(k, dtype=float)
    n = np.asarray(n, dtype=float)
    p = k / n
    z = NormalDist().inv_cdf(0.975)
    den = 1 + z * z / n
    center = (p + z * z / (2 * n)) / den
    radius = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return center - radius, center + radius


def newcombe10(e, f, g, h):
    """Newcombe (1998) method 10：配对比例差的 score 型区间。"""
    e, f, g, h = [np.asarray(v, dtype=float) for v in (e, f, g, h)]
    n = e + f + g + h
    if np.any(n <= 0):
        raise ValueError("nonempty table required")
    pa, pb = (e + f) / n, (e + g) / n
    la, ua = wilson(e + f, n)
    lb, ub = wilson(e + g, n)
    numerator = e * h - f * g
    numerator = np.where(numerator > 0, np.maximum(numerator - n / 2, 0), numerator)
    denominator = np.sqrt((e + f) * (g + h) * (e + g) * (f + h))
    corr = np.divide(numerator, denominator, out=np.zeros_like(numerator), where=denominator > 0)
    lo = pa - pb - np.sqrt(np.maximum(0, (pa - la) ** 2 + (ub - pb) ** 2 - 2 * corr * (pa - la) * (ub - pb)))
    hi = pa - pb + np.sqrt(np.maximum(0, (ua - pa) ** 2 + (pb - lb) ** 2 - 2 * corr * (ua - pa) * (pb - lb)))
    return lo, hi


_CP_CACHE: Dict[Tuple[int, int], Tuple[np.ndarray, np.ndarray]] = {}


def cp_lookup(n: int, H: int):
    """Clopper–Pearson 双侧临界值，单侧尾 α/4H（与冻结实现一致）。"""
    key = (int(n), int(H))
    if key in _CP_CACHE:
        return _CP_CACHE[key]
    tail = 0.05 / (4 * H)
    k = np.arange(n + 1)
    low = np.zeros(n + 1)
    high = np.ones(n + 1)
    low[1:] = beta.ppf(tail, k[1:], n - k[1:] + 1)
    high[:-1] = beta.ppf(1 - tail, k[:-1] + 1, n - k[:-1])
    _CP_CACHE[key] = (low, high)
    return low, high


def validate_method() -> List[Dict[str, Any]]:
    """用已发表区间自检实现（不许在换输入后才发现方法被改坏）。"""
    examples = [([36, 12, 2, 0], [.0569, .3404]), ([20, 12, 2, 16], [.0562, .3292]),
                ([35, 14, 0, 1], [.1461, .4175]), ([2, 98, 0, 0], [.9178, .9945]),
                ([27, 0, 0, 27], [-.0351, .0351]), ([54, 0, 0, 0], [-.0664, .0664])]
    checks = []
    for table, expected in examples:
        actual = [float(x) for x in newcombe10(*table)]
        if max(abs(a - b) for a, b in zip(actual, expected)) > 0.000051:
            raise AssertionError(f"Newcombe10 自检失败: {table} -> {actual} vs {expected}")
        flipped = newcombe10(table[0], table[2], table[1], table[3])
        if not np.allclose(flipped, [-actual[1], -actual[0]], atol=1e-12):
            raise AssertionError(f"Newcombe10 交换反对称性失败: {table}")
        checks.append({"counts": table, "published_4dp": expected, "computed": actual})
    # CP 与 wilson 边界检查
    for n in (1, 3, 10, 300, 1000):
        low, high = cp_lookup(n, 3)
        if not (0 <= low.min() <= high.max() <= 1):
            raise AssertionError(f"CP 临界值越界: n={n}")
    return checks


# ---------------------------------------------------------------------- 配对口径
def outcome_of(row: Optional[Dict[str, Any]], policy: str) -> Optional[int]:
    """把一条已评分行转成 0/1/None。

    policy:
      ``known``  未知（unknown/not_started）→ None，仅用于"已知评分子集"
      ``zero``   未知整体记 0（方案预列的敏感性口径 ①）
      ``low``    未知记 0（最不利：A=0, B=1 时 A 变 0、B 变 1）
      ``high``   未知记 1（最有利：A=1, B=0）
    """
    if row is None:
        return None if policy == "known" else (0 if policy in ("zero", "low") else 1)
    bucket = row["bucket"]
    if bucket in ("known", "zero_by_policy"):
        return int(row["value"] or 0)
    if policy == "known":
        return None
    if policy in ("zero", "low"):
        return 0
    return 1


def table_for_pair(a_row, b_row, policy: str):
    x, y = outcome_of(a_row, policy), outcome_of(b_row, policy)
    if x is None or y is None:
        return None
    return x, y


def cp_envelope(pairs: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """分层 discordant CP 联合界（未知按最不利/最有利双向包络）。"""
    by = defaultdict(list)
    for p in pairs:
        by[p["difficulty"]].append(p)
    n = len(pairs)
    if n == 0:
        return None
    low = high = pl = pu = 0.0
    for sub in by.values():
        ns = len(sub)
        lower, upper = cp_lookup(ns, len(by))
        poslo = neglo = poshi = neghi = 0
        unknown = 0
        for p in sub:
            x, y = outcome_of(p["a"], "known"), outcome_of(p["b"], "known")
            if x is None or y is None:
                unknown += 1
                poslo += (0 if x is None else x) > (1 if y is None else y)
                neglo += (0 if x is None else x) < (1 if y is None else y)
                poshi += (1 if x is None else x) > (0 if y is None else y)
                neghi += (1 if x is None else x) < (0 if y is None else y)
                continue
            poslo += x > y
            neglo += x < y
            poshi += x > y
            neghi += x < y
        low += ns / n * (lower[poslo] - upper[neglo])
        high += ns / n * (upper[poshi] - lower[neghi])
        pl += (poslo - neglo) / n
        pu += (poshi - neghi) / n
    return {"n": n, "difference_bounds": [pl, pu],
            "confidence_interval": [float(low), float(high)],
            "lower_gt_minus_003": bool(low > DELTA_MARGIN),
            "unknown_pairs": sum(1 for p in pairs
                                 if outcome_of(p["a"], "known") is None or outcome_of(p["b"], "known") is None),
            "strata": {k: len(v) for k, v in by.items()}}


def paired_table(pairs: Sequence[Dict[str, Any]], policy: str) -> List[int]:
    table = Counter()
    for p in pairs:
        t = table_for_pair(p["a"], p["b"], policy)
        if t is None:
            continue
        table[t] += 1
    return [table[(1, 1)], table[(1, 0)], table[(0, 1)], table[(0, 0)]]


def score_summary(pairs: Sequence[Dict[str, Any]], policy: str) -> Dict[str, Any]:
    e, f, g, h = paired_table(pairs, policy)
    n = e + f + g + h
    if not n:
        return {"n": 0}
    lo, hi = newcombe10(e, f, g, h)
    return {"n": n, "policy": policy,
            "table": {"both_correct": e, "a_only": f, "b_only": g, "neither_correct": h},
            "difference": (f - g) / n,
            "newcombe10_95": [float(lo), float(hi)],
            "post_test_lower_gt_minus_003": bool(lo > DELTA_MARGIN),
            "mcnemar_exact_zero_difference_p": float(binomtest(f, f + g, .5).pvalue) if f + g else 1.0}


def unstratified_cp(pairs: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    known = [(p["a"], p["b"]) for p in pairs
             if outcome_of(p["a"], "known") is not None and outcome_of(p["b"], "known") is not None]
    n = len(known)
    if n == 0:
        return None
    e, f, g, h = paired_table([{"a": a, "b": b, "difficulty": "all"} for a, b in known], "known")
    lower, upper = cp_lookup(n, 1)
    return {"n": n, "confidence_interval": [float(lower[f] - upper[g]), float(upper[f] - lower[g])],
            "lower_gt_minus_003": bool(lower[f] - upper[g] > DELTA_MARGIN)}


# ---------------------------------------------------------------------- 资源 bootstrap
def clustered_resource_bootstrap(pairs: Sequence[Dict[str, Any]], *,
                                 draws: int = DRAWS, seed: int = SEED) -> Dict[str, Any]:
    """按题配对、难度分层 cluster bootstrap（题是抽样单位，重复测量不是新题）。

    资源量取自轨迹的 meter_summary；缺失记 None 并单独报告覆盖率，不做插补。
    """

    def res(row, key):
        if row is None:
            return None
        v = (row.get("meter_summary") or {}).get(key)
        return v if isinstance(v, (int, float)) else None

    strata = defaultdict(list)
    for p in pairs:
        strata[p["difficulty"]].append(p)
    rng = np.random.default_rng(seed)
    keys = ["total_tokens", "n_calls"]
    out = {}
    for key in keys:
        per = []
        coverage_num = coverage_den = 0
        for p in pairs:
            for arm in ("a", "b"):
                coverage_den += 1
                if res(p[arm], key) is not None:
                    coverage_num += 1
        # 只对两臂都有记录的题计算差值
        diffs = []
        for p in pairs:
            ra, rb = res(p["a"], key), res(p["b"], key)
            if ra is None or rb is None:
                continue
            diffs.append((p["difficulty"], ra - rb))
        if not diffs:
            out[key] = {"coverage": 0.0, "n_pairs": 0}
            continue
        by_str = defaultdict(list)
        for d, v in diffs:
            by_str[d].append(v)
        boot = np.empty(draws)
        for i in range(draws):
            total = []
            for d, vals in by_str.items():
                vals = np.asarray(vals, dtype=float)
                idx = rng.integers(0, len(vals), len(vals))
                total.append(vals[idx].mean() * len(vals))
            boot[i] = float(np.sum(total) / len(diffs))
        obs = float(np.mean([v for _, v in diffs]))
        lo, hi = np.percentile(boot, [2.5, 97.5])
        out[key] = {"n_pairs": len(diffs), "coverage": coverage_num / coverage_den,
                    "observed_mean_adaptive_minus_plan_reflect": obs,
                    "cluster_bootstrap_95": [float(lo), float(hi)],
                    "draws": draws, "seed": seed,
                    "note": "差值单位与 meter_summary 一致；缺失记录不插补。"}
    return out


# ---------------------------------------------------------------------- 重复一致性
def repeat_consistency(rows: Sequence[Dict[str, Any]], source: str) -> Dict[str, Any]:
    """重复子集：600 题 × 3 次/策略（repeat 0 + repeat 1/2 合并成三次）。

    报告：逐次正确率、题内一致性（同一题三次是否同分）、按题聚类的策略差。
    缺失重复保留（缺失次数单独列），不仅分析"三次全完成"的题。
    入参是 pair_outcomes 的题对列表（每条含 a/b 两臂行，行内有 strategy 字段）。
    """
    # Fixed manifest population, including never-observed repeats.
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / 'data/followup/task_manifest.json').read_text(encoding='utf-8'))
    tasks = sorted(r['task_id'] for r in manifest if r['source'] == source and r['matched_repeat_subset'])
    lookup = {(p['task_id'], int(p['repeat_id'])): p for p in rows if p['source'] == source}
    low, high = [], []
    counts, consistency = {}, {}
    for rep in (0, 1, 2):
        c = Counter()
        for task in tasks:
            p = lookup.get((task, rep), {})
            for arm, strategy in [('a', 'adaptive'), ('b', 'plan_reflect')]:
                row = p.get(arm)
                value = outcome_of(row, 'known')
                c[strategy + '_planned'] += 1
                c[strategy + '_correct'] += int(value == 1)
                c[strategy + '_known_including_errors'] += int(value is not None)
                c[strategy + '_unknown_or_missing'] += int(value is None)
                c[strategy + '_execution_failures'] += int(row is not None and row.get('bucket') == 'zero_by_policy')
        counts[str(rep)] = dict(c)
    for task in tasks:
        lo = hi = 0.0
        for rep in (0, 1, 2):
            p = lookup.get((task, rep), {})
            a, b = outcome_of(p.get('a'), 'known'), outcome_of(p.get('b'), 'known')
            lo += (0 if a is None else a) - (1 if b is None else b)
            hi += (1 if a is None else a) - (0 if b is None else b)
        low.append(lo / 3); high.append(hi / 3)
    for arm, strategy in [('a', 'adaptive'), ('b', 'plan_reflect')]:
        full = identical = 0
        for task in tasks:
            values = [outcome_of(lookup.get((task, rep), {}).get(arm), 'known') for rep in (0, 1, 2)]
            if None not in values:
                full += 1; identical += int(len(set(values)) == 1)
        consistency[strategy] = {'tasks_with_three_known_repeats': full,
                                 'identical_outcome_rate': identical / full if full else None,
                                 'conditional_on_three_known': True}
    rng = np.random.default_rng(SEED)
    outer = None
    if tasks:
        idx = rng.integers(0, len(tasks), size=(DRAWS, len(tasks)))
        outer = [float(np.percentile(np.array(low)[idx].mean(axis=1), 2.5)),
                 float(np.percentile(np.array(high)[idx].mean(axis=1), 97.5))]
    return {'source': source, 'n_tasks_in_subset': len(tasks), 'per_repeat_counts': counts,
            'within_task_consistency': consistency,
            'clustered_task_level_difference': {'n_tasks': len(tasks),
                'difference_bounds': [float(np.mean(low)), float(np.mean(high))] if tasks else None,
                'cluster_bootstrap_outer95': outer, 'draws': DRAWS, 'seed': SEED},
            'note': 'Fixed 300 tasks per source; errors remain zero; all missing/unknown repeats retain bounds.'}


# ---------------------------------------------------------------------- 主分析
def analyze(pairs_all: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    validate_method()
    report: Dict[str, Any] = {"method_validation": "passed", "seed": SEED, "draws": DRAWS,
                              "margin": -0.03, "sources": {}}
    for source in sorted({p["source"] for p in pairs_all}):
        pairs = [p for p in pairs_all if p["source"] == source and int(p["repeat_id"]) == 0]
        if not pairs:
            continue
        primary = cp_envelope(pairs)
        known_pairs = [p for p in pairs
                       if outcome_of(p["a"], "known") is not None and outcome_of(p["b"], "known") is not None]
        completed_pairs = [p for p in pairs
                           if (p["a"] or {}).get("status") == "completed"
                           and (p["b"] or {}).get("status") == "completed"
                           and ((p['a'] or {}).get('final_answer_nonempty') or str((p['a'] or {}).get('final_answer') or '').strip())
                           and ((p['b'] or {}).get('final_answer_nonempty') or str((p['b'] or {}).get('final_answer') or '').strip())]
        report["sources"][source] = {
            "primary_stratified_cp": primary,
            "unstratified_cp_known_only": unstratified_cp(pairs),
            "sensitivity": {
                "unknown_zero_cp": cp_envelope_zero(pairs),
                "unknown_zero_newcombe10": score_summary(pairs, "zero"),
                "known_labels_newcombe10": score_summary(pairs, "known"),
                "complete_execution_cp": cp_envelope(completed_pairs),
                "complete_execution_known_newcombe10": score_summary(completed_pairs, "known"),
            },
            "denominators": {
                "planned_pairs": len(pairs),
                "started_pairs": sum(1 for p in pairs
                                     if p['a'] is not None and p['a'].get('bucket') != 'not_started'
                                     or p['b'] is not None and p['b'].get('bucket') != 'not_started'),
                "known_pairs": len(known_pairs),
                "started_but_unknown_pairs": len(pairs) - len(known_pairs)
                - sum(1 for p in pairs if (p["a"] or {}).get("bucket") == "not_started"
                      and (p["b"] or {}).get("bucket") == "not_started"),
            },
            "resources": clustered_resource_bootstrap(pairs),
        }
    report["repeat_consistency"] = {s: repeat_consistency(pairs_all, s)
                                    for s in sorted({p["source"] for p in pairs_all})}
    report["all_sources_pooled"] = _pooled(pairs_all)
    return report


def _pooled(pairs_all: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """两源**不合池做主结论**，这里只给"合并的示意值"并显式标注不得用于判定。

    方案 §5 要求两源分别报告 95% 区间；合并区间不构成"两源同时 95%"的保障。
    """
    pairs = [p for p in pairs_all if int(p["repeat_id"]) == 0]
    if not pairs:
        return {}
    return {"role": "descriptive_only_not_for_primary_claim",
            "n_pairs": len(pairs),
            "stratified_cp": cp_envelope(pairs),
            "known_labels_newcombe10": score_summary(pairs, "known"),
            "unknown_zero_newcombe10": score_summary(pairs, "zero")}


def cp_envelope_zero(pairs: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """敏感性口径 ①：未知整体记 0 的分层 CP（不包络，直接点值代入）。"""
    by = defaultdict(list)
    for p in pairs:
        by[p["difficulty"]].append(p)
    n = len(pairs)
    if n == 0:
        return None
    low = high = 0.0
    for sub in by.values():
        ns = len(sub)
        lower, upper = cp_lookup(ns, len(by))
        pos = neg = 0
        for p in sub:
            x = outcome_of(p["a"], "zero")
            y = outcome_of(p["b"], "zero")
            pos += x > y
            neg += x < y
        low += ns / n * (lower[pos] - upper[neg])
        high += ns / n * (upper[pos] - lower[neg])
    return {"n": n, "confidence_interval": [float(low), float(high)],
            "lower_gt_minus_003": bool(low > DELTA_MARGIN), "policy": "unknown_zero"}
