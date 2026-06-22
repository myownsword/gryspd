import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
from io import StringIO

from database import (
    init_db, get_transactions, get_categories,
    get_rules, add_rule, update_rule, delete_rule,
    get_budgets, set_budget, get_monthly_summary,
    get_trend_data, update_transaction_category,
    add_category, update_rule_priority, reorder_rules,
    create_internal_transfer, delete_internal_transfer,
    get_internal_transfers, get_internal_transfer_by_id,
    get_transfer_for_transaction, get_internal_transfer_txn_ids,
    update_internal_transfer
)
from csv_importer import (
    read_csv_safely, validate_and_classify,
    insert_valid_rows, classify_by_rules
)
from rule_engine import (
    apply_rules_to_transactions, create_rule_from_transaction,
    get_rule_stats, suggest_rules_from_uncategorized
)
from transfer_engine import (
    find_internal_transfer_candidates, find_transfers_with_keywords,
    get_unmatched_internal_keyword_txns, auto_detect_transfer_type,
    INTERNAL_KEYWORDS_ALL
)

st.set_page_config(
    page_title="个人预算复盘工具",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded"
)

init_db()

st.markdown("""
<style>
    .main-header { font-size: 2rem; font-weight: bold; color: #1f77b4; margin-bottom: 1rem; }
    .metric-card { border: 1px solid #e0e0e0; border-radius: 8px; padding: 1rem; margin: 0.5rem 0; }
    .warning-card { background-color: #fff3cd; border: 1px solid #ffc107; border-radius: 8px; padding: 1rem; }
    .danger-card { background-color: #f8d7da; border: 1px solid #dc3545; border-radius: 8px; padding: 1rem; }
    .success-card { background-color: #d4edda; border: 1px solid #28a745; border-radius: 8px; padding: 1rem; }
    .info-card { background-color: #d1ecf1; border: 1px solid #17a2b8; border-radius: 8px; padding: 1rem; }
    .stButton button { width: 100%; }
    .rule-item { border: 1px solid #ddd; border-radius: 6px; padding: 8px; margin: 4px 0; background: #fafafa; }
</style>
""", unsafe_allow_html=True)


def get_current_month() -> str:
    return datetime.now().strftime("%Y-%m")


def get_available_months() -> list:
    txns = get_transactions()
    months = set()
    for txn in txns:
        month = txn["date"][:7]
        months.add(month)
    current = get_current_month()
    months.add(current)
    for i in range(1, 12):
        dt = datetime.now() - timedelta(days=30 * i)
        months.add(dt.strftime("%Y-%m"))
    return sorted(list(months), reverse=True)


def format_currency(amount: float) -> str:
    return f"¥{amount:,.2f}"


