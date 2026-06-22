import pandas as pd
import re
from datetime import datetime
from typing import List, Dict, Tuple, Optional
from database import (
    get_rules, get_categories, add_category,
    get_transactions, bulk_insert_transactions
)


DATE_PATTERNS = [
    r"\d{4}-\d{2}-\d{2}",
    r"\d{4}/\d{2}/\d{2}",
    r"\d{4}年\d{1,2}月\d{1,2}日",
    r"\d{2}-\d{2}-\d{4}",
    r"\d{2}/\d{2}/\d{4}",
]

COLUMN_ALIASES = {
    "date": ["日期", "交易日期", "date", "Date", "DATE", "时间", "记账日期"],
    "description": ["摘要", "交易摘要", "交易描述", "描述", "description", "Description", "备注", "商户", "对方账户"],
    "amount": ["金额", "交易金额", "支出金额", "收入金额", "amount", "Amount", "发生额", "借方金额", "贷方金额"],
    "income": ["收入", "收入金额", "贷方", "贷方金额", "income", "Credit"],
    "expense": ["支出", "支出金额", "借方", "借方金额", "expense", "Debit"],
    "type": ["类型", "收支类型", "交易类型", "type", "Type", "借贷标志"],
}


def detect_columns(df: pd.DataFrame) -> Dict[str, str]:
    mapping = {}
    columns_lower = {col.lower(): col for col in df.columns}

    for standard_name, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias.lower() in columns_lower:
                mapping[standard_name] = columns_lower[alias.lower()]
                break

    return mapping


def normalize_date(date_str: str) -> Optional[str]:
    if pd.isna(date_str) or not str(date_str).strip():
        return None

    date_str = str(date_str).strip()

    for fmt in ["%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"]:
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue

    cn_match = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日", date_str)
    if cn_match:
        try:
            year, month, day = map(int, cn_match.groups())
            dt = datetime(year, month, day)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            return None

    for pattern in DATE_PATTERNS:
        if re.match(pattern, date_str):
            for fmt in ["%Y-%m-%d", "%Y/%m/%d"]:
                try:
                    dt = datetime.strptime(
                        re.sub(r"[年月日]", "-", date_str).rstrip("-"),
                        fmt.replace("/", "-") if "-" in date_str else fmt
                    )
                    return dt.strftime("%Y-%m-%d")
                except ValueError:
                    continue

    return None


def is_valid_date(date_str: str) -> bool:
    normalized = normalize_date(date_str)
    if not normalized:
        return False
    try:
        dt = datetime.strptime(normalized, "%Y-%m-%d")
        now = datetime.now()
        earliest = datetime(2000, 1, 1)
        return earliest <= dt <= now
    except ValueError:
        return False


def parse_amount(amount_val) -> Optional[float]:
    if pd.isna(amount_val):
        return None

    amount_str = str(amount_val).strip()
    if not amount_str:
        return None

    amount_str = re.sub(r"[¥￥$,\s]", "", amount_str)
    amount_str = amount_str.replace("(", "-").replace(")", "")

    try:
        amount = float(amount_str)
        return amount
    except ValueError:
        return None


def classify_by_rules(description: str, amount: float,
                      rules: List[Dict],
                      txn_type: Optional[str] = None,
                      categories: Optional[List[Dict]] = None) -> Tuple[Optional[str], Optional[int], str]:
    if txn_type is None:
        txn_type = "income" if amount >= 0 else "expense"

    matched_category = None
    matched_rule_id = None
    highest_priority = -1

    cat_type_map = {}
    if categories:
        for cat in categories:
            cat_type_map[cat["name"]] = cat["type"]

    for rule in rules:
        if rule["type"] != txn_type:
            continue
        if rule["keyword"] in description and rule["priority"] > highest_priority:
            if cat_type_map and rule["category"] in cat_type_map:
                if cat_type_map[rule["category"]] != txn_type:
                    continue
            highest_priority = rule["priority"]
            matched_category = rule["category"]
            matched_rule_id = rule["id"]

    if matched_category is None:
        default_cat = "其他收入" if txn_type == "income" else "其他支出"
        return default_cat, None, "uncategorized"

    return matched_category, matched_rule_id, "categorized"


