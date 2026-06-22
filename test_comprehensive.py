# -*- coding: utf-8 -*-
import sys
import os
import pandas as pd
from io import StringIO

def print_amt(amount):
    return f"RMB {amount:.2f}"

print("=" * 70)
print("全面功能测试: 内部转账与报销归并 (含同方向配对)")
print("=" * 70)

if os.path.exists("budget.db"):
    os.remove("budget.db")
    print("[INFO] 已删除旧数据库")

import database
import transfer_engine
from csv_importer import validate_and_classify, insert_valid_rows

database.init_db()
print("[OK] 数据库初始化完成")

print()
print("=" * 70)
print("TEST 1: 导入综合样例数据 (含同方向配对场景)")
print("=" * 70)

with open("sample_comprehensive.csv", "r", encoding="utf-8") as f:
    content = f.read()

df = pd.read_csv(StringIO(content))
print(f"[OK] 读取 CSV 成功，共 {len(df)} 行")

result = validate_and_classify(df)
stats = result["stats"]
print(f"[OK] 验证分类完成")
print(f"     有效行: {stats['valid']}")
print(f"     错误行: {stats['errors']}")
print(f"     重复行: {stats['duplicates']}")
print(f"     未分类: {stats['uncategorized']}")

inserted, duplicates = insert_valid_rows(result["valid_rows"])
print(f"[OK] 导入完成: 新增 {inserted} 条, 跳过重复 {duplicates} 条")

txns = database.get_transactions()
print(f"[OK] 数据库中共有 {len(txns)} 条流水")

internal_kw_txns = [t for t in txns if any(kw in t["description"] for kw in transfer_engine.INTERNAL_KEYWORDS_ALL)]
income_internal = [t for t in internal_kw_txns if t["type"] == "income"]
expense_internal = [t for t in internal_kw_txns if t["type"] == "expense"]
print(f"[INFO] 含内部转账关键词流水: {len(internal_kw_txns)} 条")
print(f"       - 收入类: {len(income_internal)} 条")
print(f"       - 支出类: {len(expense_internal)} 条")

print()
print("=" * 70)
print("TEST 2: 候选生成测试 (含同方向配对)")
print("=" * 70)

candidates = transfer_engine.find_internal_transfer_candidates(date_window_days=5)
print(f"[OK] 找到 {len(candidates)} 个候选匹配")

same_dir_candidates = [c for c in candidates if c.get("same_direction", False)]
diff_dir_candidates = [c for c in candidates if not c.get("same_direction", False)]
transfer_candidates = [c for c in candidates if c["type"] == "transfer"]
reimburse_candidates = [c for c in candidates if c["type"] == "reimbursement"]

print(f"[INFO] 候选分类统计:")
print(f"       - 同方向配对: {len(same_dir_candidates)} 个")
print(f"         * 双收入: {len([c for c in same_dir_candidates if c['items'][0]['transaction']['type'] == 'income'])} 个")
print(f"         * 双支出: {len([c for c in same_dir_candidates if c['items'][0]['transaction']['type'] == 'expense'])} 个")
print(f"       - 异方向配对: {len(diff_dir_candidates)} 个")
print(f"       - 内部转账: {len(transfer_candidates)} 个")
print(f"       - 报销抵扣: {len(reimburse_candidates)} 个")

cross_month = [c for c in candidates if c["cross_month"]]
amount_mismatch = [c for c in candidates if not c["amount_match"]]
print(f"       - 跨月匹配: {len(cross_month)} 个")
print(f"       - 金额不一致: {len(amount_mismatch)} 个")

high_conf = [c for c in candidates if c["confidence"] >= 0.7]
med_conf = [c for c in candidates if 0.4 <= c["confidence"] < 0.7]
low_conf = [c for c in candidates if c["confidence"] < 0.4]
print(f"       - 高置信度 (>=0.7): {len(high_conf)}")
print(f"       - 中置信度 (0.4-0.7): {len(med_conf)}")
print(f"       - 低置信度 (<0.4): {len(low_conf)}")

