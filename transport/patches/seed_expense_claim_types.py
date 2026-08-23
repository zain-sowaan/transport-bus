# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Expense Claim Types moved out of fixtures, which overwrote their per-company
GL accounts on every migrate. See transport.setup for the full reasoning."""

from transport.setup import ensure_expense_claim_types


def execute():
	ensure_expense_claim_types()
