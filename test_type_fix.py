import sys
import os
import pandas as pd
from io import StringIO

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import (
    init_db, DB_PATH, get_transactions, get_rules, get_categories,
    add_transaction, bulk_insert_transactions,
    get_monthly_summary, add_rule, delete_rule
)
from csv_importer import (
    validate_and_classify, insert_valid_rows, classify_by_rules
)
from rule_engine import apply_rules_to_transactions


def reset_db():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    init_db()


def test_classify_by_rules_type_filter():
    print("\n" + "="*70)
    print("🧪 测试 1: classify_by_rules 核心函数 - 按收入/支出过滤规则")
    print("="*70)

    all_categories = get_categories()
    all_rules = get_rules()

    print(f"\n  测试数据:")
    print(f"    收入规则数量: {len([r for r in all_rules if r['type'] == 'income'])}")
    print(f"    支出规则数量: {len([r for r in all_rules if r['type'] == 'expense'])}")

    test_cases = [
        {
            "description": "12月工资发放",
            "amount": 15000,
            "txn_type": "income",
            "expected_category": "工资",
            "scenario": "正常收入流水，关键词'工资'"
        },
        {
            "description": "美团外卖订单",
            "amount": -35,
            "txn_type": "expense",
            "expected_category": "餐饮",
            "scenario": "正常支出流水，关键词'美团'"
        },
        {
            "description": "餐饮有限公司收入款",
            "amount": 5000,
            "txn_type": "income",
            "expected_not_category": "餐饮",
            "must_be_income_type": True,
            "scenario": "⚠️ 关键测试: 收入流水含支出关键词'餐饮'，绝不能分到支出分类"
        },
        {
            "description": "工资扣除社保",
            "amount": -500,
            "txn_type": "expense",
            "expected_not_category": "工资",
            "must_be_expense_type": True,
            "scenario": "⚠️ 关键测试: 支出流水含收入关键词'工资'，绝不能分到收入分类"
        },
        {
            "description": "奖金到账",
            "amount": 8000,
            "txn_type": "income",
            "expected_category": "奖金",
            "scenario": "收入流水，关键词'奖金'"
        },
        {
            "description": "地铁刷卡消费",
            "amount": -6,
            "txn_type": "expense",
            "expected_category": "交通",
            "scenario": "支出流水，关键词'地铁'"
        },
        {
            "description": "房租押金退还",
            "amount": 3500,
            "txn_type": "income",
            "expected_not_category": "居住",
            "must_be_income_type": True,
            "scenario": "⚠️ 关键测试: 收入流水含支出关键词'房租'，绝不能分到'居住'"
        },
        {
            "description": "淘宝退款收入",
            "amount": 200,
            "txn_type": "income",
            "expected_not_category": "购物",
            "must_be_income_type": True,
            "scenario": "⚠️ 关键测试: 收入流水含支出关键词'淘宝'，绝不能分到'购物'"
        },
    ]

    all_passed = True
    for tc in test_cases:
        category, rule_id, status = classify_by_rules(
            tc["description"], tc["amount"], all_rules, tc["txn_type"], all_categories
        )

        cat_type = None
        for cat in all_categories:
            if cat["name"] == category:
                cat_type = cat["type"]
                break

        passed = True
        error_msg = ""

        if "expected_category" in tc and category != tc["expected_category"]:
            passed = False
            error_msg = f"期望分类={tc['expected_category']}, 实际={category}"

        if "expected_not_category" in tc and category == tc["expected_not_category"]:
            passed = False
            error_msg = f"分类不应是{tc['expected_not_category']}, 但实际就是"

        if tc.get("must_be_income_type") and cat_type != "income":
            passed = False
            error_msg = f"分类类型应为income, 实际={cat_type}, 分类={category}"

        if tc.get("must_be_expense_type") and cat_type != "expense":
            passed = False
            error_msg = f"分类类型应为expense, 实际={cat_type}, 分类={category}"

        status_icon = "✅" if passed else "❌"
        if not passed:
            all_passed = False

        print(f"\n  {status_icon} {tc['scenario']}")
        print(f"     描述: '{tc['description']}' | 金额: {tc['amount']} | 类型: {tc['txn_type']}")
        print(f"     分类结果: {category} (类型: {cat_type}) | 规则ID: {rule_id}")
        if not passed:
            print(f"     ❌ 错误: {error_msg}")

    return all_passed


