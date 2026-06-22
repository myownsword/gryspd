import sqlite3
import os
from datetime import datetime
from contextlib import contextmanager
from typing import List, Dict, Optional, Tuple

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "budget.db")


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                description TEXT NOT NULL,
                amount REAL NOT NULL,
                type TEXT NOT NULL CHECK(type IN ('income', 'expense')),
                category TEXT NOT NULL,
                rule_id INTEGER,
                created_at TEXT NOT NULL,
                UNIQUE(date, description, amount)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                type TEXT NOT NULL CHECK(type IN ('income', 'expense'))
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL,
                category TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 0,
                type TEXT NOT NULL CHECK(type IN ('income', 'expense')),
                created_at TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS budgets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                amount REAL NOT NULL,
                month TEXT NOT NULL,
                UNIQUE(category, month)
            )
        """)

        default_categories = [
            ("工资", "income"), ("奖金", "income"), ("投资收益", "income"),
            ("其他收入", "income"),
            ("餐饮", "expense"), ("交通", "expense"), ("购物", "expense"),
            ("娱乐", "expense"), ("居住", "expense"), ("医疗", "expense"),
            ("教育", "expense"), ("通讯", "expense"), ("其他支出", "expense")
        ]

        for name, ctype in default_categories:
            cursor.execute(
                "INSERT OR IGNORE INTO categories (name, type) VALUES (?, ?)",
                (name, ctype)
            )

        default_rules = [
            ("工资", "工资", "income", 100),
            ("薪资", "工资", "income", 99),
            ("奖金", "奖金", "income", 90),
            ("美团", "餐饮", "expense", 80),
            ("饿了么", "餐饮", "expense", 79),
            ("餐饮", "餐饮", "expense", 78),
            ("餐厅", "餐饮", "expense", 77),
            ("地铁", "交通", "expense", 70),
            ("公交", "交通", "expense", 69),
            ("滴滴", "交通", "expense", 68),
            ("加油", "交通", "expense", 67),
            ("淘宝", "购物", "expense", 60),
            ("京东", "购物", "expense", 59),
            ("天猫", "购物", "expense", 58),
            ("拼多多", "购物", "expense", 57),
            ("电影", "娱乐", "expense", 50),
            ("游戏", "娱乐", "expense", 49),
            ("房租", "居住", "expense", 40),
            ("水电", "居住", "expense", 39),
            ("物业", "居住", "expense", 38),
            ("医院", "医疗", "expense", 30),
            ("药店", "医疗", "expense", 29),
            ("学费", "教育", "expense", 20),
            ("话费", "通讯", "expense", 10),
        ]

        now = datetime.now().isoformat()
        for keyword, category, rtype, priority in default_rules:
            cursor.execute(
                "INSERT OR IGNORE INTO rules (keyword, category, priority, type, created_at) VALUES (?, ?, ?, ?, ?)",
                (keyword, category, priority, rtype, now)
            )


def add_transaction(date: str, description: str, amount: float,
                    txn_type: str, category: str, rule_id: Optional[int] = None) -> int:
    with get_db() as conn:
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        cursor.execute("""
            INSERT OR IGNORE INTO transactions
            (date, description, amount, type, category, rule_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (date, description, amount, txn_type, category, rule_id, now))
        return cursor.lastrowid