if candidates:
    print()
    print("  [INFO] 前 10 个候选详情:")
    for i, c in enumerate(candidates[:10]):
        type_label = "报销抵扣" if c["type"] == "reimbursement" else "内部转账"
        same_dir = c.get("same_direction", False)
        dir_tag = "[同方向]" if same_dir else ""
        amount = c["items"][0]["transaction"]["amount"]
        items_types = [item["transaction"]["type"] for item in c["items"]]
        roles = [item["role"] for item in c["items"]]

        print(f"    {i+1:2d}. {type_label} {dir_tag} - {print_amt(amount)} - 置信度: {c['confidence']:.2f}")
        print(f"        原因: {', '.join(c['reasons'][:4])}")
        print(f"        流水类型: {items_types} | 角色: {roles}")
        if c["cross_month"]:
            print(f"        警告: 跨月匹配")
        if not c["amount_match"]:
            print(f"        警告: 金额不一致 (差{c['amount_diff']:.2f})")

unmatched = transfer_engine.get_unmatched_internal_keyword_txns()
print()
print(f"[OK] 未匹配的关键词流水: {len(unmatched)} 条")
if unmatched:
    print("  前 5 条:")
    for i, u in enumerate(unmatched[:5]):
        print(f"    {i+1}. {u['transaction']['date']} | {u['transaction']['type']} | {u['transaction']['description']} | {u['matched_keywords']}")

print()
print("=" * 70)
print("TEST 3: 手动确认归并 (各种类型)")
print("=" * 70)

confirmed_ids = []

if len(candidates) >= 3:
    print("[INFO] 确认 3 个候选 (覆盖不同类型)")

    for idx, c in enumerate(candidates[:3]):
        items = [(item["transaction"]["id"], item["role"]) for item in c["items"]]
        type_label = "报销抵扣" if c["type"] == "reimbursement" else "内部转账"
        same_dir = c.get("same_direction", False)
        desc = f"测试{type_label}{'_同方向' if same_dir else ''}_{idx+1}"

        transfer_id = database.create_internal_transfer(c["type"], desc, items)
        confirmed_ids.append(transfer_id)
        transfer = database.get_internal_transfer_by_id(transfer_id)
        print(f"  [OK] 确认归并 #{transfer_id}: {desc}")
        print(f"       类型: {transfer['transfer_type']} | 金额: {print_amt(transfer['total_amount'])}")
        print(f"       包含 {len(transfer['items'])} 条流水")
        for item in transfer["items"]:
            print(f"         - #{item['transaction_id']} | {item['date']} | {item['role']} | {item['txn_description']}")

transfer_ids = database.get_internal_transfer_txn_ids()
print()
print(f"[OK] 已归并的流水总数: {len(transfer_ids)} 条")

print()
print("=" * 70)
print("TEST 4: 月度统计对比 (排除内部转账前后)")
print("=" * 70)

test_months = ["2025-12", "2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"]
print(f"{'月份':<10} {'含内部收入':>15} {'含内部支出':>15} {'排除后收入':>15} {'排除后支出':>15} {'差额收入':>12} {'差额支出':>12}")
print("-" * 95)

for month in test_months:
    s_with = database.get_monthly_summary(month, exclude_internal=False)
    s_without = database.get_monthly_summary(month, exclude_internal=True)

    diff_inc = s_with["income_total"] - s_without["income_total"]
    diff_exp = s_with["expense_total"] - s_without["expense_total"]

    if diff_inc > 0 or diff_exp > 0:
        marker = " *"
    else:
        marker = ""

    print(f"{month:<10} {print_amt(s_with['income_total']):>15} {print_amt(s_with['expense_total']):>15} "
          f"{print_amt(s_without['income_total']):>15} {print_amt(s_without['expense_total']):>15} "
          f"{print_amt(diff_inc):>12} {print_amt(diff_exp):>12}{marker}")

print()
print("[INFO] 带 * 的月份表示包含内部转账流水")

print()
print("=" * 70)
print("TEST 5: 趋势数据对比 (排除内部转账前后)")
print("=" * 70)

start_m = "2025-12"
end_m = "2026-06"
t_with = database.get_trend_data(start_m, end_m, exclude_internal=False)
t_without = database.get_trend_data(start_m, end_m, exclude_internal=True)

print(f"时间范围: {start_m} ~ {end_m}")
print(f"  含内部转账 - 数据行数: {len(t_with)}")
print(f"  排除内部转账 - 数据行数: {len(t_without)}")

