import time
from decimal import Decimal

import pandas_ta as ta  # noqa: F401

from hummingbot.core.data_type.common import TradeType
from hummingbot.smart_components.executors.position_executor.data_types import PositionConfig, TrailingStop
from hummingbot.smart_components.executors.position_executor.position_executor import PositionExecutor
from hummingbot.smart_components.strategy_frameworks.data_types import OrderLevel
from hummingbot.smart_components.strategy_frameworks.market_making.market_making_controller_base import (
    MarketMakingControllerBase,
    MarketMakingControllerConfigBase,
)


class BollinGridConfig(MarketMakingControllerConfigBase):
    strategy_name: str = "bollinger_grid"
    bb_length: int = 12
    bb_long_threshold: float = 0.7
    bb_short_threshold: float = 0.3
    natr_length: int = 14


class BollinGrid(MarketMakingControllerBase):
    """
    Directional Market Making Strategy making use of NATR indicator to make spreads dynamic and shift the mid price.
    """

    def __init__(self, config: BollinGridConfig):
        super().__init__(config)
        self.config = config

    def refresh_order_condition(self, executor: PositionExecutor, order_level: OrderLevel) -> bool:
        """
        Checks if the order needs to be refreshed.
        You can reimplement this method to add more conditions.
        """
        if executor.position_config.timestamp + order_level.order_refresh_time > time.time():
            return False
        return True

    def early_stop_condition(self, executor: PositionExecutor, order_level: OrderLevel) -> bool:
        """
        If an executor has an active position, should we close it based on a condition.
        """
        return False

    def cooldown_condition(self, executor: PositionExecutor, order_level: OrderLevel) -> bool:
        """
        After finishing an order, the executor will be in cooldown for a certain amount of time.
        This prevents the executor from creating a new order immediately after finishing one and execute a lot
        of orders in a short period of time from the same side.
        """
        if executor.close_timestamp and executor.close_timestamp + order_level.cooldown_time > time.time():
            return True
        return False

    def get_processed_data(self):
        """
        Gets the price and spread multiplier from the last candlestick.
        """
        candles_df = self.candles[0].candles_df
        natr = ta.natr(candles_df["high"], candles_df["low"], candles_df["close"], length=self.config.natr_length) / 100
        candles_df.ta.bbands(length=self.config.bb_length, std=2, append=True)
        bbp = candles_df[f"BBP_{self.config.bb_length}_2.0"]

        candles_df["spread_multiplier"] = natr
        candles_df["price_multiplier"] = bbp

        # Generate filter
        long_condition = (bbp < self.config.bb_long_threshold)
        short_condition = (bbp > self.config.bb_short_threshold)
        candles_df["active_side"] = 0
        candles_df.loc[long_condition, "active_side"] = 1
        candles_df.loc[short_condition, "active_side"] = -1
        return candles_df

    def get_position_config(self, order_level: OrderLevel) -> PositionConfig:
        """
        Creates a PositionConfig object from an OrderLevel object.
        Here you can use technical indicators to determine the parameters of the position config.
        """
        close_price = self.get_close_price(self.config.exchange, self.config.trading_pair)
        bbp, spread_multiplier, active_side = self.get_price_and_spread_multiplier()
        current_side = 1 if order_level.side == TradeType.BUY else -1
        if active_side == current_side:
            spread_multiplier = spread_multiplier if order_level.side == TradeType.SELL else -spread_multiplier
            order_price = close_price * (1 + order_level.spread_factor * spread_multiplier)
            amount = order_level.order_amount_usd / order_price
            if order_level.triple_barrier_conf.trailing_stop_trailing_delta and order_level.triple_barrier_conf.trailing_stop_trailing_delta:
                trailing_stop = TrailingStop(
                    activation_price_delta=order_level.triple_barrier_conf.trailing_stop_activation_price_delta,
                    trailing_delta=order_level.triple_barrier_conf.trailing_stop_trailing_delta,
                )
            else:
                trailing_stop = None
            position_config = PositionConfig(
                timestamp=time.time(),
                trading_pair=self.config.trading_pair,
                exchange=self.config.exchange,
                side=order_level.side,
                amount=amount,
                take_profit=order_level.triple_barrier_conf.take_profit * Decimal(spread_multiplier),
                stop_loss=order_level.triple_barrier_conf.stop_loss,
                time_limit=order_level.triple_barrier_conf.time_limit,
                entry_price=Decimal(order_price),
                open_order_type=order_level.triple_barrier_conf.open_order_type,
                take_profit_order_type=order_level.triple_barrier_conf.take_profit_order_type,
                trailing_stop=trailing_stop,
                leverage=self.config.leverage
            )
            return position_config