def render_import_page():
    st.markdown('<div class="main-header">📥 流水导入</div>', unsafe_allow_html=True)

    uploaded_file = st.file_uploader("上传银行流水 CSV 文件", type=["csv"])

    if uploaded_file is not None:
        try:
            df = read_csv_safely(uploaded_file)
            st.success(f"成功读取文件，共 {len(df)} 行数据")

            with st.expander("查看原始数据预览", expanded=False):
                st.dataframe(df.head(20), use_container_width=True)

            with st.spinner("正在验证和分类数据..."):
                result = validate_and_classify(df)

            stats = result["stats"]
            col1, col2, col3, col4, col5 = st.columns(5)
            col1.metric("总行数", stats["total"])
            col2.metric("✅ 有效行", stats["valid"])
            col3.metric("❌ 错误行", stats["errors"])
            col4.metric("🔄 重复行", stats["duplicates"])
            col5.metric("❓ 待分类", stats["uncategorized"])

            if result["error_rows"]:
                st.markdown('<div class="danger-card">⚠️ 发现错误数据</div>', unsafe_allow_html=True)
                with st.expander(f"查看 {len(result['error_rows'])} 条错误行详情", expanded=True):
                    error_df = pd.DataFrame([
                        {
                            "行号": r["row"],
                            "错误信息": r["errors"],
                            "日期": r.get("date", ""),
                            "描述": r.get("description", ""),
                            "金额": r.get("amount", 0)
                        }
                        for r in result["error_rows"]
                    ])
                    st.dataframe(error_df, use_container_width=True)

            if result["duplicate_candidates"]:
                st.markdown('<div class="warning-card">🔄 发现重复流水（已自动跳过）</div>', unsafe_allow_html=True)
                with st.expander(f"查看 {len(result['duplicate_candidates'])} 条重复行", expanded=False):
                    dup_df = pd.DataFrame(result["duplicate_candidates"])
                    st.dataframe(dup_df, use_container_width=True)

            if result["uncategorized_rows"]:
                st.markdown('<div class="info-card">❓ 以下流水未匹配到规则，请手动设置分类</div>', unsafe_allow_html=True)
                categories_exp = get_categories("expense")
                categories_inc = get_categories("income")
                all_cats = categories_inc + categories_exp

                with st.expander(f"手动修正 {len(result['uncategorized_rows'])} 条未分类数据", expanded=True):
                    for i, row in enumerate(result["uncategorized_rows"]):
                        col_info, col_cat, col_save_rule = st.columns([3, 2, 2])
                        with col_info:
                            sign = "+" if row["type"] == "income" else "-"
                            st.write(f"**{row['date']}** | {row['description']} | **{sign}{format_currency(row['amount'])}**")

                        cat_options = [c["name"] for c in (categories_inc if row["type"] == "income" else categories_exp)]
                        current_idx = cat_options.index(row["category"]) if row["category"] in cat_options else 0

                        with col_cat:
                            selected_cat = st.selectbox(
                                "分类",
                                cat_options,
                                index=current_idx,
                                key=f"uncat_cat_{row['row_num']}"
                            )
                            for r in result["valid_rows"]:
                                if r["row_num"] == row["row_num"]:
                                    r["category"] = selected_cat
                                    break

                        with col_save_rule:
                            if st.checkbox("保存为规则", key=f"save_rule_cb_{row['row_num']}"):
                                rule_keyword = st.text_input(
                                    "关键词",
                                    value=row["description"][:6] if len(row["description"]) >= 6 else row["description"],
                                    key=f"rule_kw_{row['row_num']}"
                                )
                                if st.button("✓ 保存规则并分类", key=f"apply_rule_{row['row_num']}"):
                                    txn_type = row["type"]
                                    rules = get_rules(txn_type)
                                    priority = (rules[0]["priority"] + 1) if rules else 10
                                    rule_id = add_rule(rule_keyword, selected_cat, txn_type, priority)
                                    for r in result["valid_rows"]:
                                        if r["row_num"] == row["row_num"]:
                                            r["category"] = selected_cat
                                            r["rule_id"] = rule_id
                                            break
                                    st.success(f"规则已保存：'{rule_keyword}' → {selected_cat}")
                                    st.rerun()

            valid_preview = result["valid_rows"]
            if valid_preview:
                with st.expander("预览即将导入的数据", expanded=False):
                    preview_df = pd.DataFrame([
                        {
                            "日期": r["date"],
                            "描述": r["description"],
                            "类型": "收入" if r["type"] == "income" else "支出",
                            "金额": r["amount"] if r["type"] == "income" else -r["amount"],
                            "分类": r["category"]
                        }
                        for r in valid_preview
                    ])
                    st.dataframe(preview_df, use_container_width=True)

                if st.button("🚀 确认导入数据", type="primary"):
                    inserted, duplicates = insert_valid_rows(valid_preview)
                    msg = f"导入完成！新增 {inserted} 条，跳过重复 {duplicates + result['stats']['duplicates']} 条"
                    if duplicates + result['stats']['duplicates'] > 0:
                        st.warning(msg + " ⚠️ 重复导入已自动跳过，不会产生重复数据")
                    else:
                        st.success(msg)

                    has_internal_keywords = False
                    internal_count = 0
                    income_internal = 0
                    expense_internal = 0
                    for r in valid_preview:
                        if any(kw in r["description"] for kw in INTERNAL_KEYWORDS_ALL):
                            internal_count += 1
                            has_internal_keywords = True
                            if r["type"] == "income":
                                income_internal += 1
                            else:
                                expense_internal += 1

                    if has_internal_keywords:
                        internal_msg = (
                            f"💡 检测到 {internal_count} 条可能为内部转账/报销的流水 "
                            f"(收入{income_internal}条/支出{expense_internal}条)\n\n"
                            f"常见场景:\n"
                            f"• 双收入: 微信零钱提现 + 银行卡到账 → 请前往「内部转账」归并\n"
                            f"• 双支出: 信用卡还款 + 自动扣款 → 请前往「内部转账」归并\n"
                            f"• 一收一支: 普通银行转账、同事报销抵扣 → 系统自动提示候选"
                        )
                        st.info(internal_msg)

                    if stats["uncategorized"] > 0:
                        st.info("💡 建议前往「分类规则」页面，从高频未分类描述中生成规则")
                    st.rerun()

        except Exception as e:
            st.error(f"文件处理失败：{str(e)}")
            st.info("请确保 CSV 文件包含日期、描述、金额等基本列")

    st.divider()
    st.subheader("📊 已有流水概览")
    all_txns = get_transactions()
    if all_txns:
        transfer_txn_ids = get_internal_transfer_txn_ids()

        col_filter, col_stats = st.columns([2, 3])
        with col_filter:
            show_internal = st.selectbox(
                "显示范围",
                ["全部流水", "仅日常收支 (不含内部转账)", "仅内部转账"],
                index=0
            )

        with col_stats:
            internal_count = len(transfer_txn_ids)
            normal_count = len(all_txns) - internal_count
            st.markdown(
                f"📊 共 {len(all_txns)} 条 | "
                f"✅ 日常收支: {normal_count} 条 | "
                f"🔄 内部转账: {internal_count} 条"
            )

        display_txns = all_txns
        if show_internal == "仅日常收支 (不含内部转账)":
            display_txns = [t for t in all_txns if t["id"] not in transfer_txn_ids]
        elif show_internal == "仅内部转账":
            display_txns = [t for t in all_txns if t["id"] in transfer_txn_ids]

        txn_df = pd.DataFrame([
            {
                "ID": t["id"],
                "日期": t["date"],
                "描述": t["description"],
                "类型": "收入" if t["type"] == "income" else "支出",
                "金额": t["amount"] if t["type"] == "income" else -t["amount"],
                "分类": t["category"],
                "状态": "🔄 内部转账" if t["id"] in transfer_txn_ids else "✅ 正常"
            }
            for t in display_txns
        ])
        st.dataframe(txn_df.head(200), use_container_width=True, hide_index=True)

        if show_internal != "仅日常收支 (不含内部转账)":
            st.divider()
            st.subheader("🔍 流水详情与归并追溯")
            detail_txn_id = st.number_input(
                "输入流水ID查看详情",
                min_value=1,
                value=1 if all_txns else 0,
                step=1
            )
            if detail_txn_id:
                detail_txn = None
                for t in all_txns:
                    if t["id"] == detail_txn_id:
                        detail_txn = t
                        break

                if detail_txn:
                    col1, col2 = st.columns(2)
                    with col1:
                        st.write(f"**日期:** {detail_txn['date']}")
                        st.write(f"**描述:** {detail_txn['description']}")
                        st.write(f"**类型:** {'收入' if detail_txn['type'] == 'income' else '支出'}")
                        st.write(f"**金额:** {format_currency(detail_txn['amount'] if detail_txn['type'] == 'income' else -detail_txn['amount'])}")
                        st.write(f"**分类:** {detail_txn['category']}")

                    transfer_info = get_transfer_for_transaction(detail_txn_id)
                    with col2:
                        if transfer_info:
                            type_icon = "💼" if transfer_info["transfer_type"] == "reimbursement" else "🔄"
                            type_label = "报销抵扣" if transfer_info["transfer_type"] == "reimbursement" else "内部转账"
                            st.markdown(f"**归并状态:** {type_icon} 已归并为{type_label}")
                            st.write(f"**归并ID:** #{transfer_info['id']}")
                            st.write(f"**归并描述:** {transfer_info['description']}")
                            st.write(f"**归并时间:** {transfer_info['created_at']}")

                            if st.button("📂 查看完整归并"):
                                st.session_state["transfer_detail_id"] = transfer_info["id"]

                            if st.button("🗑️ 拆开此归并"):
                                delete_internal_transfer(transfer_info["id"])
                                st.success("已拆开归并，流水恢复为独立记录")
                                st.rerun()
                        else:
                            st.info("此流水为独立记录，未参与归并")
                            if st.button("➕ 加入手动归并"):
                                st.info("请前往「内部转账」页面的「手动创建」标签进行操作")
                else:
                    st.warning("未找到该流水ID")

        st.divider()
        st.subheader("✏️ 手动修正分类")
        cat_exp = [c["name"] for c in get_categories("expense")]
        cat_inc = [c["name"] for c in get_categories("income")]

        edit_ids = st.multiselect("选择要修改的流水ID", options=[t["id"] for t in display_txns], format_func=lambda x: f"#{x}")
        if edit_ids:
            has_internal = any(tid in transfer_txn_ids for tid in edit_ids)
            if has_internal:
                st.warning("⚠️ 您选择了已归并的内部转账流水，修改分类不会影响收支统计，但建议保持分类一致")

            edit_type_selected = st.radio("分类类型", ["支出", "收入"])
            cat_list = cat_inc if edit_type_selected == "收入" else cat_exp
            new_cat = st.selectbox("选择新分类", cat_list)

            col_save, col_save_rule = st.columns(2)
            with col_save:
                if st.button("💾 仅修改分类"):
                    new_type = "income" if edit_type_selected == "收入" else "expense"
                    for tid in edit_ids:
                        update_transaction_category(tid, new_cat, None)
                    st.success(f"已更新 {len(edit_ids)} 条记录分类")
                    st.rerun()
            with col_save_rule:
                sample_txn = None
                for t in all_txns:
                    if t["id"] == edit_ids[0]:
                        sample_txn = t
                        break
                if sample_txn:
                    default_kw = sample_txn["description"][:6] if len(sample_txn["description"]) >= 6 else sample_txn["description"]
                    rule_kw = st.text_input("规则关键词", value=default_kw)
                    if st.button("💾 保存规则并修改"):
                        new_type = "income" if edit_type_selected == "收入" else "expense"
                        rules = get_rules(new_type)
                        priority = (rules[0]["priority"] + 1) if rules else 10
                        rule_id = add_rule(rule_kw, new_cat, new_type, priority)
                        for tid in edit_ids:
                            update_transaction_category(tid, new_cat, rule_id)
                        st.success(f"规则已保存并更新 {len(edit_ids)} 条记录")
                        st.rerun()
    else:
        st.info("暂无数据，请先导入银行流水 CSV")


