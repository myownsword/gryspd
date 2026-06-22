import sys
import os
import sqlite3
import pandas as pd
from io import StringIO

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import (
    init_db, DB_PATH, get_transactions, get_rules,
    add_transaction, bulk_insert_transactions,
    get_monthly_summary, get_budgets, set_budget,
    update_transaction_category, add_rule, reorder_rules
)
from csv_importer import (
    validate_and_classify, insert_valid_rows, read_csv_safely,
    detect_columns, normalize_date, is_valid_date, parse_amount
)
from rule_engine import apply_rules_to_transactions, get_rule_stats


def reset_db():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    init_db()
    print("✅ 数据库已重置并初始化")


def test_csv_validation():
    print("\n" + "="*60)
    print("📋 测试1: CSV 数据验证与错误检测")
    print("="*60)

    csv_content = """日期,摘要,金额
2025-12-05,工资发放,15000
2025-12-05,工资发放,15000
2025-12-06,美团外卖订单,-35.50
2025-12-06,未知交易,
2025-99-99,日期错误的交易,-100
,无日期记录,-50
2025-12-08,餐厅聚餐,-188
2025-12-12,非常奇怪的交易描述xxyyzz,-77.77
2030-01-01,未来的日期测试,-99
2025-12-15,房租支付,-3500
1999-01-01,太早的日期,-50
"""
    df = pd.read_csv(StringIO(csv_content))
    result = validate_and_classify(df)
    stats = result["stats"]

    print(f"  总行数: {stats['total']}")
    print(f"  有效行: {stats['valid']}")
    print(f"  错误行: {stats['errors']}")
    print(f"  重复行: {stats['duplicates']}")
    print(f"  未分类: {stats['uncategorized']}")

    assert stats["total"] == 11, f"总行数应为11, 实际{stats['total']}"
    assert stats["errors"] >= 4, f"错误行应>=4, 实际{stats['errors']}"
    assert stats["duplicates"] >= 1, f"重复行应>=1, 实际{stats['duplicates']}"
    assert stats["uncategorized"] >= 1, f"未分类应>=1, 实际{stats['uncategorized']}"

    print(f"  错误行详情:")
    for e in result["error_rows"][:3]:
        print(f"    行{e['row']}: {e['errors']}")

    print(f"  重复行详情:")
    for d in result["duplicate_candidates"][:2]:
        print(f"    {d['date']} | {d['description']} | ¥{d['amount']}")

    print("✅ CSV验证测试通过")
    return result


def test_basic_import():
    print("\n" + "="*60)
    print("📋 测试2: 基础流水导入（正常数据）")
    print("="*60)

    with open("sample_bank_statement.csv", "r", encoding="utf-8") as f:
        df = read_csv_safely(f)

    print(f"  读取CSV行数: {len(df)}")
    result = validate_and_classify(df)
    stats = result["stats"]

    print(f"  有效行: {stats['valid']}")
    print(f"  错误行: {stats['errors']}")
    print(f"  未分类: {stats['uncategorized']}")

    inserted, duplicates = insert_valid_rows(result["valid_rows"])
    print(f"  实际插入: {inserted}")
    print(f"  跳过重复: {duplicates}")

    total_txns = len(get_transactions())
    print(f"  数据库总流水: {total_txns}")

    assert inserted > 90, f"应插入>90条, 实际{inserted}"
    assert total_txns == inserted, f"数据库记录应等于插入数"

    print("✅ 基础导入测试通过")


def test_duplicate_import():
    print("\n" + "="*60)
    print("📋 测试3: 重复导入（确保不重复入账）")
    print("="*60)

    with open("sample_bank_statement.csv", "r", encoding="utf-8") as f:
        df = read_csv_safely(f)

    result = validate_and_classify(df)
    stats = result["stats"]

    print(f"  验证结果: 有效{stats['valid']}, 重复{stats['duplicates']}")
    inserted, duplicates = insert_valid_rows(result["valid_rows"])

    print(f"  新插入: {inserted}")
    print(f"  跳过重复: {duplicates + stats['duplicates']}")

    total_txns = len(get_transactions())
    prev_count = 95
    print(f"  数据库总流水: {total_txns}")

    assert inserted == 0, f"重复导入应插入0条, 实际{inserted}"
    assert duplicates + stats["duplicates"] > 90, f"应检测到90+重复"

    print("✅ 重复导入测试通过")


