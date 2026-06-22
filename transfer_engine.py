from typing import List, Dict, Tuple, Optional
from datetime import datetime, timedelta
from database import (
    get_transactions, get_internal_transfer_txn_ids
)

TRANSFER_SOURCE_KEYWORDS = [
    "转账", "转出", "汇款", "划转", "转存", "转款",
    "信用卡还款", "还款", "还信用卡",
    "提现", "微信提现", "支付宝提现",
]

TRANSFER_DEST_KEYWORDS = [
    "转账", "转入", "入账", "到账", "收款",
    "零钱提现", "微信提现", "支付宝提现",
]

EXPENSE_EXPENSE_SOURCE_KEYWORDS = [
    "转出", "汇款", "划转", "转存", "转款",
    "提现", "微信提现", "支付宝提现", "零钱提现",
]

EXPENSE_EXPENSE_DEST_KEYWORDS = [
    "信用卡还款", "还款", "还信用卡", "自动扣款",
    "自动还款", "信用卡自动还款", "信用卡自动扣款",
    "信用卡还款日", "扣款", "代扣",
]

INCOME_INCOME_SOURCE_KEYWORDS = [
    "零钱提现", "微信提现", "支付宝提现", "提现",
    "转出", "转出到银行卡",
]

INCOME_INCOME_DEST_KEYWORDS = [
    "微信提现到银行卡", "支付宝提现到银行卡", "提现到银行卡",
    "入账", "到账", "转入", "收款", "银行卡到账",
]

REIMBURSEMENT_KEYWORDS_SOURCE = [
    "报销", "报销款", "差旅费报销", "费用报销",
    "退款", "退货", "退款到账",
    "差旅费", "出差", "垫付", "采购",
]

REIMBURSEMENT_KEYWORDS_DEST = [
    "报销", "报销款", "差旅费报销", "费用报销",
    "退款", "退货", "退款到账",
    "报销到账", "报销单",
]

INTERNAL_KEYWORDS_ALL = list(set(
    TRANSFER_SOURCE_KEYWORDS + TRANSFER_DEST_KEYWORDS +
    EXPENSE_EXPENSE_SOURCE_KEYWORDS + EXPENSE_EXPENSE_DEST_KEYWORDS +
    INCOME_INCOME_SOURCE_KEYWORDS + INCOME_INCOME_DEST_KEYWORDS +
    REIMBURSEMENT_KEYWORDS_SOURCE + REIMBURSEMENT_KEYWORDS_DEST
))


def _has_keyword(desc: str, kw_list: List[str]) -> bool:
    return any(kw in desc for kw in kw_list)


def _score_role_keywords(desc: str, source_kws: List[str], dest_kws: List[str]) -> Tuple[int, int]:
    source_score = 0
    dest_score = 0
    for kw in source_kws:
        if kw in desc:
            source_score += len(kw)
    for kw in dest_kws:
        if kw in desc:
            dest_score += len(kw)
    return source_score, dest_score


def _infer_roles(txn_a: Dict, txn_b: Dict, transfer_type: str) -> Tuple[str, str]:
    if txn_a["type"] != txn_b["type"]:
        exp_txn = txn_a if txn_a["type"] == "expense" else txn_b
        inc_txn = txn_a if txn_a["type"] == "income" else txn_b
        if transfer_type == "reimbursement":
            a_role = "expense" if exp_txn["id"] == txn_a["id"] else "refund"
            b_role = "refund" if a_role == "expense" else "expense"
        else:
            a_role = "source" if exp_txn["id"] == txn_a["id"] else "destination"
            b_role = "destination" if a_role == "source" else "source"
        return (a_role, b_role)

    same_type = txn_a["type"]
    a_desc = txn_a["description"]
    b_desc = txn_b["description"]

    if same_type == "expense":
        a_src, a_dst = _score_role_keywords(a_desc, EXPENSE_EXPENSE_SOURCE_KEYWORDS, EXPENSE_EXPENSE_DEST_KEYWORDS)
        b_src, b_dst = _score_role_keywords(b_desc, EXPENSE_EXPENSE_SOURCE_KEYWORDS, EXPENSE_EXPENSE_DEST_KEYWORDS)

        a_net = a_src - a_dst
        b_net = b_src - b_dst

        if a_net > b_net:
            return ("source", "destination")
        elif b_net > a_net:
            return ("destination", "source")
    else:
        a_src, a_dst = _score_role_keywords(a_desc, INCOME_INCOME_SOURCE_KEYWORDS, INCOME_INCOME_DEST_KEYWORDS)
        b_src, b_dst = _score_role_keywords(b_desc, INCOME_INCOME_SOURCE_KEYWORDS, INCOME_INCOME_DEST_KEYWORDS)

        a_net = a_src - a_dst
        b_net = b_src - b_dst

        if a_net > b_net:
            return ("source", "destination")
        elif b_net > a_net:
            return ("destination", "source")

    if txn_a["date"] <= txn_b["date"]:
        return ("source", "destination")
    return ("destination", "source")