income_with = sum(r["total"] for r in t_with if r["type"] == "income")
expense_with = sum(r["total"] for r in t_with if r["type"] == "expense")
income_without = sum(r["total"] for r in t_without if r["type"] == "income")
expense_without = sum(r["total"] for r in t_without if r["type"] == "expense")

print()
print(f"  总计:")
print(f"    含内部转账 - 收入: {print_amt(income_with)} | 支出: {print_amt(expense_with)} | 结余: {print_amt(income_with - expense_with)}")
print(f"    排除内部转账 - 收入: {print_amt(income_without)} | 支出: {print_amt(expense_without)} | 结余: {print_amt(income_without - expense_without)}")
print(f"    内部转账影响 - 收入: {print_amt(income_with - income_without)} | 支出: {print_amt(expense_with - expense_without)}")

print()
print("=" * 70)
print("TEST 6: 流水追溯功能")
print("=" * 70)

if confirmed_ids:
    first_t = database.get_internal_transfer_by_id(confirmed_ids[0])
    if first_t and first_t["items"]:
        sample_txn_id = first_t["items"][0]["transaction_id"]
        transfer_info = database.get_transfer_for_transaction(sample_txn_id)

        if transfer_info:
            print(f"[OK] 流水 #{sample_txn_id} 追溯成功")
            print(f"     归并ID: #{transfer_info['id']}")
            print(f"     归并类型: {transfer_info['transfer_type']}")
            print(f"     归并描述: {transfer_info['description']}")
            print(f"     归并创建时间: {transfer_info['created_at']}")
        else:
            print(f"[FAIL] 流水 #{sample_txn_id} 追溯失败")

print()
print("=" * 70)
print("TEST 7: 拆开归并并验证统计恢复")
print("=" * 70)

if confirmed_ids:
    test_transfer_id = confirmed_ids[0]
    test_t = database.get_internal_transfer_by_id(test_transfer_id)

    if test_t:
        test_month = test_t["items"][0]["date"][:7]
        before = database.get_monthly_summary(test_month, exclude_internal=True)

        print(f"[INFO] 测试归并 #{test_transfer_id} (月份 {test_month})")
        before_ids_count = len(database.get_internal_transfer_txn_ids())
        print(f"  拆开前归并流水数: {before_ids_count}")

        database.delete_internal_transfer(test_transfer_id)
        after_ids_count = len(database.get_internal_transfer_txn_ids())
        print(f"  拆开后归并流水数: {after_ids_count}")

        after = database.get_monthly_summary(test_month, exclude_internal=True)
        print()
        print(f"  月度统计变化 ({test_month}):")
        print(f"    拆开前 - 收入: {print_amt(before['income_total'])} | 支出: {print_amt(before['expense_total'])}")
        print(f"    拆开后 - 收入: {print_amt(after['income_total'])} | 支出: {print_amt(after['expense_total'])}")

        diff_inc = after["income_total"] - before["income_total"]
        diff_exp = after["expense_total"] - before["expense_total"]
        if diff_inc != 0 or diff_exp != 0:
            print(f"    变化量 - 收入: +{print_amt(diff_inc)} | 支出: +{print_amt(diff_exp)}")
            print(f"  [OK] 拆开归并后统计已恢复")
        else:
            print(f"  [INFO] 该归并不影响指定月份统计")

        check_t = database.get_internal_transfer_by_id(test_transfer_id)
        if check_t is None:
            print(f"  [OK] 验证: 归并记录已从数据库删除")
        else:
            print(f"  [FAIL] 验证失败: 归并记录仍然存在")

print()
print("=" * 70)
print("TEST 8: 重复导入检测")
print("=" * 70)

result2 = validate_and_classify(df)
stats2 = result2["stats"]
print(f"[OK] 第二次导入检测")
print(f"     检测到重复行: {stats2['duplicates']}")
print(f"     有效行: {stats2['valid']}")

inserted2, duplicates2 = insert_valid_rows(result2["valid_rows"])
print(f"     实际导入: 新增 {inserted2} 条, 跳过重复 {duplicates2 + stats2['duplicates']} 条")
if inserted2 == 0 and (duplicates2 + stats2['duplicates']) > 0:
    print("  [OK] 重复导入检测工作正常，无重复数据")