def render_rules_page():
    st.markdown('<div class="main-header">⚙️ 分类规则管理</div>', unsafe_allow_html=True)

    tab_exp, tab_inc = st.tabs(["支出规则", "收入规则"])

    def render_rules_tab(rtype: str, type_label: str):
        rules = get_rules(rtype)
        categories = [c["name"] for c in get_categories(rtype)]
        stats = get_rule_stats()

        st.subheader(f"{type_label}规则 (共 {len(rules)} 条)")

        rule_df = pd.DataFrame([
            {
                "ID": r["id"],
                "优先级": r["priority"],
                "关键词": r["keyword"],
                "分类": r["category"],
                "使用次数": stats["rule_usage"].get(r["id"], {}).get("count", 0),
                "累计金额": stats["rule_usage"].get(r["id"], {}).get("total_amount", 0)
            }
            for r in rules
        ])
        st.dataframe(rule_df, use_container_width=True, hide_index=True)

        st.divider()
        st.markdown("### 🔢 调整规则优先级")
        st.info("💡 优先级高的规则先匹配。使用下方按钮上移/下移，或直接拖动列表排序。")

        rule_ids = [r["id"] for r in rules]
        rule_display = {r["id"]: f"[优先级:{r['priority']}] '{r['keyword']}' → {r['category']}" for r in rules}

        ordered_ids = st.multiselect(
            "拖放调整顺序（从上到下 = 高到低优先级）",
            options=rule_ids,
            default=rule_ids,
            format_func=lambda x: rule_display[x]
        )

        if ordered_ids and len(ordered_ids) == len(rule_ids):
            original_order = rule_ids
            if ordered_ids != original_order:
                if st.button("💾 应用新排序", type="primary"):
                    reorder_rules(rtype, ordered_ids)
                    st.success("规则优先级已更新")
                    st.rerun()
        elif ordered_ids:
            st.warning("请确保包含所有规则后再保存")

        st.divider()
        col1, col2 = st.columns(2)

        with col1:
            st.markdown("### ➕ 添加新规则")
            with st.form(f"add_rule_{rtype}"):
                new_keyword = st.text_input("关键词 (模糊匹配)")
                new_category = st.selectbox("分类", categories)
                priority_val = st.number_input("优先级 (数字越大越优先)", value=5, min_value=0)
                submitted = st.form_submit_button("添加规则", type="primary")
                if submitted and new_keyword:
                    add_rule(new_keyword, new_category, rtype, priority_val)
                    st.success(f"规则已添加：'{new_keyword}' → {new_category}")
                    st.rerun()

        with col2:
            st.markdown("### ✏️ 编辑/删除规则")
            if rules:
                selected_rule_id = st.selectbox(
                    "选择规则",
                    options=[r["id"] for r in rules],
                    format_func=lambda x: rule_display.get(x, str(x)),
                    key=f"select_{rtype}"
                )
                selected_rule = next((r for r in rules if r["id"] == selected_rule_id), None)
                if selected_rule:
                    with st.form(f"edit_rule_{rtype}"):
                        edit_kw = st.text_input("关键词", value=selected_rule["keyword"])
                        edit_cat = st.selectbox(
                            "分类",
                            categories,
                            index=categories.index(selected_rule["category"]) if selected_rule["category"] in categories else 0
                        )
                        edit_prio = st.number_input("优先级", value=selected_rule["priority"], min_value=0)
                        col_upd, col_del = st.columns(2)
                        with col_upd:
                            if st.form_submit_button("更新规则"):
                                update_rule(selected_rule_id, edit_kw, edit_cat, edit_prio)
                                st.success("规则已更新")
                                st.rerun()
                        with col_del:
                            if st.form_submit_button("🗑️ 删除规则"):
                                delete_rule(selected_rule_id)
                                st.warning("规则已删除")
                                st.rerun()

        st.divider()
        st.markdown("### 💡 智能建议")
        suggestions = suggest_rules_from_uncategorized(min_count=1)
        if suggestions:
            suggestion_filtered = [s for s in suggestions if (rtype == "expense" and s["type"] == "expense") or (rtype == "income" and s["type"] == "income")]
            if suggestion_filtered:
                st.info(f"发现 {len(suggestion_filtered)} 个高频描述尚未有规则匹配")
                for s in suggestion_filtered[:10]:
                    col_s, c_cat, c_btn = st.columns([3, 2, 1])
                    with col_s:
                        st.write(f"**{s['description']}** | 出现 {s['count']} 次 | 累计: {format_currency(s['total_amount'])}")
                    with c_cat:
                        sug_cat = st.selectbox(
                            "建议分类",
                            categories,
                            key=f"sug_cat_{rtype}_{s['description']}"
                        )
                    with c_btn:
                        if st.button("生成规则", key=f"sug_btn_{rtype}_{s['description']}"):
                            rs = get_rules(rtype)
                            prio = (rs[0]["priority"] + 1) if rs else 10
                            add_rule(s["keyword"], sug_cat, rtype, prio)
                            st.success(f"已创建规则：'{s['keyword']}' → {sug_cat}")
                            st.rerun()
            else:
                st.success("🎉 所有描述都有规则匹配，没有需要建议的")
        else:
            st.success("🎉 目前没有未分类的高频描述")

        st.divider()
        if st.button(f"🔄 重新应用所有{type_label}规则到历史流水"):
            updated, unchanged = apply_rules_to_transactions()
            st.success(f"已完成，更新 {updated} 条分类，{unchanged} 条无变化")
            st.rerun()

    with tab_exp:
        render_rules_tab("expense", "支出")
    with tab_inc:
        render_rules_tab("income", "收入")

    st.divider()
    st.subheader("🗂️ 分类管理")
    col_new_cat_type, col_new_cat_name = st.columns(2)
    with col_new_cat_type:
        new_cat_type = st.radio("类型", ["支出", "收入"], horizontal=True, key="new_cat_type")
    with col_new_cat_name:
        new_cat_name = st.text_input("新分类名称", key="new_cat_name")
    if st.button("➕ 添加分类") and new_cat_name:
        t = "income" if new_cat_type == "收入" else "expense"
        add_category(new_cat_name, t)
        st.success(f"分类已添加：{new_cat_name}")
        st.rerun()

    all_cats = get_categories()
    cat_df = pd.DataFrame([
        {"名称": c["name"], "类型": "收入" if c["type"] == "income" else "支出"}
        for c in all_cats
    ])
    st.dataframe(cat_df, use_container_width=True, hide_index=True)


