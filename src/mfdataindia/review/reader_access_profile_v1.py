"""Frozen present-column evidence, NOT permission or runtime authority."""
import json


def profile():
    """Return a fresh value; no caller may expand the frozen profile."""
    return json.loads(r'''
{
  "all_rows_all_role_consumers": true,
  "candidate_sha256": "c25bd271368b4d40d218e3560f49ace5c5ea53f943f5a39a06738b4ae979f85a",
  "database": "mfdataindia",
  "format": "reader-explicit-columns-v1",
  "lifetime": "PERSISTENT_PRESENT_COLUMNS_ONLY",
  "objects": [
    {
      "all_physical_columns": [
        {
          "attnum": 1,
          "collation_oid": 0,
          "dropped": false,
          "name": "amfi_scheme_code",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 23
        },
        {
          "attnum": 2,
          "collation_oid": 0,
          "dropped": false,
          "name": "plan_id",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 23
        },
        {
          "attnum": 3,
          "collation_oid": 100,
          "dropped": false,
          "name": "sub_plan_id",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 4,
          "collation_oid": 100,
          "dropped": false,
          "name": "plan_name",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 5,
          "collation_oid": 100,
          "dropped": false,
          "name": "rta_scheme_code",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 6,
          "collation_oid": 100,
          "dropped": false,
          "name": "asset_class",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 7,
          "collation_oid": 100,
          "dropped": false,
          "name": "asset_class_code",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 8,
          "collation_oid": 100,
          "dropped": false,
          "name": "sub_asset_class",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 9,
          "collation_oid": 100,
          "dropped": false,
          "name": "sub_asset_class_code",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 10,
          "collation_oid": 100,
          "dropped": false,
          "name": "sebi_category_name",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 11,
          "collation_oid": 100,
          "dropped": false,
          "name": "taxability",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 12,
          "collation_oid": 0,
          "dropped": false,
          "name": "openended",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 16
        },
        {
          "attnum": 13,
          "collation_oid": 0,
          "dropped": false,
          "name": "aum",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": 1310730,
          "type_oid": 1700
        },
        {
          "attnum": 14,
          "collation_oid": 0,
          "dropped": false,
          "name": "expense_ratio",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": 589832,
          "type_oid": 1700
        },
        {
          "attnum": 15,
          "collation_oid": 0,
          "dropped": false,
          "name": "face_value",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": 1179656,
          "type_oid": 1700
        },
        {
          "attnum": 16,
          "collation_oid": 0,
          "dropped": false,
          "name": "inception_date",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1082
        },
        {
          "attnum": 17,
          "collation_oid": 100,
          "dropped": false,
          "name": "status",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 18,
          "collation_oid": 100,
          "dropped": false,
          "name": "transaction_status",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 19,
          "collation_oid": 0,
          "dropped": false,
          "name": "is_active_status",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 16
        },
        {
          "attnum": 20,
          "collation_oid": 0,
          "dropped": false,
          "name": "is_investable",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 16
        },
        {
          "attnum": 21,
          "collation_oid": 0,
          "dropped": false,
          "name": "is_purchase_allowed",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 16
        },
        {
          "attnum": 22,
          "collation_oid": 0,
          "dropped": false,
          "name": "is_withdrawal_allowed",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 16
        },
        {
          "attnum": 23,
          "collation_oid": 0,
          "dropped": false,
          "name": "is_sip_allowed",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 16
        },
        {
          "attnum": 24,
          "collation_oid": 0,
          "dropped": false,
          "name": "is_stp_allowed",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 16
        },
        {
          "attnum": 25,
          "collation_oid": 0,
          "dropped": false,
          "name": "is_swp_allowed",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 16
        },
        {
          "attnum": 26,
          "collation_oid": 0,
          "dropped": false,
          "name": "is_switch_in_allowed",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 16
        },
        {
          "attnum": 27,
          "collation_oid": 0,
          "dropped": false,
          "name": "is_switch_out_allowed",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 16
        },
        {
          "attnum": 28,
          "collation_oid": 0,
          "dropped": false,
          "name": "is_nfo",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 16
        },
        {
          "attnum": 29,
          "collation_oid": 0,
          "dropped": false,
          "name": "min_initial_investment_amount",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": 1179654,
          "type_oid": 1700
        },
        {
          "attnum": 30,
          "collation_oid": 0,
          "dropped": false,
          "name": "min_subsequent_investment_amount",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": 1179654,
          "type_oid": 1700
        },
        {
          "attnum": 31,
          "collation_oid": 0,
          "dropped": false,
          "name": "min_withdrawal_amount",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": 1179654,
          "type_oid": 1700
        },
        {
          "attnum": 32,
          "collation_oid": 0,
          "dropped": false,
          "name": "min_investment_multiples",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": 1179654,
          "type_oid": 1700
        },
        {
          "attnum": 33,
          "collation_oid": 0,
          "dropped": false,
          "name": "returns_as_on_date",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1082
        },
        {
          "attnum": 34,
          "collation_oid": 0,
          "dropped": false,
          "name": "return_1day",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1700
        },
        {
          "attnum": 35,
          "collation_oid": 0,
          "dropped": false,
          "name": "return_3month",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1700
        },
        {
          "attnum": 36,
          "collation_oid": 0,
          "dropped": false,
          "name": "return_6month",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1700
        },
        {
          "attnum": 37,
          "collation_oid": 0,
          "dropped": false,
          "name": "return_1year",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1700
        },
        {
          "attnum": 38,
          "collation_oid": 0,
          "dropped": false,
          "name": "return_2year",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1700
        },
        {
          "attnum": 39,
          "collation_oid": 0,
          "dropped": false,
          "name": "return_3year",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1700
        },
        {
          "attnum": 40,
          "collation_oid": 0,
          "dropped": false,
          "name": "return_4year",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1700
        },
        {
          "attnum": 41,
          "collation_oid": 0,
          "dropped": false,
          "name": "return_5year",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1700
        },
        {
          "attnum": 42,
          "collation_oid": 0,
          "dropped": false,
          "name": "return_7year",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1700
        },
        {
          "attnum": 43,
          "collation_oid": 0,
          "dropped": false,
          "name": "return_10year",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1700
        },
        {
          "attnum": 44,
          "collation_oid": 0,
          "dropped": false,
          "name": "return_since_launch",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1700
        },
        {
          "attnum": 45,
          "collation_oid": 0,
          "dropped": false,
          "name": "source_nav",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": 1179656,
          "type_oid": 1700
        },
        {
          "attnum": 46,
          "collation_oid": 0,
          "dropped": false,
          "name": "source_nav_date",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1082
        },
        {
          "attnum": 47,
          "collation_oid": 0,
          "dropped": false,
          "name": "composition",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 3802
        },
        {
          "attnum": 48,
          "collation_oid": 0,
          "dropped": false,
          "name": "sectorwise_holding",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 3802
        },
        {
          "attnum": 49,
          "collation_oid": 0,
          "dropped": false,
          "name": "asset_holding",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 3802
        },
        {
          "attnum": 50,
          "collation_oid": 0,
          "dropped": false,
          "name": "holdings_maturity",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 3802
        },
        {
          "attnum": 51,
          "collation_oid": 0,
          "dropped": false,
          "name": "exit_load",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 3802
        },
        {
          "attnum": 52,
          "collation_oid": 0,
          "dropped": false,
          "name": "sip",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 3802
        },
        {
          "attnum": 53,
          "collation_oid": 0,
          "dropped": false,
          "name": "stp",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 3802
        },
        {
          "attnum": 54,
          "collation_oid": 0,
          "dropped": false,
          "name": "swp",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 3802
        },
        {
          "attnum": 55,
          "collation_oid": 0,
          "dropped": false,
          "name": "stats_variables",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 3802
        },
        {
          "attnum": 56,
          "collation_oid": 0,
          "dropped": false,
          "name": "category_return",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 3802
        },
        {
          "attnum": 57,
          "collation_oid": 0,
          "dropped": false,
          "name": "fund_manager",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 3802
        },
        {
          "attnum": 58,
          "collation_oid": 100,
          "dropped": false,
          "name": "source",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 59,
          "collation_oid": 0,
          "dropped": false,
          "name": "source_updated_at",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1184
        },
        {
          "attnum": 60,
          "collation_oid": 0,
          "dropped": false,
          "name": "fetched_at",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1184
        },
        {
          "attnum": 61,
          "collation_oid": 100,
          "dropped": false,
          "name": "benchmark",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 62,
          "collation_oid": 100,
          "dropped": false,
          "name": "benchmark_name",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 63,
          "collation_oid": 100,
          "dropped": false,
          "name": "fund_manager_name",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 64,
          "collation_oid": 100,
          "dropped": false,
          "name": "risk_level",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 65,
          "collation_oid": 0,
          "dropped": false,
          "name": "base_expense_ratio",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": 589832,
          "type_oid": 1700
        },
        {
          "attnum": 66,
          "collation_oid": 100,
          "dropped": false,
          "name": "super_category",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 67,
          "collation_oid": 100,
          "dropped": false,
          "name": "sub_category",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 68,
          "collation_oid": 100,
          "dropped": false,
          "name": "registrar_agent",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 69,
          "collation_oid": 0,
          "dropped": false,
          "name": "launch_date",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1082
        },
        {
          "attnum": 70,
          "collation_oid": 0,
          "dropped": false,
          "name": "expense_ratio_history",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 3802
        },
        {
          "attnum": 71,
          "collation_oid": 0,
          "dropped": false,
          "name": "fund_manager_details",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 3802
        },
        {
          "attnum": 72,
          "collation_oid": 100,
          "dropped": false,
          "name": "crisil_rating",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 73,
          "collation_oid": 100,
          "dropped": false,
          "name": "sub_type",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 74,
          "collation_oid": 100,
          "dropped": false,
          "name": "exit_load_value",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 75,
          "collation_oid": 100,
          "dropped": false,
          "name": "lock_in_period",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 76,
          "collation_oid": 0,
          "dropped": false,
          "name": "portfolio_turnover",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": 589830,
          "type_oid": 1700
        },
        {
          "attnum": 77,
          "collation_oid": 0,
          "dropped": false,
          "name": "return_1week",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1700
        },
        {
          "attnum": 78,
          "collation_oid": 0,
          "dropped": false,
          "name": "return_1month",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1700
        },
        {
          "attnum": 79,
          "collation_oid": 0,
          "dropped": false,
          "name": "return_9month",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1700
        },
        {
          "attnum": 80,
          "collation_oid": 0,
          "dropped": false,
          "name": "sharpe_ratio",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1700
        },
        {
          "attnum": 81,
          "collation_oid": 0,
          "dropped": false,
          "name": "beta",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1700
        },
        {
          "attnum": 82,
          "collation_oid": 0,
          "dropped": false,
          "name": "std_deviation",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1700
        },
        {
          "attnum": 83,
          "collation_oid": 100,
          "dropped": false,
          "name": "risk_rating",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 84,
          "collation_oid": 0,
          "dropped": false,
          "name": "holdings_analysis",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 3802
        }
      ],
      "columns_in_actual_attnum_order": [
        {
          "attnum": 1,
          "collation_oid": 0,
          "dropped": false,
          "name": "amfi_scheme_code",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 23
        },
        {
          "attnum": 2,
          "collation_oid": 0,
          "dropped": false,
          "name": "plan_id",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 23
        },
        {
          "attnum": 3,
          "collation_oid": 100,
          "dropped": false,
          "name": "sub_plan_id",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 4,
          "collation_oid": 100,
          "dropped": false,
          "name": "plan_name",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 5,
          "collation_oid": 100,
          "dropped": false,
          "name": "rta_scheme_code",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 6,
          "collation_oid": 100,
          "dropped": false,
          "name": "asset_class",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 7,
          "collation_oid": 100,
          "dropped": false,
          "name": "asset_class_code",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 8,
          "collation_oid": 100,
          "dropped": false,
          "name": "sub_asset_class",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 9,
          "collation_oid": 100,
          "dropped": false,
          "name": "sub_asset_class_code",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 10,
          "collation_oid": 100,
          "dropped": false,
          "name": "sebi_category_name",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 11,
          "collation_oid": 100,
          "dropped": false,
          "name": "taxability",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 12,
          "collation_oid": 0,
          "dropped": false,
          "name": "openended",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 16
        },
        {
          "attnum": 13,
          "collation_oid": 0,
          "dropped": false,
          "name": "aum",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": 1310730,
          "type_oid": 1700
        },
        {
          "attnum": 14,
          "collation_oid": 0,
          "dropped": false,
          "name": "expense_ratio",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": 589832,
          "type_oid": 1700
        },
        {
          "attnum": 15,
          "collation_oid": 0,
          "dropped": false,
          "name": "face_value",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": 1179656,
          "type_oid": 1700
        },
        {
          "attnum": 16,
          "collation_oid": 0,
          "dropped": false,
          "name": "inception_date",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1082
        },
        {
          "attnum": 17,
          "collation_oid": 100,
          "dropped": false,
          "name": "status",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 18,
          "collation_oid": 100,
          "dropped": false,
          "name": "transaction_status",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 19,
          "collation_oid": 0,
          "dropped": false,
          "name": "is_active_status",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 16
        },
        {
          "attnum": 20,
          "collation_oid": 0,
          "dropped": false,
          "name": "is_investable",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 16
        },
        {
          "attnum": 21,
          "collation_oid": 0,
          "dropped": false,
          "name": "is_purchase_allowed",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 16
        },
        {
          "attnum": 22,
          "collation_oid": 0,
          "dropped": false,
          "name": "is_withdrawal_allowed",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 16
        },
        {
          "attnum": 23,
          "collation_oid": 0,
          "dropped": false,
          "name": "is_sip_allowed",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 16
        },
        {
          "attnum": 24,
          "collation_oid": 0,
          "dropped": false,
          "name": "is_stp_allowed",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 16
        },
        {
          "attnum": 25,
          "collation_oid": 0,
          "dropped": false,
          "name": "is_swp_allowed",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 16
        },
        {
          "attnum": 26,
          "collation_oid": 0,
          "dropped": false,
          "name": "is_switch_in_allowed",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 16
        },
        {
          "attnum": 27,
          "collation_oid": 0,
          "dropped": false,
          "name": "is_switch_out_allowed",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 16
        },
        {
          "attnum": 28,
          "collation_oid": 0,
          "dropped": false,
          "name": "is_nfo",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 16
        },
        {
          "attnum": 29,
          "collation_oid": 0,
          "dropped": false,
          "name": "min_initial_investment_amount",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": 1179654,
          "type_oid": 1700
        },
        {
          "attnum": 30,
          "collation_oid": 0,
          "dropped": false,
          "name": "min_subsequent_investment_amount",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": 1179654,
          "type_oid": 1700
        },
        {
          "attnum": 31,
          "collation_oid": 0,
          "dropped": false,
          "name": "min_withdrawal_amount",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": 1179654,
          "type_oid": 1700
        },
        {
          "attnum": 32,
          "collation_oid": 0,
          "dropped": false,
          "name": "min_investment_multiples",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": 1179654,
          "type_oid": 1700
        },
        {
          "attnum": 33,
          "collation_oid": 0,
          "dropped": false,
          "name": "returns_as_on_date",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1082
        },
        {
          "attnum": 34,
          "collation_oid": 0,
          "dropped": false,
          "name": "return_1day",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1700
        },
        {
          "attnum": 35,
          "collation_oid": 0,
          "dropped": false,
          "name": "return_3month",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1700
        },
        {
          "attnum": 36,
          "collation_oid": 0,
          "dropped": false,
          "name": "return_6month",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1700
        },
        {
          "attnum": 37,
          "collation_oid": 0,
          "dropped": false,
          "name": "return_1year",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1700
        },
        {
          "attnum": 38,
          "collation_oid": 0,
          "dropped": false,
          "name": "return_2year",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1700
        },
        {
          "attnum": 39,
          "collation_oid": 0,
          "dropped": false,
          "name": "return_3year",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1700
        },
        {
          "attnum": 40,
          "collation_oid": 0,
          "dropped": false,
          "name": "return_4year",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1700
        },
        {
          "attnum": 41,
          "collation_oid": 0,
          "dropped": false,
          "name": "return_5year",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1700
        },
        {
          "attnum": 42,
          "collation_oid": 0,
          "dropped": false,
          "name": "return_7year",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1700
        },
        {
          "attnum": 43,
          "collation_oid": 0,
          "dropped": false,
          "name": "return_10year",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1700
        },
        {
          "attnum": 44,
          "collation_oid": 0,
          "dropped": false,
          "name": "return_since_launch",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1700
        },
        {
          "attnum": 45,
          "collation_oid": 0,
          "dropped": false,
          "name": "source_nav",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": 1179656,
          "type_oid": 1700
        },
        {
          "attnum": 46,
          "collation_oid": 0,
          "dropped": false,
          "name": "source_nav_date",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1082
        },
        {
          "attnum": 47,
          "collation_oid": 0,
          "dropped": false,
          "name": "composition",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 3802
        },
        {
          "attnum": 48,
          "collation_oid": 0,
          "dropped": false,
          "name": "sectorwise_holding",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 3802
        },
        {
          "attnum": 49,
          "collation_oid": 0,
          "dropped": false,
          "name": "asset_holding",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 3802
        },
        {
          "attnum": 50,
          "collation_oid": 0,
          "dropped": false,
          "name": "holdings_maturity",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 3802
        },
        {
          "attnum": 51,
          "collation_oid": 0,
          "dropped": false,
          "name": "exit_load",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 3802
        },
        {
          "attnum": 52,
          "collation_oid": 0,
          "dropped": false,
          "name": "sip",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 3802
        },
        {
          "attnum": 53,
          "collation_oid": 0,
          "dropped": false,
          "name": "stp",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 3802
        },
        {
          "attnum": 54,
          "collation_oid": 0,
          "dropped": false,
          "name": "swp",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 3802
        },
        {
          "attnum": 55,
          "collation_oid": 0,
          "dropped": false,
          "name": "stats_variables",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 3802
        },
        {
          "attnum": 56,
          "collation_oid": 0,
          "dropped": false,
          "name": "category_return",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 3802
        },
        {
          "attnum": 57,
          "collation_oid": 0,
          "dropped": false,
          "name": "fund_manager",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 3802
        },
        {
          "attnum": 58,
          "collation_oid": 100,
          "dropped": false,
          "name": "source",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 59,
          "collation_oid": 0,
          "dropped": false,
          "name": "source_updated_at",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1184
        },
        {
          "attnum": 60,
          "collation_oid": 0,
          "dropped": false,
          "name": "fetched_at",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1184
        },
        {
          "attnum": 61,
          "collation_oid": 100,
          "dropped": false,
          "name": "benchmark",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 62,
          "collation_oid": 100,
          "dropped": false,
          "name": "benchmark_name",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 63,
          "collation_oid": 100,
          "dropped": false,
          "name": "fund_manager_name",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 64,
          "collation_oid": 100,
          "dropped": false,
          "name": "risk_level",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 65,
          "collation_oid": 0,
          "dropped": false,
          "name": "base_expense_ratio",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": 589832,
          "type_oid": 1700
        },
        {
          "attnum": 66,
          "collation_oid": 100,
          "dropped": false,
          "name": "super_category",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 67,
          "collation_oid": 100,
          "dropped": false,
          "name": "sub_category",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 68,
          "collation_oid": 100,
          "dropped": false,
          "name": "registrar_agent",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 69,
          "collation_oid": 0,
          "dropped": false,
          "name": "launch_date",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1082
        },
        {
          "attnum": 70,
          "collation_oid": 0,
          "dropped": false,
          "name": "expense_ratio_history",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 3802
        },
        {
          "attnum": 71,
          "collation_oid": 0,
          "dropped": false,
          "name": "fund_manager_details",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 3802
        },
        {
          "attnum": 72,
          "collation_oid": 100,
          "dropped": false,
          "name": "crisil_rating",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 73,
          "collation_oid": 100,
          "dropped": false,
          "name": "sub_type",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 74,
          "collation_oid": 100,
          "dropped": false,
          "name": "exit_load_value",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 75,
          "collation_oid": 100,
          "dropped": false,
          "name": "lock_in_period",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 76,
          "collation_oid": 0,
          "dropped": false,
          "name": "portfolio_turnover",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": 589830,
          "type_oid": 1700
        },
        {
          "attnum": 77,
          "collation_oid": 0,
          "dropped": false,
          "name": "return_1week",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1700
        },
        {
          "attnum": 78,
          "collation_oid": 0,
          "dropped": false,
          "name": "return_1month",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1700
        },
        {
          "attnum": 79,
          "collation_oid": 0,
          "dropped": false,
          "name": "return_9month",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1700
        },
        {
          "attnum": 80,
          "collation_oid": 0,
          "dropped": false,
          "name": "sharpe_ratio",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1700
        },
        {
          "attnum": 81,
          "collation_oid": 0,
          "dropped": false,
          "name": "beta",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1700
        },
        {
          "attnum": 82,
          "collation_oid": 0,
          "dropped": false,
          "name": "std_deviation",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 1700
        },
        {
          "attnum": 83,
          "collation_oid": 100,
          "dropped": false,
          "name": "risk_rating",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 84,
          "collation_oid": 0,
          "dropped": false,
          "name": "holdings_analysis",
          "not_null": false,
          "relation_oid": 39841,
          "type_modifier": -1,
          "type_oid": 3802
        }
      ],
      "direct_ACLs_before_fingerprint": "0dbd1ac04c7f09d1e632c79409e05f3d5c9ff2a2fe79658b53fd343f3fc87c34",
      "direct_relation_and_all_column_ACLs_before": [
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 1
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 2
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 3
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 4
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 5
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 6
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 7
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 8
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 9
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 10
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 11
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 12
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 13
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 14
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 15
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 16
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 17
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 18
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 19
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 20
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 21
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 22
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 23
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 24
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 25
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 26
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 27
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 28
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 29
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 30
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 31
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 32
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 33
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 34
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 35
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 36
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 37
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 38
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 39
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 40
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 41
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 42
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 43
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 44
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 45
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 46
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 47
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 48
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 49
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 50
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 51
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 52
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 53
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 54
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 55
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 56
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 57
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 58
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 59
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 60
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 61
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 62
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 63
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 64
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 65
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 66
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 67
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 68
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 69
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 70
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 71
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 72
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 73
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 74
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 75
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 76
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 77
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 78
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 79
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 80
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 81
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 82
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 83
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 84
        },
        {
          "normalized_acl": [
            {
              "grantable": false,
              "grantee": 16585,
              "grantor": 16587,
              "privilege": "SELECT"
            },
            {
              "grantable": false,
              "grantee": 16587,
              "grantor": 16587,
              "privilege": "DELETE"
            },
            {
              "grantable": false,
              "grantee": 16587,
              "grantor": 16587,
              "privilege": "INSERT"
            },
            {
              "grantable": false,
              "grantee": 16587,
              "grantor": 16587,
              "privilege": "MAINTAIN"
            },
            {
              "grantable": false,
              "grantee": 16587,
              "grantor": 16587,
              "privilege": "REFERENCES"
            },
            {
              "grantable": false,
              "grantee": 16587,
              "grantor": 16587,
              "privilege": "SELECT"
            },
            {
              "grantable": false,
              "grantee": 16587,
              "grantor": 16587,
              "privilege": "TRIGGER"
            },
            {
              "grantable": false,
              "grantee": 16587,
              "grantor": 16587,
              "privilege": "TRUNCATE"
            },
            {
              "grantable": false,
              "grantee": 16587,
              "grantor": 16587,
              "privilege": "UPDATE"
            }
          ],
          "object_kind": "relation",
          "object_oid": 39841,
          "owner_oid": 16587,
          "raw_acl": [
            "mfdata_admin=arwdDxtm/mfdata_admin",
            "mfdata_app=r/mfdata_admin"
          ],
          "raw_acl_is_null": false,
          "sub_id": 0
        }
      ],
      "expected_identity": {
        "expected_kind": "v",
        "nspname": "mf",
        "nspoid": 16595,
        "ord": 1,
        "relname": "current_fund_facts",
        "reloid": 39841
      },
      "expected_owner": {
        "bypass_rls": false,
        "can_login": true,
        "create_db": true,
        "create_role": true,
        "inherit": true,
        "is_function_body_principal": false,
        "is_original_query_user": false,
        "is_policy_role_target": false,
        "is_relation_access_principal": true,
        "name": "mfdata_admin",
        "replication": false,
        "role_oid": 16587,
        "superuser": false
      },
      "grant_privilege": "SELECT",
      "grantee": "mfdata_verified_reader",
      "grantee_oid": 40032,
      "qualified_name": "mf.current_fund_facts",
      "reader_capabilities_before_fingerprint": "41ed744387337fde820e603e019610b512ce72742aa35b4a26d7b530e46d81a7",
      "relation_definition_and_security": {
        "attribute_count": 84,
        "force_row_security": false,
        "namespace_name": "mf",
        "namespace_oid": 16595,
        "owner_oid": 16587,
        "private_view_definition": {
          "is_null": false,
          "sha256": "ede28a9cf8cff6a85b54de7e431187c0c03f81ab4a3f170a42c5137af589af6e",
          "utf8_bytes": 5757
        },
        "relation_kind": "v",
        "relation_name": "current_fund_facts",
        "relation_oid": 39841,
        "relation_options": null,
        "row_security": false
      },
      "row_exposure": "ALL rows exposed by this object; RLSfalse; NOT code154117/job scoped",
      "scope": "EXPLICIT_CAPTURED_COLUMNS_ONLY",
      "table_wide_or_future_columns": false,
      "with_grant_option": false
    },
    {
      "all_physical_columns": [
        {
          "attnum": 1,
          "collation_oid": 0,
          "dropped": false,
          "name": "amfi_scheme_code",
          "not_null": true,
          "relation_oid": 16683,
          "type_modifier": -1,
          "type_oid": 23
        },
        {
          "attnum": 2,
          "collation_oid": 100,
          "dropped": false,
          "name": "group_key",
          "not_null": true,
          "relation_oid": 16683,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 3,
          "collation_oid": 100,
          "dropped": false,
          "name": "base_scheme_name",
          "not_null": true,
          "relation_oid": 16683,
          "type_modifier": -1,
          "type_oid": 25
        },
        {
          "attnum": 4,
          "collation_oid": 0,
          "dropped": false,
          "name": "regular_code",
          "not_null": false,
          "relation_oid": 16683,
          "type_modifier": -1,
          "type_oid": 23
        },
        {
          "attnum": 5,
          "collation_oid": 0,
          "dropped": false,
          "name": "direct_code",
          "not_null": false,
          "relation_oid": 16683,
          "type_modifier": -1,
          "type_oid": 23
        },
        {
          "attnum": 6,
          "collation_oid": 0,
          "dropped": false,
          "name": "has_direct_sibling",
          "not_null": true,
          "relation_oid": 16683,
          "type_modifier": -1,
          "type_oid": 16
        },
        {
          "attnum": 7,
          "collation_oid": 0,
          "dropped": false,
          "name": "created_at",
          "not_null": true,
          "relation_oid": 16683,
          "type_modifier": -1,
          "type_oid": 1184
        },
        {
          "attnum": 8,
          "collation_oid": 0,
          "dropped": false,
          "name": "updated_at",
          "not_null": true,
          "relation_oid": 16683,
          "type_modifier": -1,
          "type_oid": 1184
        }
      ],
      "columns_in_actual_attnum_order": [
        {
          "attnum": 1,
          "collation_oid": 0,
          "dropped": false,
          "name": "amfi_scheme_code",
          "not_null": true,
          "relation_oid": 16683,
          "type_modifier": -1,
          "type_oid": 23
        },
        {
          "attnum": 2,
          "collation_oid": 100,
          "dropped": false,
          "name": "group_key",
          "not_null": true,
          "relation_oid": 16683,
          "type_modifier": -1,
          "type_oid": 25
        }
      ],
      "direct_ACLs_before_fingerprint": "2719def79a429b3ab4b426f1a032884626fa9728287809b465e99ff3deed57d4",
      "direct_relation_and_all_column_ACLs_before": [
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 16683,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 1
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 16683,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 2
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 16683,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 3
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 16683,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 4
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 16683,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 5
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 16683,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 6
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 16683,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 7
        },
        {
          "normalized_acl": [],
          "object_kind": "column",
          "object_oid": 16683,
          "owner_oid": 16587,
          "raw_acl": null,
          "raw_acl_is_null": true,
          "sub_id": 8
        },
        {
          "normalized_acl": [
            {
              "grantable": false,
              "grantee": 16585,
              "grantor": 16587,
              "privilege": "SELECT"
            },
            {
              "grantable": false,
              "grantee": 16587,
              "grantor": 16587,
              "privilege": "DELETE"
            },
            {
              "grantable": false,
              "grantee": 16587,
              "grantor": 16587,
              "privilege": "INSERT"
            },
            {
              "grantable": false,
              "grantee": 16587,
              "grantor": 16587,
              "privilege": "MAINTAIN"
            },
            {
              "grantable": false,
              "grantee": 16587,
              "grantor": 16587,
              "privilege": "REFERENCES"
            },
            {
              "grantable": false,
              "grantee": 16587,
              "grantor": 16587,
              "privilege": "SELECT"
            },
            {
              "grantable": false,
              "grantee": 16587,
              "grantor": 16587,
              "privilege": "TRIGGER"
            },
            {
              "grantable": false,
              "grantee": 16587,
              "grantor": 16587,
              "privilege": "TRUNCATE"
            },
            {
              "grantable": false,
              "grantee": 16587,
              "grantor": 16587,
              "privilege": "UPDATE"
            },
            {
              "grantable": false,
              "grantee": 16589,
              "grantor": 16587,
              "privilege": "DELETE"
            },
            {
              "grantable": false,
              "grantee": 16589,
              "grantor": 16587,
              "privilege": "INSERT"
            },
            {
              "grantable": false,
              "grantee": 16589,
              "grantor": 16587,
              "privilege": "SELECT"
            },
            {
              "grantable": false,
              "grantee": 16589,
              "grantor": 16587,
              "privilege": "UPDATE"
            },
            {
              "grantable": false,
              "grantee": 39392,
              "grantor": 16587,
              "privilege": "SELECT"
            }
          ],
          "object_kind": "relation",
          "object_oid": 16683,
          "owner_oid": 16587,
          "raw_acl": [
            "mfdata_admin=arwdDxtm/mfdata_admin",
            "mfdata_app=r/mfdata_admin",
            "mfdata_ingest=arwd/mfdata_admin",
            "mfdata_audit=r/mfdata_admin"
          ],
          "raw_acl_is_null": false,
          "sub_id": 0
        }
      ],
      "expected_identity": {
        "expected_kind": "r",
        "nspname": "mf",
        "nspoid": 16595,
        "ord": 2,
        "relname": "fund_variants",
        "reloid": 16683
      },
      "expected_owner": {
        "bypass_rls": false,
        "can_login": true,
        "create_db": true,
        "create_role": true,
        "inherit": true,
        "is_function_body_principal": false,
        "is_original_query_user": false,
        "is_policy_role_target": false,
        "is_relation_access_principal": true,
        "name": "mfdata_admin",
        "replication": false,
        "role_oid": 16587,
        "superuser": false
      },
      "grant_privilege": "SELECT",
      "grantee": "mfdata_verified_reader",
      "grantee_oid": 40032,
      "qualified_name": "mf.fund_variants",
      "reader_capabilities_before_fingerprint": "a78f4f112504a0143f344220758ed651f756062fa085d4d7000525731e282be9",
      "relation_definition_and_security": {
        "attribute_count": 8,
        "force_row_security": false,
        "namespace_name": "mf",
        "namespace_oid": 16595,
        "owner_oid": 16587,
        "private_view_definition": {
          "is_null": true,
          "sha256": null,
          "utf8_bytes": 0
        },
        "relation_kind": "r",
        "relation_name": "fund_variants",
        "relation_oid": 16683,
        "relation_options": null,
        "row_security": false
      },
      "row_exposure": "ALL rows exposed by this object; RLSfalse; NOT code154117/job scoped",
      "scope": "EXPLICIT_CAPTURED_COLUMNS_ONLY",
      "table_wide_or_future_columns": false,
      "with_grant_option": false
    }
  ],
  "reader": {
    "bypass_rls": false,
    "can_login": true,
    "create_db": false,
    "create_role": false,
    "inherit": false,
    "is_function_body_principal": false,
    "is_original_query_user": true,
    "is_policy_role_target": false,
    "is_relation_access_principal": true,
    "name": "mfdata_verified_reader",
    "replication": false,
    "role_oid": 40032,
    "superuser": false
  }
}
''')