def test_budget_and_summary():
    print("\n" + "="*60)
    print("📋 测试4: 月度预算与统计汇总")
    print("="*60)

    test_month = "2026-05"

    set_budget("餐饮", 800, test_month)
    set_budget("交通", 400, test_month)
    set_budget("购物", 2000, test_month)
    set_budget("居住", 4000, test_month)
    set_budget("娱乐", 600, test_month)
    set_budget("医疗", 1000, test_month)

    budgets = get_budgets(test_month)
    print(f"  已设置{len(budgets)}个分类预算")

    summary = get_monthly_summary(test_month)
    print(f"  {test_month} 收入: ¥{summary['income_total']:,.2f}")
    print(f"  {test_month} 支出: ¥{summary['expense_total']:,.2f}")
    print(f"  {test_month} 结余: ¥{summary['balance']:,.2f}")

    print(f"  分类明细:")
    for cat, info in sorted(summary["by_category"].items(), key=lambda x: -x[1]["total"]):
        budget_amt = next((b["amount"] for b in budgets if b["category"] == cat), 0)
        status = ""
        if budget_amt > 0:
            pct = info["total"] / budget_amt * 100
            if info["total"] >= budget_amt:
                status = "🔴超支"
            elif info["total"] >= budget_amt * 0.8:
                status = "🟡接近"
            else:
                status = "🟢正常"
        print(f"    {cat}: ¥{info['total']:,.2f} (预算:¥{budget_amt:,.2f}) {status}")

    assert summary["income_total"] > 0, f"应有收入数据"
    assert summary["expense_total"] > 0, f"应有支出数据"

    print("✅ 预算与统计测试通过")


def test_rule_priority():
    print("\n" + "="*60)
    print("📋 测试5: 规则优先级调整与重新分类")
    print("="*60)

    expense_rules = get_rules("expense")
    print(f"  当前支出规则数: {len(expense_rules)}")

    print(f"  原规则优先级Top5:")
    for r in expense_rules[:5]:
        print(f"    [{r['priority']}] '{r['keyword']}' -> {r['category']}")

    rule_ids = [r["id"] for r in expense_rules]
    reversed_ids = list(reversed(rule_ids))
    reorder_rules("expense", reversed_ids)

    new_rules = get_rules("expense")
    print(f"  调整后规则优先级Top5:")
    for r in new_rules[:5]:
        print(f"    [{r['priority']}] '{r['keyword']}' -> {r['category']}")

    assert new_rules[0]["priority"] >= new_rules[1]["priority"], "优先级应递减"
    assert new_rules[0]["id"] == rule_ids[-1], "第一条应是原最后一条"

    print("  创建新规则并测试自动分类...")
    new_rule_id = add_rule("xxyyzz", "娱乐", "expense", 200)

    add_transaction(
        "2026-06-15",
        "非常奇怪的交易描述xxyyzz消费",
        99.99,
        "expense",
        "其他支出",
        None
    )

    updated, unchanged = apply_rules_to_transactions()
    print(f"  应用规则后更新: {updated}条, 未变化: {unchanged}条")

    txns = get_transactions()
    special_txn = None
    for t in txns:
        if "xxyyzz" in t["description"]:
            special_txn = t
            break

    if special_txn:
        print(f"  特殊流水分类结果: {special_txn['category']} (规则ID: {special_txn.get('rule_id')})")
        assert special_txn["category"] == "娱乐", f"应被新规则分类为娱乐, 实际{special_txn['category']}"
        assert special_txn["rule_id"] == new_rule_id, f"应关联新规则ID"

    print("✅ 规则优先级测试通过")


def test_data_persistence():
    print("\n" + "="*60)
    print("📋 测试6: 数据持久化（关闭后数据保留）")
    print("="*60)

    txn_count_before = len(get_transactions())
    rule_count_before = len(get_rules())
    budget_count_before = len(get_budgets("2026-05"))

    print(f"  当前流水数: {txn_count_before}")
    print(f"  当前规则数: {rule_count_before}")
    print(f"  当前预算设置数: {budget_count_before}")

    conn = sqlite3.connect(DB_PATH)
    conn.close()
    print("  模拟关闭应用（关闭数据库连接）")

    print("  模拟重新打开应用（重新连接数据库）")
    conn2 = sqlite3.connect(DB_PATH)
    conn2.row_factory = sqlite3.Row

    cursor = conn2.cursor()
    cursor.execute("SELECT COUNT(*) as cnt FROM transactions")
    txn_count_after = cursor.fetchone()["cnt"]
    cursor.execute("SELECT COUNT(*) as cnt FROM rules")
    rule_count_after = cursor.fetchone()["cnt"]
    cursor.execute("SELECT COUNT(*) as cnt FROM budgets WHERE month = '2026-05'")
    budget_count_after = cursor.fetchone()["cnt"]
    conn2.close()

    print(f"  重新读取流水数: {txn_count_after}")
    print(f"  重新读取规则数: {rule_count_after}")
    print(f"  重新读取预算设置数: {budget_count_after}")

    assert txn_count_before == txn_count_after, "流水数据应持久化"
    assert rule_count_before == rule_count_after, "规则数据应持久化"
    assert budget_count_before == budget_count_after, "预算数据应持久化"

    print("✅ 数据持久化测试通过")


