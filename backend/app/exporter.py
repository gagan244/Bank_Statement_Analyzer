"""Export transaction data to Excel and CSV, plus a category breakdown chart."""

from __future__ import annotations

import os
from typing import Dict, Optional

import matplotlib

matplotlib.use("Agg")  # non-interactive backend -- required to save PNGs with no display
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import pandas as pd


REQUIRED_COLUMNS = ["date", "description", "debit_amount", "credit_amount", "balance", "category", "classification_method"]

CATEGORY_SUMMARY_COLUMNS = ["category", "transaction_count", "total_debit", "total_credit", "total_amount"]


def normalize_output_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure output data has the expected columns and sanitized values."""
    output = df.copy()
    for col in REQUIRED_COLUMNS:
        if col not in output.columns:
            output[col] = None

    output["description"] = output["description"].fillna("UNKNOWN").astype(str)
    output["category"] = output["category"].fillna("Uncategorized").astype(str)
    output["classification_method"] = output["classification_method"].fillna("rule").astype(str)

    numeric_columns = ["debit_amount", "credit_amount", "balance"]
    for col in numeric_columns:
        output[col] = pd.to_numeric(output[col], errors="coerce").fillna(0.0)

    output["date"] = pd.to_datetime(output["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    output = output[REQUIRED_COLUMNS]
    return output.drop_duplicates().reset_index(drop=True)


def build_category_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate classified transactions into a per-category summary: transaction
    count, total debit, total credit, and a combined total_amount (debit + credit --
    the total monetary volume in that category, since a category like "Transfer" can
    legitimately contain both debit and credit rows). Sorted by total_amount
    descending so the chart and sheet both read largest-first.
    """
    if df.empty:
        return pd.DataFrame(columns=CATEGORY_SUMMARY_COLUMNS)

    working = df.copy()
    working["category"] = working["category"].fillna("Uncategorized").astype(str)
    working["debit_amount"] = pd.to_numeric(working.get("debit_amount"), errors="coerce").fillna(0.0)
    working["credit_amount"] = pd.to_numeric(working.get("credit_amount"), errors="coerce").fillna(0.0)

    summary = (
        working.groupby("category")
        .agg(transaction_count=("category", "count"), total_debit=("debit_amount", "sum"), total_credit=("credit_amount", "sum"))
        .reset_index()
    )
    summary["total_amount"] = summary["total_debit"] + summary["total_credit"]
    summary = summary.sort_values("total_amount", ascending=False).reset_index(drop=True)
    for col in ("total_debit", "total_credit", "total_amount"):
        summary[col] = summary[col].round(2)
    return summary[CATEGORY_SUMMARY_COLUMNS]


def generate_category_chart(df: pd.DataFrame, output_path: str) -> Optional[str]:
    """Render a two-panel bar chart (transaction count + total amount, per category)
    and save it as a PNG. Returns the saved path, or None if there's no data to chart
    at all (an empty dataframe) -- callers should treat that as "no chart produced,"
    not an error.

    A bar chart (not a pie chart) is used deliberately: pie charts get hard to read
    once there are more than ~4-5 slices or when a couple of categories dominate --
    exactly the shape a real statement's category mix tends to have. Two panels (count
    vs. amount) avoid conflating two different scales (a transaction count and a
    currency total) on one axis.
    """
    summary = build_category_summary(df)
    if summary.empty:
        return None

    categories = summary["category"].tolist()
    counts = summary["transaction_count"].tolist()
    amounts = summary["total_amount"].tolist()

    # Scale figure width to the number of categories: a single bar in a wide, mostly
    # empty figure looks broken; many bars crushed into a narrow one are unreadable.
    fig_width = max(6.0, min(14.0, 1.6 * len(categories) + 3.0))
    fig, (count_ax, amount_ax) = plt.subplots(1, 2, figsize=(fig_width, 5.5))

    palette = plt.get_cmap("tab10").colors
    colors = [palette[i % len(palette)] for i in range(len(categories))]

    count_bars = count_ax.bar(categories, counts, color=colors)
    count_ax.set_title("Transaction Count by Category")
    count_ax.set_ylabel("Number of transactions")
    count_ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    count_ax.bar_label(count_bars, labels=[str(c) for c in counts], padding=3, fontsize=9)
    count_ax.set_ylim(0, max(counts) * 1.2 if counts and max(counts) > 0 else 1)

    amount_bars = amount_ax.bar(categories, amounts, color=colors)
    amount_ax.set_title("Total Amount by Category")
    amount_ax.set_ylabel("Amount (debit + credit)")
    amount_ax.bar_label(amount_bars, labels=[f"{a:,.0f}" for a in amounts], padding=3, fontsize=9)
    amount_ax.set_ylim(0, max(amounts) * 1.2 if amounts and max(amounts) > 0 else 1)

    for ax in (count_ax, amount_ax):
        ax.tick_params(axis="x", rotation=30)
        for label in ax.get_xticklabels():
            label.set_ha("right")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    fig.suptitle("Category Breakdown", fontsize=14, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.94))

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path


def _account_details_frame(account_details: Optional[Dict[str, Optional[str]]]) -> pd.DataFrame:
    details = account_details or {}
    labels = {
        "account_holder_name": "Account Holder Name",
        "account_number": "Account Number",
        "ifsc_code": "IFSC Code",
    }
    rows = [
        {"Field": label, "Value": details.get(key) or "Not detected"}
        for key, label in labels.items()
    ]
    return pd.DataFrame(rows)


def export_transactions(
    df: pd.DataFrame,
    excel_path: str,
    csv_path: Optional[str] = None,
    account_details: Optional[Dict[str, Optional[str]]] = None,
) -> Dict[str, str]:
    """Export the classified transactions to Excel and CSV, plus a category chart PNG.

    Excel sheets: "Account Details", "Transactions", "Category Summary".

    CSV holds the transaction rows only (no concept of sheets); when `account_details`
    is provided, a sibling `<csv_path>_account_details.csv` is also written.

    Returns a dict of the paths actually written: always has "excel" and "csv"; has
    "account_details_csv" when account_details was provided, and "chart" when there
    was at least one transaction to chart (an empty result has no "chart" key rather
    than pointing at a nonexistent or blank file).
    """
    output_df = normalize_output_dataframe(df)

    excel_dir = os.path.dirname(excel_path) or "."
    os.makedirs(excel_dir, exist_ok=True)

    account_df = _account_details_frame(account_details)
    category_summary_df = build_category_summary(output_df)

    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        account_df.to_excel(writer, sheet_name="Account Details", index=False)
        output_df.to_excel(writer, sheet_name="Transactions", index=False)
        category_summary_df.to_excel(writer, sheet_name="Category Summary", index=False)

    csv_target = csv_path or os.path.splitext(excel_path)[0] + ".csv"
    output_df.to_csv(csv_target, index=False)

    result: Dict[str, str] = {"excel": excel_path, "csv": csv_target}

    if account_details:
        account_csv_target = os.path.splitext(csv_target)[0] + "_account_details.csv"
        account_df.to_csv(account_csv_target, index=False)
        result["account_details_csv"] = account_csv_target

    chart_path = os.path.join(excel_dir, "category_breakdown.png")
    generated_chart = generate_category_chart(output_df, chart_path)
    if generated_chart:
        result["chart"] = generated_chart

    return result