def test_csv_import_type_consistency():
    print("\n" + "="*70)
    print("🧪 测试 2: CSV 导入后所有流水分类类型与流水类型一致")
    print("="*70)

    csv_content = """日期,摘要,金额
2026-06-01,工资发放,18000
2026-06-01,美团外卖订单,-58
2026-06-02,季度奖金,5000
2026-06-02,地铁充值,-200
2026-06-03,餐饮消费,-128
2026-06-03,投资收益分红,800
2026-06-04,淘宝购物退款,300
2026-06-04,房租支出扣除,-3500
2026-06-05,京东商城购物,-688
2026-06-05,项目奖金收入,2000
"""
    df = pd.read_csv(StringIO(csv_content))
    result = validate_and_classify(df)

    print(f"\n  导入统计: 有效={result['stats']['valid']}, 错误={result['stats']['errors']}, "
          f"未分类={result['stats']['uncategorized']}")

    insert_valid_rows(result["valid_rows"])
    txns = get_transactions()

    all_categories = get_categories()
    cat_type_map = {c["name"]: c["type"] for c in all_categories}

    mismatches = []
    print(f"\n  验证每条流水分类类型一致性:")
    for t in txns:
        cat_type = cat_type_map.get(t["category"])
        if cat_type != t["type"]:
            mismatches.append(t)
            print(f"    ❌ 流水ID={t['id']}: 流水类型={t['type']}, "
                  f"分类={t['category']}, 分类类型={cat_type} "
                  f"- 描述: {t['description']} 金额: {t['amount']}")
        else:
            print(f"    ✅ 流水类型={t['type']:7s}, 分类={t['category']:6s} "
                  f"(类型一致) | {t['description']}")

    if mismatches:
        print(f"\n  ❌ 发现 {len(mismatches)} 条流水分类类型不匹配!")
        return False
    else:
        print(f"\n  ✅ 全部 {len(txns)} 条流水分类类型均一致!")
        return True


def test_rule_reapply_fixes_mistakes():
    print("\n" + "="*70)
    print("🧪 测试 3: 重新应用规则可修复历史错误分类")
    print("="*70)

    reset_db()

    print("\n  步骤1: 先手动插入一条错误分类 (收入流水分到支出分类)")
    add_transaction(
        "2026-05-15",
        "工资发放",
        15000,
        "income",
        "餐饮",
        None
    )
    add_transaction(
        "2026-05-16",
        "美团外卖",
        50,
        "expense",
        "工资",
        None
    )

    txns_before = get_transactions()
    print(f"  插入后流水:")
    for t in txns_before:
        print(f"    {t['description']} | 类型={t['type']} | 错误分类={t['category']}")

    print("\n  步骤2: 调用 apply_rules_to_transactions() 重新分类")
    updated, unchanged = apply_rules_to_transactions()
    print(f"  更新: {updated} 条, 未变化: {unchanged} 条")

    txns_after = get_transactions()
    all_categories = get_categories()
    cat_type_map = {c["name"]: c["type"] for c in all_categories}

    all_fixed = True
    print(f"\n  修复后验证:")
    for t in txns_after:
        cat_type = cat_type_map.get(t["category"])
        ok = (cat_type == t["type"])
        icon = "✅" if ok else "❌"
        if not ok:
            all_fixed = False
        print(f"    {icon} {t['description']} | 类型={t['type']} | "
              f"分类={t['category']} (分类类型={cat_type})")

    return all_fixed


def test_new_rule_applies_correctly_by_type():
    print("\n" + "="*70)
    print("🧪 测试 4: 手动保存规则后再次导入同类描述按类型正确分类")
    print("="*70)

    reset_db()

    print("\n  步骤1: 导入包含'某某科技'的收入流水 (首次导入会未分类)")
    csv1 = """日期,摘要,金额
2026-06-01,某某科技公司服务费,6000
2026-06-02,某某科技公司服务费,4500
"""
    df1 = pd.read_csv(StringIO(csv1))
    result1 = validate_and_classify(df1)
    print(f"  首次导入: 有效={result1['stats']['valid']}, 未分类={result1['stats']['uncategorized']}")
    insert_valid_rows(result1["valid_rows"])

    txns1 = get_transactions()
    print(f"  首次导入后分类结果:")
    for t in txns1:
        print(f"    {t['description']} ¥{t['amount']} -> {t['category']}")

    print("\n  步骤2: 添加规则 - '某某科技' -> '投资收益'(收入类型)")
    rules_before = get_rules("income")
    priority = (rules_before[0]["priority"] + 1) if rules_before else 10
    new_rule_id = add_rule("某某科技", "投资收益", "income", priority)
    print(f"  创建规则ID={new_rule_id}, 关键词='某某科技', 分类='投资收益'(income)")

    print("\n  步骤3: 添加一个陷阱支出规则 (不应影响收入流水)")
    rules_exp = get_rules("expense")
    priority_exp = (rules_exp[0]["priority"] + 1) if rules_exp else 10
    trap_rule_id = add_rule("某某科技", "娱乐", "expense", priority_exp)
    print(f"  创建陷阱规则ID={trap_rule_id}, 关键词='某某科技', 分类='娱乐'(expense)")

    print("\n  步骤4: 重新应用规则并验证类型正确")
    updated, _ = apply_rules_to_transactions()
    print(f"  重新分类更新了 {updated} 条")

    txns2 = get_transactions()
    all_categories = get_categories()
    cat_type_map = {c["name"]: c["type"] for c in all_categories}

    all_correct = True
    print(f"\n  最终分类结果验证:")
    for t in txns2:
        cat_type = cat_type_map.get(t["category"])
        ok = (t["category"] == "投资收益" and t["type"] == "income" and cat_type == "income")
        icon = "✅" if ok else "❌"
        if not ok:
            all_correct = False
        print(f"    {icon} {t['description']} ¥{t['amount']} "
              f"-> {t['category']} (分类类型={cat_type}, 流水类型={t['type']})")

    print("\n  步骤5: 再次导入同类收入流水，应自动按收入规则分类")
    csv2 = """日期,摘要,金额
2026-06-10,某某科技公司服务费,7500
"""
    df2 = pd.read_csv(StringIO(csv2))
    result2 = validate_and_classify(df2)

    correctly_classified = True
    print(f"\n  再次导入验证 (应自动分到正确的收入分类):")
    for row in result2["valid_rows"]:
        cat_type = cat_type_map.get(row["category"])
        ok = (row["category"] == "投资收益" and row["type"] == "income")
        icon = "✅" if ok else "❌"
        if not ok:
            correctly_classified = False
        print(f"    {icon} {row['description']} ¥{row['amount']} "
              f"-> {row['category']} (类型匹配)")

    delete_rule(trap_rule_id)
    return all_correct and correctly_classified