def validate_and_classify(df: pd.DataFrame) -> Dict:
    result = {
        "valid_rows": [],
        "error_rows": [],
        "duplicate_candidates": [],
        "uncategorized_rows": [],
        "stats": {
            "total": len(df),
            "valid": 0,
            "errors": 0,
            "duplicates": 0,
            "uncategorized": 0
        }
    }

    column_map = detect_columns(df)

    if "date" not in column_map:
        result["error_rows"] = [
            {"row": i + 1, "error": "无法识别日期列", "data": str(row)}
            for i, row in df.iterrows()
        ]
        result["stats"]["errors"] = len(df)
        return result

    has_separate_income_expense = "income" in column_map or "expense" in column_map

    existing_txns = get_transactions()
    existing_keys = set()
    for txn in existing_txns:
        key = (txn["date"], txn["description"], abs(txn["amount"]))
        existing_keys.add(key)

    file_seen_keys = set()

    rules = get_rules()
    categories = get_categories()
    category_names = {cat["name"] for cat in categories}

    for idx, row in df.iterrows():
        row_num = idx + 2
        errors = []

        date_raw = row[column_map["date"]] if column_map.get("date") else None
        normalized_date = normalize_date(date_raw)

        if not normalized_date:
            errors.append(f"日期格式无效: {date_raw}")
        elif not is_valid_date(normalized_date):
            errors.append(f"日期超出合理范围: {normalized_date}")

        description = ""
        if "description" in column_map:
            description = str(row[column_map["description"]]).strip()
        if not description or pd.isna(description) or description == "nan":
            description = "(无描述)"

        amount = None
        txn_type = None

        if has_separate_income_expense:
            income_amt = parse_amount(row.get(column_map.get("income", ""), 0)) if column_map.get("income") else None
            expense_amt = parse_amount(row.get(column_map.get("expense", ""), 0)) if column_map.get("expense") else None

            if income_amt and income_amt > 0:
                amount = income_amt
                txn_type = "income"
            elif expense_amt and expense_amt > 0:
                amount = -expense_amt
                txn_type = "expense"
            elif "amount" in column_map:
                amount = parse_amount(row[column_map["amount"]])
        elif "amount" in column_map:
            amount = parse_amount(row[column_map["amount"]])
        elif "type" in column_map:
            type_val = str(row[column_map["type"]]).strip()
            amt = parse_amount(row.get(column_map.get("amount", ""), 0))
            if amt is not None:
                if "借" in type_val or "支出" in type_val or type_val.lower() == "debit":
                    amount = -abs(amt)
                elif "贷" in type_val or "收入" in type_val or type_val.lower() == "credit":
                    amount = abs(amt)
                else:
                    amount = amt

        if amount is None:
            errors.append("金额缺失或格式无效")
        elif amount == 0:
            errors.append("金额不能为0")

        if errors:
            result["error_rows"].append({
                "row": row_num,
                "errors": "; ".join(errors),
                "data": {col: str(row[col]) for col in df.columns},
                "date": normalized_date or "",
                "description": description,
                "amount": amount or 0
            })
            result["stats"]["errors"] += 1
            continue

        if amount > 0:
            txn_type = "income"
        elif amount < 0:
            txn_type = "expense"
            amount = abs(amount)

        dup_key = (normalized_date, description, amount)
        if dup_key in existing_keys:
            result["duplicate_candidates"].append({
                "row": row_num,
                "date": normalized_date,
                "description": description,
                "amount": amount if txn_type == "income" else -amount,
                "reason": "数据库中已存在相同流水"
            })
            result["stats"]["duplicates"] += 1
            continue
        if dup_key in file_seen_keys:
            result["duplicate_candidates"].append({
                "row": row_num,
                "date": normalized_date,
                "description": description,
                "amount": amount if txn_type == "income" else -amount,
                "reason": "CSV文件内重复"
            })
            result["stats"]["duplicates"] += 1
            continue
        file_seen_keys.add(dup_key)

        category, rule_id, status = classify_by_rules(
            description,
            amount if txn_type == "income" else -amount,
            rules,
            txn_type,
            categories
        )

        row_data = {
            "row_num": row_num,
            "date": normalized_date,
            "description": description,
            "amount": amount,
            "type": txn_type,
            "category": category,
            "rule_id": rule_id,
            "raw_amount": amount if txn_type == "income" else -amount
        }

        if status == "uncategorized":
            result["uncategorized_rows"].append(row_data)
            result["stats"]["uncategorized"] += 1

        result["valid_rows"].append(row_data)
        result["stats"]["valid"] += 1

    return result


def insert_valid_rows(valid_rows: List[Dict]) -> Tuple[int, int]:
    insert_data = [
        (
            row["date"],
            row["description"],
            row["amount"] if row["type"] == "income" else row["amount"],
            row["type"],
            row["category"],
            row.get("rule_id")
        )
        for row in valid_rows
    ]
    return bulk_insert_transactions(insert_data)


def read_csv_safely(file) -> pd.DataFrame:
    encodings = ["utf-8", "utf-8-sig", "gbk", "gb2312", "latin-1"]
    for encoding in encodings:
        try:
            file.seek(0)
            return pd.read_csv(file, encoding=encoding)
        except (UnicodeDecodeError, pd.errors.EmptyDataError):
            continue
        except Exception:
            continue
    raise ValueError("无法解析CSV文件，请检查文件编码或格式")