print()
print("=" * 70)
print("TEST 9: 重启后数据持久化验证 (模拟重启)")
print("=" * 70)

import importlib
importlib.reload(database)

persisted_transfers = database.get_internal_transfers()
transfer_count_after = len(database.get_internal_transfer_txn_ids())
print(f"[OK] 重新加载数据库模块后验证:")
print(f"     归并记录数: {len(persisted_transfers)}")
print(f"     归并流水ID数: {transfer_count_after}")

if persisted_transfers:
    print("  [OK] 数据持久化成功，重启后归并关系仍然存在")
    for t in persisted_transfers[:3]:
        items_count = len(t["items"]) if t.get("items") else 0
        type_label = "报销抵扣" if t["transfer_type"] == "reimbursement" else "内部转账"
        print(f"    - #{t['id']} {type_label}: {t['description']} ({items_count}条流水)")

print()
print("=" * 70)
print("TEST 10: 手动创建同方向归并 (双收入/双支出)")
print("=" * 70)

matched_ids = database.get_internal_transfer_txn_ids()
all_txns = database.get_transactions()
available = [t for t in all_txns if t["id"] not in matched_ids]

income_available = [t for t in available if t["type"] == "income" and any(kw in t["description"] for kw in transfer_engine.INTERNAL_KEYWORDS_ALL)]
expense_available = [t for t in available if t["type"] == "expense" and any(kw in t["description"] for kw in transfer_engine.INTERNAL_KEYWORDS_ALL)]

print(f"[INFO] 可用内部关键词流水:")
print(f"       收入类: {len(income_available)} 条")
print(f"       支出类: {len(expense_available)} 条")

if len(income_available) >= 2:
    print()
    print("  [TEST] 手动创建双收入归并 (模拟微信零钱提现):")
    txn_a = income_available[0]
    txn_b = income_available[1]
    print(f"    流水A: #{txn_a['id']} | {txn_a['date']} | {txn_a['description']} | {print_amt(txn_a['amount'])}")
    print(f"    流水B: #{txn_b['id']} | {txn_b['date']} | {txn_b['description']} | {print_amt(txn_b['amount'])}")

    items = [(txn_a["id"], "source"), (txn_b["id"], "destination")]
    manual_id = database.create_internal_transfer("transfer", "手动测试:双收入_微信提现", items)
    manual_t = database.get_internal_transfer_by_id(manual_id)
    if manual_t:
        print(f"  [OK] 双收入归并创建成功，ID: #{manual_t['id']}")

if len(expense_available) >= 2:
    print()
    print("  [TEST] 手动创建双支出归并 (模拟信用卡重复扣款):")
    txn_a = expense_available[0]
    txn_b = expense_available[1]
    print(f"    流水A: #{txn_a['id']} | {txn_a['date']} | {txn_a['description']} | {print_amt(txn_a['amount'])}")
    print(f"    流水B: #{txn_b['id']} | {txn_b['date']} | {txn_b['description']} | {print_amt(txn_b['amount'])}")

    items = [(txn_a["id"], "source"), (txn_b["id"], "destination")]
    manual_id = database.create_internal_transfer("transfer", "手动测试:双支出_信用卡还款", items)
    manual_t = database.get_internal_transfer_by_id(manual_id)
    if manual_t:
        print(f"  [OK] 双支出归并创建成功，ID: #{manual_t['id']}")

final_transfers = database.get_internal_transfers()
final_txn_ids = database.get_internal_transfer_txn_ids()
same_dir_final = 0
for t in final_transfers:
    if t["items"]:
        types = {item["type"] for item in t["items"]}
        if len(types) == 1:
            same_dir_final += 1

print()
print("=" * 70)
print("TEST 总结")
print("=" * 70)
print(f"  总流水数: {len(database.get_transactions())}")
print(f"  归并记录数: {len(final_transfers)}")
print(f"    - 同方向配对: {same_dir_final}")
print(f"    - 异方向配对: {len(final_transfers) - same_dir_final}")
print(f"  涉及流水数: {len(final_txn_ids)}")
print(f"  日常收支流水: {len(database.get_transactions()) - len(final_txn_ids)}")
print()
print("[DONE] 所有测试场景执行完成！")
print("=" * 70)