def render_budget_page():
    st.markdown('<div class="main-header">📋 月度预算</div>', unsafe_allow_html=True)

    months = get_available_months()
    selected_month = st.selectbox("选择月份", months, index=0)

    col_mode, col_info = st.columns([1, 3])
    with col_mode:
        exclude_internal = st.checkbox(
            "排除内部转账",
            value=True,
            help="内部转账/报销抵扣不计入日常收支和预算"
        )
    with col_info:
        if exclude_internal:
            st.info("💡 当前统计已排除内部转账和报销抵扣，仅显示日常收支")

    summary = get_monthly_summary(selected_month, exclude_internal=exclude_internal)
    summary_all = get_monthly_summary(selected_month, exclude_internal=False)
    budgets = get_budgets(selected_month)
    expense_cats = get_categories("expense")
    expense_cat_names = [c["name"] for c in expense_cats]

    internal_expense = summary_all["expense_total"] - summary["expense_total"]
    internal_income = summary_all["income_total"] - summary["income_total"]

    col1, col2, col3 = st.columns(3)
    col1.metric("💰 总收入", format_currency(summary["income_total"]))
    col2.metric("💸 总支出", format_currency(summary["expense_total"]))
    balance_color = "green" if summary["balance"] >= 0 else "red"
    col3.markdown(
        f"<h3 style='text-align:center;color:{balance_color}'>"
        f"{'📈 结余' if summary['balance'] >= 0 else '📉 赤字'}: {format_currency(summary['balance'])}</h3>",
        unsafe_allow_html=True
    )

    if exclude_internal and (internal_expense > 0 or internal_income > 0):
        st.markdown(f"""
        <div class="info-card">
        🔄 **内部转账统计** (已排除): 支出 {format_currency(internal_expense)} | 收入 {format_currency(internal_income)}
        </div>
        """, unsafe_allow_html=True)

    st.divider()
    st.subheader("🎯 设置预算")
    budget_dict = {b["category"]: b["amount"] for b in budgets}

    with st.expander("编辑各分类预算", expanded=True):
        changed_budgets = {}
        for cat in expense_cat_names:
            col_cat, col_amt = st.columns([2, 3])
            current = budget_dict.get(cat, 0.0)
            actual = summary["by_category"].get(cat, {}).get("total", 0.0)

            with col_cat:
                if cat in summary["by_category"]:
                    pct = (actual / current * 100) if current > 0 else 100
                    if current > 0 and actual >= current:
                        st.markdown(f'<div class="danger-card">🔴 **{cat}**</div>', unsafe_allow_html=True)
                    elif current > 0 and actual >= current * 0.8:
                        st.markdown(f'<div class="warning-card">🟡 **{cat}**</div>', unsafe_allow_html=True)
                    else:
                        st.markdown(f'<div class="success-card">🟢 **{cat}**</div>', unsafe_allow_html=True)
                else:
                    st.markdown(f"**{cat}**")

            with col_amt:
                val = st.number_input(
                    f"预算金额 - {cat}",
                    value=float(current),
                    min_value=0.0,
                    step=100.0,
                    key=f"budget_{cat}",
                    label_visibility="collapsed"
                )
                if val != current:
                    changed_budgets[cat] = val

        if changed_budgets and st.button("💾 保存预算更改", type="primary"):
            for cat, amt in changed_budgets.items():
                set_budget(cat, amt, selected_month)
            st.success(f"已更新 {len(changed_budgets)} 个分类的预算")
            st.rerun()

    st.divider()
    st.subheader("📊 预算执行情况")

    budget_rows = []
    over_budget = []
    near_budget = []

    for cat in expense_cat_names:
        budget_amt = budget_dict.get(cat, 0.0)
        actual_amt = summary["by_category"].get(cat, {}).get("total", 0.0)

        if budget_amt > 0:
            usage_pct = actual_amt / budget_amt * 100
            remaining = budget_amt - actual_amt
            status = "正常"
            status_class = "success"

            if actual_amt >= budget_amt:
                status = "超支 ⚠️"
                status_class = "danger"
                over_budget.append(cat)
            elif actual_amt >= budget_amt * 0.8:
                status = "接近上限"
                status_class = "warning"
                near_budget.append(cat)

            budget_rows.append({
                "分类": cat,
                "预算": budget_amt,
                "实际支出": actual_amt,
                "剩余": remaining,
                "使用率(%)": round(usage_pct, 1),
                "状态": status,
                "状态类": status_class
            })
        elif actual_amt > 0:
            budget_rows.append({
                "分类": cat,
                "预算": 0,
                "实际支出": actual_amt,
                "剩余": 0,
                "使用率(%)": "-",
                "状态": "无预算",
                "状态类": "info"
            })

    if over_budget:
        st.markdown(
            f'<div class="danger-card">🚨 <b>超支提醒：</b>{"、".join(over_budget)} 已超出预算！</div>',
            unsafe_allow_html=True
        )
    if near_budget:
        st.markdown(
            f'<div class="warning-card">⚠️ <b>接近上限：</b>{"、".join(near_budget)} 支出已达80%以上</div>',
            unsafe_allow_html=True
        )

    if budget_rows:
        display_rows = []
        for r in budget_rows:
            row_class = r["状态类"]
            if row_class == "danger":
                display_rows.append({
                    "分类": f"🔴 {r['分类']}",
                    "预算": format_currency(r["预算"]),
                    "实际支出": format_currency(r["实际支出"]),
                    "剩余": format_currency(r["剩余"]),
                    "使用率(%)": r["使用率(%)"],
                    "状态": r["状态"]
                })
            elif row_class == "warning":
                display_rows.append({
                    "分类": f"🟡 {r['分类']}",
                    "预算": format_currency(r["预算"]),
                    "实际支出": format_currency(r["实际支出"]),
                    "剩余": format_currency(r["剩余"]),
                    "使用率(%)": r["使用率(%)"],
                    "状态": r["状态"]
                })
            else:
                prefix = "🟢" if row_class == "success" else "ℹ️"
                display_rows.append({
                    "分类": f"{prefix} {r['分类']}",
                    "预算": format_currency(r["预算"]),
                    "实际支出": format_currency(r["实际支出"]),
                    "剩余": format_currency(r["剩余"]),
                    "使用率(%)": r["使用率(%)"],
                    "状态": r["状态"]
                })

        st.dataframe(pd.DataFrame(display_rows), use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("📈 预算vs实际 对比图")

        chart_data = pd.DataFrame([
            {
                "分类": r["分类"],
                "金额": r["预算"],
                "类型": "预算"
            } for r in budget_rows
        ] + [
            {
                "分类": r["分类"],
                "金额": r["实际支出"],
                "类型": "实际支出"
            } for r in budget_rows
        ])

        fig = px.bar(
            chart_data,
            x="分类",
            y="金额",
            color="类型",
            barmode="group",
            title=f"{selected_month} 预算执行对比",
            color_discrete_map={"预算": "#1f77b4", "实际支出": "#ff7f0e"}
        )
        fig.update_layout(xaxis_tickangle=-45)
        st.plotly_chart(fig, use_container_width=True)

        fig_pie = px.pie(
            pd.DataFrame([r for r in budget_rows if r["实际支出"] > 0]),
            values="实际支出",
            names="分类",
            title=f"{selected_month} 支出分类占比"
        )
        st.plotly_chart(fig_pie, use_container_width=True)
    else:
        st.info("暂无支出数据或预算设置")


def render_trend_page():
    st.markdown('<div class="main-header">📈 收支趋势分析</div>', unsafe_allow_html=True)

    months = get_available_months()
    if len(months) >= 2:
        default_end = months[0]
        default_start = months[-1] if len(months) <= 6 else months[5]

        col_start, col_end = st.columns(2)
        with col_start:
            start_month = st.selectbox("开始月份", months, index=min(months.index(default_start), len(months) - 1))
        with col_end:
            end_month_idx = months.index(default_end)
            start_idx = months.index(start_month)
            end_options = months[:start_idx + 1] if start_idx < len(months) else months
            end_month = st.selectbox("结束月份", end_options, index=0)
    else:
        start_month = end_month = months[0] if months else get_current_month()
        st.info("数据较少，请先导入更多月份的流水")

    if start_month > end_month:
        start_month, end_month = end_month, start_month

    col_mode, col_info = st.columns([1, 3])
    with col_mode:
        exclude_internal = st.checkbox(
            "排除内部转账",
            value=True,
            help="内部转账/报销抵扣不计入趋势统计"
        )
    with col_info:
        if exclude_internal:
            st.info("💡 当前趋势已排除内部转账和报销抵扣，仅显示日常收支")

    trend_data = get_trend_data(start_month, end_month, exclude_internal=exclude_internal)
    trend_data_all = get_trend_data(start_month, end_month, exclude_internal=False)
    summary_by_month = {}

    for item in trend_data:
        m = item["month"]
        if m not in summary_by_month:
            summary_by_month[m] = {"income": 0, "expense": 0}
        if item["type"] == "income":
            summary_by_month[m]["income"] += item["total"]
        else:
            summary_by_month[m]["expense"] += item["total"]

    summary_all_by_month = {}
    for item in trend_data_all:
        m = item["month"]
        if m not in summary_all_by_month:
            summary_all_by_month[m] = {"income": 0, "expense": 0}
        if item["type"] == "income":
            summary_all_by_month[m]["income"] += item["total"]
        else:
            summary_all_by_month[m]["expense"] += item["total"]

    month_list = sorted(summary_by_month.keys())
    if not month_list:
        month_list = [start_month] if start_month == end_month else [start_month, end_month]
        for m in month_list:
            summary_by_month[m] = {"income": 0, "expense": 0}

    col1, col2, col3 = st.columns(3)
    total_income = sum(v["income"] for v in summary_by_month.values())
    total_expense = sum(v["expense"] for v in summary_by_month.values())
    col1.metric(f"{start_month} ~ {end_month} 总收入", format_currency(total_income))
    col2.metric(f"{start_month} ~ {end_month} 总支出", format_currency(total_expense))
    balance = total_income - total_expense
    col3.metric("累计结余", format_currency(balance), delta=f"{len(month_list)} 个月")

    if exclude_internal and summary_all_by_month:
        total_income_all = sum(v["income"] for v in summary_all_by_month.values())
        total_expense_all = sum(v["expense"] for v in summary_all_by_month.values())
        internal_income = total_income_all - total_income
        internal_expense = total_expense_all - total_expense
        if internal_income > 0 or internal_expense > 0:
            st.markdown(f"""
            <div class="info-card">
            🔄 **内部转账统计** (已排除): 收入 {format_currency(internal_income)} | 支出 {format_currency(internal_expense)}
            </div>
            """, unsafe_allow_html=True)

    st.divider()

    month_df = pd.DataFrame([
        {
            "月份": m,
            "收入": summary_by_month[m]["income"],
            "支出": summary_by_month[m]["expense"],
            "结余": summary_by_month[m]["income"] - summary_by_month[m]["expense"]
        }
        for m in month_list
    ])

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=month_df["月份"],
        y=month_df["收入"],
        name="收入",
        marker_color="#28a745"
    ))
    fig.add_trace(go.Bar(
        x=month_df["月份"],
        y=month_df["支出"],
        name="支出",
        marker_color="#dc3545"
    ))
    fig.add_trace(go.Scatter(
        x=month_df["月份"],
        y=month_df["结余"],
        name="结余",
        mode="lines+markers",
        line=dict(color="#1f77b4", width=3),
        marker=dict(size=10)
    ))
    fig.update_layout(
        title=f"{start_month} ~ {end_month} 月度收支趋势",
        barmode="group",
        hovermode="x unified"
    )
    st.plotly_chart(fig, use_container_width=True)

    st.divider()
    st.subheader("📊 分类趋势分析")

    view_type = st.radio("查看类型", ["支出分类趋势", "收入分类趋势"], horizontal=True)
    target_type = "expense" if view_type == "支出分类趋势" else "income"
    cats = [c["name"] for c in get_categories(target_type)]

    default_cats = []
    type_totals = {}
    for item in trend_data:
        if item["type"] == target_type:
            type_totals[item["category"]] = type_totals.get(item["category"], 0) + item["total"]
    sorted_cats = sorted(type_totals.items(), key=lambda x: x[1], reverse=True)
    default_cats = [c[0] for c in sorted_cats[:5]] if sorted_cats else []

    selected_cats = st.multiselect(
        "选择分类查看趋势",
        options=cats,
        default=default_cats if default_cats else cats[:3]
    )

    if selected_cats:
        cat_trend = {}
        for m in month_list:
            cat_trend[m] = {c: 0 for c in selected_cats}

        for item in trend_data:
            if item["type"] == target_type and item["category"] in selected_cats:
                cat_trend[item["month"]][item["category"]] = item["total"]

        cat_rows = []
        for m in month_list:
            for c in selected_cats:
                cat_rows.append({
                    "月份": m,
                    "分类": c,
                    "金额": cat_trend[m][c]
                })

        cat_df = pd.DataFrame(cat_rows)

        fig_cat = px.line(
            cat_df,
            x="月份",
            y="金额",
            color="分类",
            markers=True,
            title=f"{view_type} - {start_month} ~ {end_month}"
        )
        fig_cat.update_layout(hovermode="x unified")
        st.plotly_chart(fig_cat, use_container_width=True)

        fig_cat_area = px.area(
            cat_df,
            x="月份",
            y="金额",
            color="分类",
            title=f"{view_type} - 堆积面积图"
        )
        st.plotly_chart(fig_cat_area, use_container_width=True)

        fig_cat_bar = px.bar(
            cat_df,
            x="月份",
            y="金额",
            color="分类",
            title=f"{view_type} - 堆积柱状图"
        )
        st.plotly_chart(fig_cat_bar, use_container_width=True)

    st.divider()
    st.subheader("📋 明细数据")
    st.dataframe(month_df, use_container_width=True, hide_index=True)


