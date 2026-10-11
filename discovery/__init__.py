"""Single-ticker strategy discovery: profile -> hypotheses -> backtest -> critique -> revise -> sealed holdout.

Code measures and decides what survives; a generator (offline templates, or Claude when a key is set)
proposes and revises DSL files. Every candidate is a counted trial. Output is draft strategies only.
"""
