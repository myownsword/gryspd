# -*- coding: utf-8 -*-
import sys
import os

print("开始测试...")
print(f"当前目录: {os.getcwd()}")

try:
    import database
    print('[OK] database 模块导入成功')
    database.init_db()
    print('[OK] 数据库初始化成功')
    
    txns = database.get_transactions()
    print(f'[OK] 读取流水成功，共 {len(txns)} 条')
    
    transfers = database.get_internal_transfers()
    print(f'[OK] 读取内部转账成功，共 {len(transfers)} 条')
    
    monthly = database.get_monthly_summary("2026-01")
    print(f'[OK] 月度统计成功: 收入 {monthly["income_total"]}, 支出 {monthly["expense_total"]}')
    
except Exception as e:
    print(f'[FAIL] database 模块错误: {e}')
    import traceback
    traceback.print_exc()
    sys.exit(1)

try:
    import transfer_engine
    print('[OK] transfer_engine 模块导入成功')
    
    candidates = transfer_engine.find_internal_transfer_candidates()
    print(f'[OK] 候选匹配成功，找到 {len(candidates)} 个候选')
    
    unmatched = transfer_engine.get_unmatched_internal_keyword_txns()
    print(f'[OK] 未匹配关键词检测成功，找到 {len(unmatched)} 条')
    
except Exception as e:
    print(f'[FAIL] transfer_engine 模块错误: {e}')
    import traceback
    traceback.print_exc()
    sys.exit(1)

print()
print('[DONE] 所有核心功能测试通过！')