def test_budget_and_trend_not_corrupted():
    print("\n" + "="*70)
    print("🧪 测试 5: 月度预算统计和趋势图表数据不受错误分类影响")
    print("="*70)

    reset_db()

    print("\n  导入多月份混合流水...")
    csv_content = """日期,摘要,金额
2026-04-05,工资发放,15000
2026-04-06,美团外卖,-88
2026-04-15,奖金发放,3000
2026-04-20,淘宝购物,-500
2026-05-05,工资发放,15500
2026-05-06,滴滴打车,-45
2026-05-15,季度奖金,5000
2026-05-20,京东购物,-800
2026-06-05,工资发放,16000
2026-06-06,地铁充值,-150
2026-06-15,项目奖金,4000
2026-06-20,拼多多购物,-300
"""
    df = pd.read_csv(StringIO(csv_content))
    result = validate_and_classify(df)
    insert_valid_rows(result["valid_rows"])

    all_categories = get_categories()
    cat_type_map = {c["name"]: c["type"] for c in all_categories}

    print(f"\n  验证所有月份流水分类类型一致:")
    txns = get_transactions()
    type_ok = True
    for t in txns:
        if cat_type_map.get(t["category"]) != t["type"]:
            type_ok = False
            print(f"    ❌ 类型不一致: {t}")
    if type_ok:
        print(f"    ✅ 全部 {len(txns)} 条流水分类类型正确")

    print(f"\n  月度汇总验证:")
    for month in ["2026-04", "2026-05", "2026-06"]:
        summary = get_monthly_summary(month)
        income_by_cat = 0
        expense_by_cat = 0
        for cat, info in summary["by_category"].items():
            if info["type"] == "income":
                income_by_cat += info["total"]
            else:
                expense_by_cat += info["total"]

        income_ok = (abs(income_by_cat - summary["income_total"]) < 0.01)
        expense_ok = (abs(expense_by_cat - summary["expense_total"]) < 0.01)
        balance_ok = (abs((summary["income_total"] - summary["expense_total"]) - summary["balance"]) < 0.01)

        icon = "✅" if (income_ok and expense_ok and balance_ok) else "❌"
        print(f"    {icon} {month}: 收入={summary['income_total']:,.0f} "
              f"支出={summary['expense_total']:,.0f} 结余={summary['balance']:,.0f} "
              f"(分类汇总校验: 收入{'✅' if income_ok else '❌'} "
              f"支出{'✅' if expense_ok else '❌'})")

    return type_ok


def main():
    print("🔍 开始验证分类规则匹配类型过滤修复")
    print(f"数据库路径: {DB_PATH}")

    tests = [
        ("测试 1: 核心函数类型过滤", test_classify_by_rules_type_filter),
        ("测试 2: CSV导入类型一致性", test_csv_import_type_consistency),
        ("测试 3: 重新应用规则修复", test_rule_reapply_fixes_mistakes),
        ("测试 4: 新规则正确应用类型", test_new_rule_applies_correctly_by_type),
        ("测试 5: 预算统计不受影响", test_budget_and_trend_not_corrupted),
    ]

    passed_count = 0
    failed_tests = []

    for name, test_func in tests:
        try:
            reset_db()
            if test_func():
                passed_count += 1
                print(f"\n  {'='*10} ✅ {name} 通过 {'='*10}")
            else:
                failed_tests.append(name)
                print(f"\n  {'='*10} ❌ {name} 失败 {'='*10}")
        except Exception as e:
            failed_tests.append(name)
            print(f"\n  {'='*10} 💥 {name} 异常: {e} {'='*10}")
            import traceback
            traceback.print_exc()

    print("\n" + "🎉" * 30)
    if passed_count == len(tests):
        print(f"✅✅✅ 全部 {passed_count}/{len(tests)} 个测试通过！类型过滤修复成功！ ✅✅✅")
    else:
        print(f"❌ 测试结果: {passed_count}/{len(tests)} 通过")
        print(f"   失败: {', '.join(failed_tests)}")
        sys.exit(1)
    print("🎉" * 30)


if __name__ == "__main__":
    main()
