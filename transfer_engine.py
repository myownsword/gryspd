from typing import List, Dict, Tuple, Optional
from datetime import datetime, timedelta
from database import (
    get_transactions, get_internal_transfer_txn_ids
)

TRANSFER_KEYWORDS_SOURCE = [
    "转账", "转出", "汇款", "划转", "转存", "转款",
    "信用卡还款", "还款", "还信用卡",
    "提现", "零钱提现", "微信提现", "支付宝提现",
    "转入", "入账", "到账",
]

TRANSFER_KEYWORDS_DEST = [
    "转账", "转入", "入账", "到账", "收款",
    "提现", "零钱提现", "微信提现", "支付宝提现",
]

REIMBURSEMENT_KEYWORDS_SOURCE = [
    "报销", "报销款", "差旅费报销", "费用报销",
    "退款", "退货", "退款到账",
]

REIMBURSEMENT_KEYWORDS_DEST = [
    "报销", "报销款", "差旅费", "费用报销",
    "退款", "退货",
]

INTERNAL_KEYWORDS_ALL = list(set(
    TRANSFER_KEYWORDS_SOURCE + TRANSFER_KEYWORDS_DEST +
    REIMBURSEMENT_KEYWORDS_SOURCE + REIMBURSEMENT_KEYWORDS_DEST
))


def find_internal_transfer_candidates(
    date_window_days: int = 3,
    amount_tolerance: float = 0.01
) -> List[Dict]:
    all_txns = get_transactions()
    matched_ids = get_internal_transfer_txn_ids()

    available_txns = [t for t in all_txns if t["id"] not in matched_ids]

    income_txns = [t for t in available_txns if t["type"] == "income"]
    expense_txns = [t for t in available_txns if t["type"] == "expense"]

    candidates = []
    used_txn_ids = set()

    for inc_txn in income_txns:
        if inc_txn["id"] in used_txn_ids:
            continue

        inc_desc = inc_txn["description"]
        inc_amount = inc_txn["amount"]
        inc_date = datetime.strptime(inc_txn["date"], "%Y-%m-%d")

        has_income_kw = any(kw in inc_desc for kw in INTERNAL_KEYWORDS_ALL)

        for exp_txn in expense_txns:
            if exp_txn["id"] in used_txn_ids:
                continue

            exp_desc = exp_txn["description"]
            exp_amount = exp_txn["amount"]
            exp_date = datetime.strptime(exp_txn["date"], "%Y-%m-%d")

            has_expense_kw = any(kw in exp_desc for kw in INTERNAL_KEYWORDS_ALL)

            if not has_income_kw and not has_expense_kw:
                continue

            date_diff = abs((inc_date - exp_date).days)
            if date_diff > date_window_days:
                continue

            amount_diff = abs(inc_amount - exp_amount)
            if amount_diff > amount_tolerance:
                continue

            transfer_type = "transfer"
            confidence = 0.0
            match_reasons = []

            if amount_diff <= 0.01:
                confidence += 0.4
                match_reasons.append("金额完全匹配")
            elif amount_diff <= 1:
                confidence += 0.2
                match_reasons.append(f"金额接近 (差¥{amount_diff:.2f})")

            if date_diff == 0:
                confidence += 0.3
                match_reasons.append("同一天")
            elif date_diff == 1:
                confidence += 0.2
                match_reasons.append(f"相差{date_diff}天")
            else:
                confidence += 0.1
                match_reasons.append(f"相差{date_diff}天")

            if has_income_kw and has_expense_kw:
                confidence += 0.3
                match_reasons.append("双方都有关键词")
            elif has_income_kw or has_expense_kw:
                confidence += 0.15
                match_reasons.append("一方有关键词")

            is_reimbursement = (
                any(kw in inc_desc for kw in REIMBURSEMENT_KEYWORDS_DEST) or
                any(kw in exp_desc for kw in REIMBURSEMENT_KEYWORDS_SOURCE)
            )
            if is_reimbursement:
                transfer_type = "reimbursement"

            cross_month = inc_txn["date"][:7] != exp_txn["date"][:7]
            if cross_month:
                confidence -= 0.1
                match_reasons.append("跨月匹配")

            candidates.append({
                "type": transfer_type,
                "confidence": round(confidence, 2),
                "reasons": match_reasons,
                "cross_month": cross_month,
                "amount_match": amount_diff <= amount_tolerance,
                "amount_diff": round(amount_diff, 2),
                "items": [
                    {
                        "transaction": exp_txn,
                        "role": "source" if transfer_type == "transfer" else "expense"
                    },
                    {
                        "transaction": inc_txn,
                        "role": "destination" if transfer_type == "transfer" else "refund"
                    }
                ],
                "date_range": [
                    min(inc_txn["date"], exp_txn["date"]),
                    max(inc_txn["date"], exp_txn["date"])
                ]
            })

            used_txn_ids.add(inc_txn["id"])
            used_txn_ids.add(exp_txn["id"])

    candidates.sort(key=lambda x: x["confidence"], reverse=True)
    return candidates


def find_transfers_with_keywords() -> List[Dict]:
    all_txns = get_transactions()
    matched_ids = get_internal_transfer_txn_ids()

    available_txns = [t for t in all_txns if t["id"] not in matched_ids]

    keyword_txns = []
    for txn in available_txns:
        matched_kws = [kw for kw in INTERNAL_KEYWORDS_ALL if kw in txn["description"]]
        if matched_kws:
            keyword_txns.append({
                "transaction": txn,
                "matched_keywords": matched_kws
            })

    return keyword_txns


def get_unmatched_internal_keyword_txns() -> List[Dict]:
    candidates = find_internal_transfer_candidates()
    candidate_txn_ids = set()
    for c in candidates:
        for item in c["items"]:
            candidate_txn_ids.add(item["transaction"]["id"])

    keyword_txns = find_transfers_with_keywords()
    unmatched = []
    for kt in keyword_txns:
        if kt["transaction"]["id"] not in candidate_txn_ids:
            unmatched.append(kt)

    return unmatched


def auto_detect_transfer_type(txn: Dict) -> Optional[str]:
    desc = txn["description"]
    is_income = txn["type"] == "income"

    is_transfer_source = any(kw in desc for kw in TRANSFER_KEYWORDS_SOURCE)
    is_transfer_dest = any(kw in desc for kw in TRANSFER_KEYWORDS_DEST)
    is_reimb_source = any(kw in desc for kw in REIMBURSEMENT_KEYWORDS_SOURCE)
    is_reimb_dest = any(kw in desc for kw in REIMBURSEMENT_KEYWORDS_DEST)

    if is_income:
        if is_reimb_dest:
            return "reimbursement_refund"
        if is_transfer_dest:
            return "transfer_destination"
    else:
        if is_reimb_source:
            return "reimbursement_expense"
        if is_transfer_source:
            return "transfer_source"

    return None


def suggest_description_similarity(desc1: str, desc2: str) -> float:
    words1 = set(desc1)
    words2 = set(desc2)
    if not words1 or not words2:
        return 0.0
    intersection = words1 & words2
    union = words1 | words2
    return len(intersection) / len(union)