def bulk_insert_transactions(rows: List[Tuple]) -> Tuple[int, int]:
    with get_db() as conn:
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        inserted = 0
        duplicates = 0
        for row in rows:
            date, description, amount, txn_type, category, rule_id = row
            try:
                cursor.execute("""
                    INSERT INTO transactions
                    (date, description, amount, type, category, rule_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (date, description, amount, txn_type, category, rule_id, now))
                inserted += 1
            except sqlite3.IntegrityError:
                duplicates += 1
        return inserted, duplicates


def get_transactions(start_date: Optional[str] = None,
                     end_date: Optional[str] = None,
                     category: Optional[str] = None) -> List[Dict]:
    with get_db() as conn:
        cursor = conn.cursor()
        query = "SELECT * FROM transactions WHERE 1=1"
        params = []
        if start_date:
            query += " AND date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND date <= ?"
            params.append(end_date)
        if category:
            query += " AND category = ?"
            params.append(category)
        query += " ORDER BY date DESC"
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]


def get_transaction_by_id(txn_id: int) -> Optional[Dict]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM transactions WHERE id = ?", (txn_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def update_transaction_category(txn_id: int, category: str, rule_id: Optional[int] = None):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE transactions SET category = ?, rule_id = ? WHERE id = ?",
            (category, rule_id, txn_id)
        )


def delete_transaction(txn_id: int):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM transactions WHERE id = ?", (txn_id,))


def get_categories(txn_type: Optional[str] = None) -> List[Dict]:
    with get_db() as conn:
        cursor = conn.cursor()
        if txn_type:
            cursor.execute(
                "SELECT * FROM categories WHERE type = ? ORDER BY name",
                (txn_type,)
            )
        else:
            cursor.execute("SELECT * FROM categories ORDER BY type, name")
        return [dict(row) for row in cursor.fetchall()]


def add_category(name: str, ctype: str):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO categories (name, type) VALUES (?, ?)",
            (name, ctype)
        )


def get_rules(rtype: Optional[str] = None) -> List[Dict]:
    with get_db() as conn:
        cursor = conn.cursor()
        if rtype:
            cursor.execute(
                "SELECT * FROM rules WHERE type = ? ORDER BY priority DESC, id ASC",
                (rtype,)
            )
        else:
            cursor.execute("SELECT * FROM rules ORDER BY type, priority DESC, id ASC")
        return [dict(row) for row in cursor.fetchall()]


def add_rule(keyword: str, category: str, rtype: str, priority: int = 0) -> int:
    with get_db() as conn:
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        cursor.execute("""
            INSERT INTO rules (keyword, category, priority, type, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (keyword, category, priority, rtype, now))
        return cursor.lastrowid


def update_rule(rule_id: int, keyword: str, category: str, priority: int):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE rules SET keyword = ?, category = ?, priority = ?
            WHERE id = ?
        """, (keyword, category, priority, rule_id))


def delete_rule(rule_id: int):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM rules WHERE id = ?", (rule_id,))


def update_rule_priority(rule_id: int, new_priority: int):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE rules SET priority = ? WHERE id = ?",
            (new_priority, rule_id)
        )


def reorder_rules(rtype: str, ordered_ids: List[int]):
    with get_db() as conn:
        cursor = conn.cursor()
        max_priority = len(ordered_ids)
        for idx, rule_id in enumerate(ordered_ids):
            cursor.execute(
                "UPDATE rules SET priority = ? WHERE id = ? AND type = ?",
                (max_priority - idx, rule_id, rtype)
            )


def get_budgets(month: Optional[str] = None) -> List[Dict]:
    with get_db() as conn:
        cursor = conn.cursor()
        if month:
            cursor.execute(
                "SELECT * FROM budgets WHERE month = ? ORDER BY category",
                (month,)
            )
        else:
            cursor.execute("SELECT * FROM budgets ORDER BY month, category")
        return [dict(row) for row in cursor.fetchall()]


def set_budget(category: str, amount: float, month: str):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO budgets (category, amount, month)
            VALUES (?, ?, ?)
            ON CONFLICT(category, month) DO UPDATE SET amount = excluded.amount
        """, (category, amount, month))


def delete_budget(budget_id: int):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM budgets WHERE id = ?", (budget_id,))


def get_monthly_summary(month: str) -> Dict:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT category, type, SUM(amount) as total
            FROM transactions
            WHERE strftime('%Y-%m', date) = ?
            GROUP BY category, type
        """, (month,))
        rows = cursor.fetchall()

        income_total = 0.0
        expense_total = 0.0
        by_category = {}

        for row in rows:
            cat, txn_type, total = dict(row).values()
            by_category[cat] = {"type": txn_type, "total": total}
            if txn_type == "income":
                income_total += total
            else:
                expense_total += total

        return {
            "income_total": income_total,
            "expense_total": expense_total,
            "balance": income_total - expense_total,
            "by_category": by_category
        }


def get_trend_data(start_month: str, end_month: str) -> List[Dict]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                strftime('%Y-%m', date) as month,
                category,
                type,
                SUM(amount) as total
            FROM transactions
            WHERE strftime('%Y-%m', date) BETWEEN ? AND ?
            GROUP BY month, category, type
            ORDER BY month, category
        """, (start_month, end_month))
        return [dict(row) for row in cursor.fetchall()]
