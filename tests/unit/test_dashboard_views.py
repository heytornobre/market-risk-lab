from market_risk_engine.dashboard.views import _signed_eur


def test_signed_eur_places_sign_before_currency_symbol() -> None:
    assert _signed_eur(-157_674.4) == "−€157,674"
    assert _signed_eur(157_674.4) == "+€157,674"
    assert _signed_eur(0.0) == "€0"