def render_internal_transfer_page():
    st.markdown('<div class="main-header">🔄 内部转账与报销归并</div>', unsafe_allow_html=True)

    st.markdown("""
    <div class="info-card">
    💡 **功能说明：**
    - 自动识别银行卡互转、信用卡还款、微信零钱提现、同事报销等内部流水
    - 归并后不计入日常收支，避免预算和趋势统计失真
    - 明细可追溯，随时可拆开恢复
    </div>
    """, unsafe_allow_html=True)

    st.divider()

    tab_candidates, tab_confirmed, tab_manual = st.tabs(
        ["🤖 候选匹配", "✅ 已确认", "✋ 手动创建"]
    )

    with tab_candidates:
        render_candidates_tab()

    with tab_confirmed:
        render_confirmed_tab()

    with tab_manual:
        render_manual_create_tab()


def render_candidates_tab():
    st.subheader("🔍 自动匹配候选")

    col_settings, col_refresh = st.columns([3, 1])
    with col_settings:
        date_window = st.slider("日期窗口 (天)", min_value=1, max_value=15, value=3)

    with col_refresh:
        st.write("")
        if st.button("🔄 重新检测", type="primary"):
            st.rerun()

    with st.spinner("正在分析流水，寻找内部转账候选..."):
        candidates = find_internal_transfer_candidates(date_window_days=date_window)
        unmatched = get_unmatched_internal_keyword_txns()

    if not candidates and not unmatched:
        st.success("🎉 目前没有检测到内部转账候选，所有流水都已处理")
        return

    if candidates:
        st.markdown(f"##### 找到 {len(candidates)} 个匹配候选")

        high_conf = [c for c in candidates if c["confidence"] >= 0.7]
        med_conf = [c for c in candidates if 0.4 <= c["confidence"] < 0.7]
        low_conf = [c for c in candidates if c["confidence"] < 0.4]

        col1, col2, col3 = st.columns(3)
        col1.metric("高置信度 (≥0.7)", len(high_conf))
        col2.metric("中置信度 (0.4-0.7)", len(med_conf))
        col3.metric("低置信度 (<0.4)", len(low_conf))

        same_dir_count = len([c for c in candidates if c.get("same_direction", False)])
        if same_dir_count > 0:
            st.info(f"📌 其中包含 {same_dir_count} 个同方向配对 (双收入/双支出)，请仔细核对角色")

        st.divider()

        for idx, candidate in enumerate(candidates):
            with st.container():
                confidence = candidate["confidence"]
                conf_color = "green" if confidence >= 0.7 else ("orange" if confidence >= 0.4 else "red")
                type_label = "报销抵扣" if candidate["type"] == "reimbursement" else "内部转账"
                type_icon = "💼" if candidate["type"] == "reimbursement" else "🔄"

                same_direction = candidate.get("same_direction", False)

                warnings = []
                if candidate["cross_month"]:
                    warnings.append("⚠️ 跨月匹配 - 建议确认是否为同一笔业务")
                if not candidate["amount_match"]:
                    warnings.append(f"⚠️ 金额不一致 (差¥{candidate['amount_diff']})")
                if same_direction:
                    dir_label = "双收入" if candidate["items"][0]["transaction"]["type"] == "income" else "双支出"
                    warnings.append(f"⚠️ 同方向配对({dir_label}) - 请确认角色分配是否正确")

                st.markdown(f"""
                <div style="border: 1px solid #e0e0e0; border-radius: 8px; padding: 12px; margin: 8px 0;">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <div>
                            <span style="font-size: 1.1em; font-weight: bold;">{type_icon} {type_label}</span>
                            {"<span style='margin-left: 8px; background: #fff3cd; color: #856404; padding: 2px 8px; border-radius: 4px; font-size: 0.8em;'>同方向配对</span>" if same_direction else ""}
                            <span style="margin-left: 12px; color: {conf_color}; font-weight: bold;">
                                置信度: {confidence:.2f}
                            </span>
                        </div>
                        <div style="color: #666; font-size: 0.9em;">
                            {' '.join(candidate['reasons'])}
                        </div>
                    </div>
                    {''.join(f'<div style="color: #ff9800; margin-top: 4px;">{w}</div>' for w in warnings)}
                </div>
                """, unsafe_allow_html=True)

                items_df = pd.DataFrame([
                    {
                        "日期": item["transaction"]["date"],
                        "描述": item["transaction"]["description"],
                        "类型": "收入" if item["transaction"]["type"] == "income" else "支出",
                        "金额": format_currency(
                            item["transaction"]["amount"] if item["transaction"]["type"] == "income"
                            else -item["transaction"]["amount"]
                        ),
                        "分类": item["transaction"]["category"],
                        "角色": "转出/支出" if item["role"] in ("source", "expense") else "转入/退款"
                    }
                    for item in candidate["items"]
                ])
                st.dataframe(items_df, use_container_width=True, hide_index=True)

                col_confirm, col_skip, col_edit = st.columns([2, 1, 2])

                with col_confirm:
                    if st.button(f"✓ 确认归并", key=f"confirm_candidate_{idx}", type="primary"):
                        items = [
                            (item["transaction"]["id"], item["role"])
                            for item in candidate["items"]
                        ]
                        desc = f"{type_label}: {candidate['items'][0]['transaction']['description']} ↔ {candidate['items'][1]['transaction']['description']}"
                        create_internal_transfer(candidate["type"], desc, items)
                        st.success("已确认归并！")
                        st.rerun()

                with col_edit:
                    with st.expander("✏️ 编辑后确认"):
                        edit_type = st.selectbox(
                            "归并类型",
                            ["内部转账", "报销抵扣"],
                            index=0 if candidate["type"] == "transfer" else 1,
                            key=f"edit_type_{idx}"
                        )
                        edit_desc = st.text_input(
                            "备注描述",
                            value=f"{type_label}: {candidate['items'][0]['transaction']['description'][:20]}...",
                            key=f"edit_desc_{idx}"
                        )
                        if st.button("保存并确认", key=f"save_edit_candidate_{idx}"):
                            t_type = "reimbursement" if edit_type == "报销抵扣" else "transfer"
                            items = [
                                (item["transaction"]["id"], item["role"])
                                for item in candidate["items"]
                            ]
                            create_internal_transfer(t_type, edit_desc, items)
                            st.success("已确认归并！")
                            st.rerun()

                st.divider()

    if unmatched:
        st.markdown(f"##### 🔔 未匹配的内部转账关键词流水 ({len(unmatched)} 条)")
        st.info("这些流水包含内部转账关键词，但未找到匹配的对应流水，请手动处理")

        unmatched_df = pd.DataFrame([
            {
                "ID": u["transaction"]["id"],
                "日期": u["transaction"]["date"],
                "描述": u["transaction"]["description"],
                "类型": "收入" if u["transaction"]["type"] == "income" else "支出",
                "金额": format_currency(
                    u["transaction"]["amount"] if u["transaction"]["type"] == "income"
                    else -u["transaction"]["amount"]
                ),
                "匹配关键词": "、".join(u["matched_keywords"])
            }
            for u in unmatched
        ])
        st.dataframe(unmatched_df, use_container_width=True, hide_index=True)


