def split_profit(total_investment, sale_price, investments):
    """
    Profit is ALWAYS split equally between participants.
    Each participant receives back their own invested amount
    PLUS an equal share of the total profit.
    """
    profit = sale_price - total_investment
    n = len(investments)
    if n == 0:
        return []
    profit_share = profit / n
    return [
        {
            "name": name,
            "invested": amount,
            "profit_share": profit_share,
            "payout": amount + profit_share,
        }
        for name, amount in investments
    ]
