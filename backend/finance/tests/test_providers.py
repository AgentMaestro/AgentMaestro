from finance.providers import get_default_provider_catalog


def test_default_provider_catalog_includes_massive_sec_and_schwab():
    catalog = get_default_provider_catalog()
    assert [item.provider_name for item in catalog] == ["schwab", "massive", "sec", "schwab"]
    assert catalog[0].phase == "phase_1"
    assert catalog[1].phase == "phase_1_and_phase_2"
    assert catalog[2].phase == "phase_1"
    assert catalog[3].phase == "phase_1_and_phase_2"
    assert "real-time quote" in catalog[0].summary.lower()