def render_confirmed_tab():
    st.subheader("✅ 已确认的归并记录")

    transfers = get_internal_transfers()

    if not transfers:
        st.info("暂无已确认的归并记录")
        return

    transfer_count = len([t for t in transfers if t["transfer_type"] == "transfer"])
    reimburse_count = len([t for t in transfers if t["transfer_type"] == "reimbursement"])

    same_direction_count = 0
    for t in transfers:
        if t["items"]:
            types = {item["type"] for item in t["items"]}
            if len(types) == 1:
                same_direction_count += 1

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("归并总数", len(transfers))
    col2.metric("内部转账", transfer_count)
    col3.metric("报销抵扣", reimburse_count)
    col4.metric("同方向配对", same_direction_count)

    st.divider()

    for t in transfers:
        type_icon = "💼" if t["transfer_type"] == "reimbursement" else "🔄"
        type_label = "报销抵扣" if t["transfer_type"] == "reimbursement" else "内部转账"

        if t["items"]:
            types = {item["type"] for item in t["items"]}
            is_same_direction = len(types) == 1
            if is_same_direction:
                only_type = list(types)[0]
                dir_label = "双收入" if only_type == "income" else "双支出"
            else:
                is_same_direction = False
                dir_label = ""
        else:
            is_same_direction = False
            dir_label = ""

        expand_title = f"{type_icon} {type_label} - {t['description']}"
        if is_same_direction:
            expand_title += f" ⚠️[{dir_label}]"
        expand_title += f" (金额: {format_currency(t['total_amount'])})"

        with st.expander(expand_title, expanded=False):
            st.write(f"**归并ID:** #{t['id']}")
            st.write(f"**创建时间:** {t['created_at']}")
            if is_same_direction:
                st.warning(f"⚠️ 此归并为同方向配对 ({dir_label})，适用于微信提现、信用卡重复扣款等场景")

            items_df = pd.DataFrame([
                {
                    "ID": item["transaction_id"],
                    "日期": item["date"],
                    "描述": item["txn_description"],
                    "类型": "收入" if item["type"] == "income" else "支出",
                    "金额": format_currency(
                        item["amount"] if item["type"] == "income" else -item["amount"]
                    ),
                    "分类": item["category"],
                    "角色": "转出/支出" if item["role"] in ("source", "expense") else "转入/退款"
                }
                for item in t["items"]
            ])
            st.dataframe(items_df, use_container_width=True, hide_index=True)

            col_edit, col_delete = st.columns(2)

            with col_edit:
                with st.expander("✏️ 编辑归并"):
                    new_desc = st.text_input(
                        "修改描述",
                        value=t["description"],
                        key=f"edit_transfer_desc_{t['id']}"
                    )
                    new_type = st.selectbox(
                        "修改类型",
                        ["内部转账", "报销抵扣"],
                        index=0 if t["transfer_type"] == "transfer" else 1,
                        key=f"edit_transfer_type_{t['id']}"
                    )
                    if st.button("保存修改", key=f"save_transfer_edit_{t['id']}"):
                        t_type = "reimbursement" if new_type == "报销抵扣" else "transfer"
                        items = [(item["transaction_id"], item["role"]) for item in t["items"]]
                        update_internal_transfer(t["id"], description=new_desc, items=items)
                        st.success("修改已保存")
                        st.rerun()

            with col_delete:
                if st.button("🗑️ 拆开归并 (恢复原流水)", key=f"delete_transfer_{t['id']}"):
                    delete_internal_transfer(t["id"])
                    st.success("已拆开归并，流水恢复为独立记录")
                    st.rerun()


