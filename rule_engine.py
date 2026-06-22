from typing import List, Dict, Tuple, Optional
from database import (
    get_rules, get_transactions, update_transaction_category,
    add_rule, add_category
)
from csv_importer import classify_by_rules


def apply_rules_to_transactions(txn_ids: Optional[List[int]] = None,
                                start_date: Optional[str] = None,
                                end_date: Optional[str] = None) -> Tuple[int, int]:
    rules = get_rules()
    txns = get_transactions(start_date, end_date)

    if txn_ids:
        txns = [t for t in txns if t["id"] in txn_ids]

    updated = 0
    unchanged = 0

    for txn in txns:
        amount_for_classify = txn["amount"] if txn["type"] == "income" else -txn["amount"]
        new_category, new_rule_id, _ = classify_by_rules(
            txn["description"], amount_for_classify, rules
        )

        if new_category != txn["category"] or new_rule_id != txn.get("rule_id"):
            update_transaction_category(txn["id"], new_category, new_rule_id)
            updated += 1
        else:
            unchanged += 1

    return updated, unchanged


def create_rule_from_transaction(description: str, category: str,
                                 txn_type: str, priority: Optional[int] = None) -> int:
    keyword = extract_key_keyword(description)
    if not keyword:
        keyword = description[:4] if len(description) >= 4 else description

    if priority is None:
        rules = get_rules(txn_type)
        priority = (rules[0]["priority"] + 1) if rules else 10

    return add_rule(keyword, category, txn_type, priority)


def extract_key_keyword(description: str) -> str:
    common_noise = [
        "支付", "付款", "收款", "转账", "交易", "消费",
        "有限公司", "股份有限公司", "科技", "网络",
        "有限公司", "商店", "超市", "商城",
    ]
    result = description
    for noise in common_noise:
        result = result.replace(noise, "")

    result = result.strip()
    if len(result) >= 2:
        return result[:6] if len(result) > 6 else result

    return description[:4] if len(description) >= 4 else description


def find_matching_rules(description: str, rules: List[Dict]) -> List[Dict]:
    matches = []
    for rule in rules:
        if rule["keyword"] in description:
            matches.append(rule)
    matches.sort(key=lambda r: r["priority"], reverse=True)
    return matches


def get_rule_stats() -> Dict:
    rules = get_rules()
    txns = get_transactions()

    rule_usage = {}
    uncategorized = 0
    manually_classified = 0

    for rule in rules:
        rule_usage[rule["id"]] = {
            "rule": rule,
            "count": 0,
            "total_amount": 0.0
        }

    for txn in txns:
        rule_id = txn.get("rule_id")
        if rule_id and rule_id in rule_usage:
            rule_usage[rule_id]["count"] += 1
            rule_usage[rule_id]["total_amount"] += txn["amount"]
        else:
            if txn["category"] in ["其他收入", "其他支出"]:
                uncategorized += 1
            else:
                manually_classified += 1

    return {
        "rule_usage": rule_usage,
        "uncategorized_count": uncategorized,
        "manually_classified_count": manually_classified,
        "total_rules": len(rules),
        "total_transactions": len(txns)
    }


def suggest_rules_from_uncategorized(min_count: int = 2) -> List[Dict]:
    txns = get_transactions()
    uncategorized_descriptions = {}

    for txn in txns:
        if txn["category"] in ["其他收入", "其他支出"] and not txn.get("rule_id"):
            desc = txn["description"]
            if desc not in uncategorized_descriptions:
                uncategorized_descriptions[desc] = {
                    "count": 0,
                    "amounts": [],
                    "type": txn["type"]
                }
            uncategorized_descriptions[desc]["count"] += 1
            uncategorized_descriptions[desc]["amounts"].append(txn["amount"])

    suggestions = []
    for desc, info in uncategorized_descriptions.items():
        if info["count"] >= min_count:
            keyword = extract_key_keyword(desc)
            suggestions.append({
                "description": desc,
                "keyword": keyword,
                "count": info["count"],
                "total_amount": sum(info["amounts"]),
                "avg_amount": sum(info["amounts"]) / len(info["amounts"]),
                "type": info["type"]
            })

    suggestions.sort(key=lambda x: x["count"], reverse=True)
    return suggestions
