"""Historical conversion of local closing prices into the EUR base currency."""

from datetime import date

from market_risk_engine.data.loaders import FixtureBundle
from market_risk_engine.exceptions import DataValidationError


def base_currency_prices(bundle: FixtureBundle) -> dict[tuple[date, str], float]:
    """Return local close multiplied by EUR-per-quote-currency historical FX."""
    currencies = {item.instrument_id: item.quote_currency for item in bundle.instruments}
    rates = {
        (item.date, item.base_currency, item.quote_currency): item.rate for item in bundle.fx_rates
    }
    converted: dict[tuple[date, str], float] = {}
    for price in bundle.prices:
        currency = currencies[price.instrument_id]
        key = (price.date, bundle.spec.base_currency, currency)
        try:
            rate = rates[key]
        except KeyError as error:
            raise DataValidationError(
                f"missing {bundle.spec.base_currency.value}/{currency.value} rate on {price.date}"
            ) from error
        converted[(price.date, price.instrument_id)] = price.close * rate
    return converted