def render_manual_create_tab():
    st.subheader("✋ 手动创建归并")

    st.markdown("""
    <div class="info-card">
    💡 **使用说明：**
    - 支持选择任意 2 条或多条流水（包括同方向的双收入/双支出）
    - 同方向配对常见场景：微信零钱提现(两条收入)、信用卡还款+自动扣款(两条支出)
    - 可手动调整每条流水的角色（转出/转入/支出/退款）
    </div>
    """, unsafe_allow_html=True)

    all_txns = get_transactions()
    matched_ids = get_internal_transfer_txn_ids()
    available_txns = [t for t in all_txns if t["id"] not in matched_ids]

    if len(available_txns) < 2:
        st.warning("可用流水不足 2 条，无法手动创建归并")
        return

    txn_options = []
    for t in available_txns:
        sign = "+" if t["type"] == "income" else "-"
        already_tag = ""
        if any(kw in t["description"] for kw in INTERNAL_KEYWORDS_ALL):
            already_tag = " 🔑"
        txn_options.append(f"#{t['id']} | {t['date']} | {t['description']}{already_tag} | {sign}{format_currency(t['amount'])}")

    selected = st.multiselect(
        "选择要归并的流水 (支持双收入/双支出等同方向配对)",
        options=list(range(len(available_txns))),
        format_func=lambda i: txn_options[i],
        max_selections=10
    )

    if selected:
        selected_txns = [available_txns[i] for i in selected]
        incomes = [t for t in selected_txns if t["type"] == "income"]
        expenses = [t for t in selected_txns if t["type"] == "expense"]

        same_direction = len(incomes) == 0 or len(expenses) == 0
        if same_direction:
            dir_label = "双收入" if len(incomes) > 0 else "双支出"
            st.warning(f"⚠️ 检测到同方向配对({dir_label})，请确认角色分配是否正确")

        st.markdown(f"已选择 **{len(selected)}** 条流水:")
        st.markdown(f"- 收入: {len(incomes)} 条, 总计: {format_currency(sum(t['amount'] for t in incomes))}")
        st.markdown(f"- 支出: {len(expenses)} 条, 总计: {format_currency(sum(t['amount'] for t in expenses))}")

        if len(incomes) == 0 and len(expenses) >= 2:
            st.info("提示：双支出常见于 信用卡还款+自动扣款 等场景")
        if len(expenses) == 0 and len(incomes) >= 2:
            st.info("提示：双收入常见于 微信零钱提现到银行卡 等场景")

        st.markdown("##### 调整每条流水的角色")

        role_assignments = {}
        for i, t in enumerate(selected_txns):
            col1, col2, col3 = st.columns([1, 2, 2])
            with col1:
                sign = "+" if t["type"] == "income" else "-"
                st.write(f"#{t['id']}")
            with col2:
                st.write(f"{t['date']} | {sign}{format_currency(t['amount'])}")
                st.caption(t["description"])

            default_role = None
            if t["type"] == "expense":
                default_role = "source"
            else:
                default_role = "destination"

            role_options = [
                ("转出 (source)", "source"),
                ("转入 (destination)", "destination"),
                ("支出 (expense)", "expense"),
                ("退款 (refund)", "refund"),
            ]

            with col3:
                selected_role = st.selectbox(
                    f"角色 - 流水#{t['id']}",
                    options=[r[1] for r in role_options],
                    format_func=lambda x: [r[0] for r in role_options if r[1] == x][0],
                    index=[r[1] for r in role_options].index(default_role) if default_role in [r[1] for r in role_options] else 0,
                    key=f"manual_role_{t['id']}"
                )
                role_assignments[t["id"]] = selected_role

        preview_df = pd.DataFrame([
            {
                "ID": t["id"],
                "日期": t["date"],
                "描述": t["description"],
                "类型": "收入" if t["type"] == "income" else "支出",
                "金额": format_currency(t["amount"] if t["type"] == "income" else -t["amount"]),
                "分类": t["category"],
                "分配角色": [r[0] for r in role_options if r[1] == role_assignments[t["id"]]][0]
            }
            for t in selected_txns
        ])
        st.dataframe(preview_df, use_container_width=True, hide_index=True)

        st.divider()

        col_type, col_desc = st.columns([1, 2])
        with col_type:
            manual_type = st.selectbox("归并类型", ["内部转账", "报销抵扣"], key="manual_create_type")
        with col_desc:
            manual_desc = st.text_input("备注描述", value=f"手动{manual_type}", key="manual_create_desc")

        has_source = any(r in ("source", "expense") for r in role_assignments.values())
        has_dest = any(r in ("destination", "refund") for r in role_assignments.values())

        if not has_source or not has_dest:
            st.error("⚠️ 请至少分配一个'转出/支出'角色和一个'转入/退款'角色")

        if st.button("✅ 创建归并", type="primary", disabled=(not has_source or not has_dest)):
            items = []
            t_type = "reimbursement" if manual_type == "报销抵扣" else "transfer"
            for t in selected_txns:
                items.append((t["id"], role_assignments[t["id"]]))

            create_internal_transfer(t_type, manual_desc, items)
            st.success(f"已创建{manual_type}归并！包含 {len(items)} 条流水")
            st.rerun()