def _infer_type_from_pair(txn_a: Dict, txn_b: Dict) -> str:
    a_desc = txn_a["description"]
    b_desc = txn_b["description"]
    has_reimb = (
        _has_keyword(a_desc, REIMBURSEMENT_KEYWORDS_SOURCE) or
        _has_keyword(a_desc, REIMBURSEMENT_KEYWORDS_DEST) or
        _has_keyword(b_desc, REIMBURSEMENT_KEYWORDS_SOURCE) or
        _has_keyword(b_desc, REIMBURSEMENT_KEYWORDS_DEST)
    )
    if has_reimb:
        exp_txn = txn_a if txn_a["type"] == "expense" else (txn_b if txn_b["type"] == "expense" else None)
        inc_txn = txn_a if txn_a["type"] == "income" else (txn_b if txn_b["type"] == "income" else None)
        if exp_txn and inc_txn:
            exp_has_reimb = _has_keyword(exp_txn["description"], REIMBURSEMENT_KEYWORDS_SOURCE)
            inc_has_reimb = _has_keyword(inc_txn["description"], REIMBURSEMENT_KEYWORDS_DEST)
            if exp_has_reimb or inc_has_reimb:
                return "reimbursement"
    return "transfer"


def find_internal_transfer_candidates(
    date_window_days: int = 3,
    amount_tolerance: float = 0.01
) -> List[Dict]:
    all_txns = get_transactions()
    matched_ids = get_internal_transfer_txn_ids()
    available_txns = [t for t in all_txns if t["id"] not in matched_ids]

    candidates = []
    used_txn_ids = set()

    for i in range(len(available_txns)):
        txn_a = available_txns[i]
        if txn_a["id"] in used_txn_ids:
            continue

        a_desc = txn_a["description"]
        a_amount = txn_a["amount"]
        a_date = datetime.strptime(txn_a["date"], "%Y-%m-%d")
        a_has_kw = _has_keyword(a_desc, INTERNAL_KEYWORDS_ALL)
        if not a_has_kw:
            continue

        for j in range(i + 1, len(available_txns)):
            txn_b = available_txns[j]
            if txn_b["id"] in used_txn_ids:
                continue

            b_desc = txn_b["description"]
            b_has_kw = _has_keyword(b_desc, INTERNAL_KEYWORDS_ALL)
            if not b_has_kw:
                continue

            b_date = datetime.strptime(txn_b["date"], "%Y-%m-%d")
            date_diff = abs((a_date - b_date).days)
            if date_diff > date_window_days:
                continue

            if txn_a["type"] != txn_b["type"]:
                b_amount = txn_b["amount"]
                amount_diff = abs(a_amount - b_amount)
            else:
                amount_diff = abs(a_amount - txn_b["amount"])

            if amount_diff > amount_tolerance:
                continue

            transfer_type = _infer_type_from_pair(txn_a, txn_b)
            role_a, role_b = _infer_roles(txn_a, txn_b, transfer_type)

            confidence = 0.0
            match_reasons = []

            if amount_diff <= 0.01:
                confidence += 0.4
                match_reasons.append("金额完全匹配")
            elif amount_diff <= 1:
                confidence += 0.2
                match_reasons.append(f"金额接近 (差{amount_diff:.2f})")

            if date_diff == 0:
                confidence += 0.3
                match_reasons.append("同一天")
            elif date_diff == 1:
                confidence += 0.2
                match_reasons.append(f"相差{date_diff}天")
            else:
                confidence += 0.1
                match_reasons.append(f"相差{date_diff}天")

            if a_has_kw and b_has_kw:
                confidence += 0.3
                match_reasons.append("双方都有关键词")
            else:
                confidence += 0.15
                match_reasons.append("一方有关键词")

            same_direction = txn_a["type"] == txn_b["type"]
            if same_direction:
                direction_label = "双收入" if txn_a["type"] == "income" else "双支出"
                match_reasons.append(f"同方向配对({direction_label})")

            cross_month = txn_a["date"][:7] != txn_b["date"][:7]
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
                "same_direction": same_direction,
                "items": [
                    {"transaction": txn_a, "role": role_a},
                    {"transaction": txn_b, "role": role_b},
                ],
                "date_range": [
                    min(txn_a["date"], txn_b["date"]),
                    max(txn_a["date"], txn_b["date"]),
                ],
            })

            used_txn_ids.add(txn_a["id"])
            used_txn_ids.add(txn_b["id"])
            break

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

    is_transfer_source = _has_keyword(desc, TRANSFER_SOURCE_KEYWORDS)
    is_transfer_dest = _has_keyword(desc, TRANSFER_DEST_KEYWORDS)
    is_reimb_src = _has_keyword(desc, REIMBURSEMENT_KEYWORDS_SOURCE)
    is_reimb_dest = _has_keyword(desc, REIMBURSEMENT_KEYWORDS_DEST)

    if is_income:
        if is_reimb_src or is_reimb_dest:
            return "reimbursement_refund"
        if is_transfer_source:
            return "transfer_source"
        if is_transfer_dest:
            return "transfer_destination"
    else:
        if is_reimb_src or is_reimb_dest:
            return "reimbursement_expense"
        if is_transfer_source:
            return "transfer_source"
        if is_transfer_dest:
            return "transfer_destination"

    return None


def suggest_description_similarity(desc1: str, desc2: str) -> float:
    words1 = set(desc1)
    words2 = set(desc2)
    if not words1 or not words2:
        return 0.0
    intersection = words1 & words2
    union = words1 | words2
    return len(intersection) / len(union)
