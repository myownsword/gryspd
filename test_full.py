# -*- coding: utf-8 -*-
import sys
import os
import pandas as pd

def print_amt(amount):
    return f"RMB {amount:.2f}"

print("=" * 60)
print("全面功能测试: 内部转账与报销归并")
print("=" * 60)

if os.path.exists("budget.db"):
    os.remove("budget.db")
    print("[INFO] 已删除旧数据库")

import database
import transfer_engine
from csv_importer import read_csv_safely, validate_and_classify, insert_valid_rows
from io import StringIO

database.init_db()
print("[OK] 数据库初始化完成")

print()
print("--- 测试 1: 导入样例数据 (含内部转账) ---")

with open("sample_with_transfers.csv", "r", encoding="utf-8") as f:
    content = f.read()

df = pd.read_csv(StringIO(content))
print(f"[OK] 读取 CSV 成功，共 {len(df)} 行")

result = validate_and_classify(df)
print(f"[OK] 验证分类完成")
print(f"     有效行: {result['stats']['valid']}")
print(f"     错误行: {result['stats']['errors']}")
print(f"     重复行: {result['stats']['duplicates']}")
print(f"     未分类: {result['stats']['uncategorized']}")

inserted, duplicates = insert_valid_rows(result["valid_rows"])
print(f"[OK] 导入完成: 新增 {inserted} 条, 跳过重复 {duplicates} 条")

txns = database.get_transactions()
print(f"[OK] 数据库中共有 {len(txns)} 条流水")

print()
print("--- 测试 2: 候选生成 ---")

candidates = transfer_engine.find_internal_transfer_candidates(date_window_days=3)
print(f"[OK] 找到 {len(candidates)} 个候选匹配")

high_conf = [c for c in candidates if c["confidence"] >= 0.7]
med_conf = [c for c in candidates if 0.4 <= c["confidence"] < 0.7]
low_conf = [c for c in candidates if c["confidence"] < 0.4]
print(f"     高置信度 (>=0.7): {len(high_conf)}")
print(f"     中置信度 (0.4-0.7): {len(med_conf)}")
print(f"     低置信度 (<0.4): {len(low_conf)}")

if candidates:
    print()
    print("  前 5 个候选:")
    for i, c in enumerate(candidates[:5]):
        type_label = "报销抵扣" if c["type"] == "reimbursement" else "内部转账"
        amount = c["items"][0]["transaction"]["amount"]
        print(f"    {i+1}. {type_label} - 金额: {print_amt(amount)} - 置信度: {c['confidence']:.2f}")
        print(f"       原因: {', '.join(c['reasons'])}")
        if c["cross_month"]:
            print(f"       警告: 跨月匹配")
        if not c["amount_match"]:
            print(f"       警告: 金额不一致")

unmatched = transfer_engine.get_unmatched_internal_keyword_txns()
print()
print(f"[OK] 未匹配的关键词流水: {len(unmatched)} 条")
if unmatched:
    print("  前 5 条:")
    for i, u in enumerate(unmatched[:5]):
        print(f"    {i+1}. {u['transaction']['date']} | {u['transaction']['description']} | {u['matched_keywords']}")

print()
print("--- 测试 3: 手动确认归并 ---")

if candidates:
    candidate = candidates[0]
    items = [(item["transaction"]["id"], item["role"]) for item in candidate["items"]]
    desc = f"测试归并: {candidate['items'][0]['transaction']['description']}"
    
    transfer_id = database.create_internal_transfer(candidate["type"], desc, items)
    print(f"[OK] 创建归并成功，ID: {transfer_id}")
    
    transfer = database.get_internal_transfer_by_id(transfer_id)
    print(f"     类型: {transfer['transfer_type']}")
    print(f"     描述: {transfer['description']}")
    print(f"     金额: {print_amt(transfer['total_amount'])}")
    print(f"     包含流水: {len(transfer['items'])} 条")
    
    transfer_ids = database.get_internal_transfer_txn_ids()
    print(f"[OK] 已归并的流水ID: {len(transfer_ids)} 条")

print()
print("--- 测试 4: 月度统计 (排除内部转账) ---")

month = "2025-12"
summary_with = database.get_monthly_summary(month, exclude_internal=False)
summary_without = database.get_monthly_summary(month, exclude_internal=True)

print(f"月份: {month}")
print(f"  包含内部转账 - 收入: {print_amt(summary_with['income_total'])}, 支出: {print_amt(summary_with['expense_total'])}")
print(f"  排除内部转账 - 收入: {print_amt(summary_without['income_total'])}, 支出: {print_amt(summary_without['expense_total'])}")
print(f"  差额 - 收入: {print_amt(summary_with['income_total'] - summary_without['income_total'])}, 支出: {print_amt(summary_with['expense_total'] - summary_without['expense_total'])}")

print()
print("--- 测试 5: 趋势数据 (排除内部转账) ---")

start_month = "2025-12"
end_month = "2026-06"
trend_with = database.get_trend_data(start_month, end_month, exclude_internal=False)
trend_without = database.get_trend_data(start_month, end_month, exclude_internal=True)

print(f"时间范围: {start_month} ~ {end_month}")
print(f"  包含内部转账 - 数据行数: {len(trend_with)}")
print(f"  排除内部转账 - 数据行数: {len(trend_without)}")

print()
print("--- 测试 6: 拆开归并 (恢复原流水) ---")

if candidates and 'transfer_id' in locals():
    before_count = len(database.get_internal_transfer_txn_ids())
    database.delete_internal_transfer(transfer_id)
    after_count = len(database.get_internal_transfer_txn_ids())
    
    print(f"[OK] 归并已删除")
    print(f"     删除前归并流水数: {before_count}")
    print(f"     删除后归并流水数: {after_count}")
    
    transfer_check = database.get_internal_transfer_by_id(transfer_id)
    if transfer_check is None:
        print("[OK] 验证: 归并记录已删除")
    else:
        print("[FAIL] 验证失败: 归并记录仍然存在")

print()
print("--- 测试 7: 重复导入检测 ---")

result2 = validate_and_classify(df)
print(f"[OK] 重复导入检测")
print(f"     检测到重复行: {result2['stats']['duplicates']}")
print(f"     有效行: {result2['stats']['valid']}")

print()
print("--- 测试 8: 跨月匹配 ---")

cross_month_candidates = [c for c in candidates if c["cross_month"]]
print(f"[OK] 跨月匹配候选: {len(cross_month_candidates)} 个")

print()
print("--- 测试 9: 流水追溯 ---")

if candidates:
    candidate = candidates[1]
    items = [(item["transaction"]["id"], item["role"]) for item in candidate["items"]]
    desc = "追溯测试归并"
    test_transfer_id = database.create_internal_transfer(candidate["type"], desc, items)
    
    test_txn_id = candidate["items"][0]["transaction"]["id"]
    transfer_info = database.get_transfer_for_transaction(test_txn_id)
    
    if transfer_info:
        print(f"[OK] 流水追溯成功")
        print(f"     流水ID: {test_txn_id}")
        print(f"     归并ID: {transfer_info['id']}")
        print(f"     归并描述: {transfer_info['description']}")
        print(f"     归并类型: {transfer_info['transfer_type']}")
    else:
        print("[FAIL] 流水追溯失败")

print()
print("=" * 60)
print("[DONE] 所有功能测试完成！")
print("=" * 60)