def main():
    st.sidebar.title("💰 个人预算复盘")
    st.sidebar.markdown("---")

    page = st.sidebar.radio(
        "导航菜单",
        ["📥 流水导入", "⚙️ 分类规则", "� 内部转账", "�� 月度预算", "📈 趋势分析"],
        index=0
    )

    st.sidebar.markdown("---")
    st.sidebar.info(
        "💡 **使用说明：**\n"
        "1. 在「流水导入」上传银行CSV\n"
        "2. 在「分类规则」管理匹配规则\n"
        "3. 在「内部转账」归并内部流水\n"
        "4. 在「月度预算」设置并查看预算\n"
        "5. 在「趋势分析」查看历史走势"
    )

    total_txns = len(get_transactions())
    st.sidebar.metric("📊 流水总数", total_txns)

    transfer_ids = get_internal_transfer_txn_ids()
    st.sidebar.metric("🔄 已归并流水", len(transfer_ids))

    if page == "📥 流水导入":
        render_import_page()
    elif page == "⚙️ 分类规则":
        render_rules_page()
    elif page == "🔄 内部转账":
        render_internal_transfer_page()
    elif page == "📋 月度预算":
        render_budget_page()
    elif page == "📈 趋势分析":
        render_trend_page()


if __name__ == "__main__":
    main()