def test_trend_data():
    print("\n" + "="*60)
    print("📋 测试7: 趋势数据（月份范围切换）")
    print("="*60)

    from database import get_trend_data

    test_ranges = [
        ("2025-12", "2026-01"),
        ("2025-12", "2026-03"),
        ("2026-01", "2026-06"),
    ]

    for start, end in test_ranges:
        trend = get_trend_data(start, end)
        months = set()
        for item in trend:
            months.add(item["month"])

        income_total = sum(t["total"] for t in trend if t["type"] == "income")
        expense_total = sum(t["total"] for t in trend if t["type"] == "expense")

        print(f"  {start} ~ {end}:")
        print(f"    覆盖月份: {len(months)}个 ({sorted(months)})")
        print(f"    记录数: {len(trend)}")
        print(f"    累计收入: ¥{income_total:,.2f}")
        print(f"    累计支出: ¥{expense_total:,.2f}")

        assert len(months) > 0, f"应有月份数据"

    print("✅ 趋势数据测试通过")


def test_date_and_amount_parsing():
    print("\n" + "="*60)
    print("📋 测试8: 日期金额解析（边界条件）")
    print("="*60)

    test_dates = [
        ("2025-12-31", True, "标准日期"),
        ("2025/12/31", True, "斜杠日期"),
        ("2025年12月31日", True, "中文日期"),
        ("2025-13-01", False, "无效月份"),
        ("2025-12-32", False, "无效日期"),
        ("2030-01-01", False, "未来日期"),
        ("1999-01-01", False, "过早日期"),
        ("", False, "空日期"),
    ]

    all_ok = True
    for date_str, expected, desc in test_dates:
        result = is_valid_date(date_str)
        status = "✅" if result == expected else "❌"
        print(f"  {status} {desc}: '{date_str}' -> {result} (预期: {expected})")
        if result != expected:
            all_ok = False

    test_amounts = [
        ("1234.56", 1234.56, "正数"),
        ("-1234.56", -1234.56, "负数"),
        ("¥1,234.56", 1234.56, "带货币符号千分位"),
        ("(1234.56)", -1234.56, "括号负数"),
        ("  500  ", 500.0, "前后空格"),
        ("", None, "空字符串"),
        ("abc", None, "非数字"),
    ]

    for amt_str, expected, desc in test_amounts:
        result = parse_amount(amt_str)
        status = "✅" if (result == expected or (result is None and expected is None)) else "❌"
        print(f"  {status} {desc}: '{amt_str}' -> {result} (预期: {expected})")
        if not (result == expected or (result is None and expected is None)):
            all_ok = False

    assert all_ok, "存在解析失败"
    print("✅ 日期金额解析测试通过")


def main():
    print("🚀 开始个人预算复盘工具功能测试")
    print(f"数据库路径: {DB_PATH}")

    try:
        reset_db()

        test_date_and_amount_parsing()
        test_csv_validation()
        test_basic_import()
        test_duplicate_import()
        test_budget_and_summary()
        test_rule_priority()
        test_data_persistence()
        test_trend_data()

        print("\n" + "🎉" * 30)
        print("✅✅✅ 所有测试通过！工具功能验证成功！ ✅✅✅")
        print("🎉" * 30)
        print("\n📊 最终数据库状态:")
        print(f"  - 流水记录: {len(get_transactions())}条")
        print(f"  - 分类规则: {len(get_rules())}条")
        stats = get_rule_stats()
        print(f"  - 已分类流水: {stats['total_transactions'] - stats['uncategorized_count']}条")
        print(f"  - 未分类流水: {stats['uncategorized_count']}条")

    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        print(f"\n💥 意外错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
