from unittest.mock import Mock

import pytest

from worksite_gis.integrations.cwa import CwaAlertAdapter, CwaRadarAdapter, CwaWeatherAdapter
from worksite_gis.integrations.geology_cloud import GeologyCloudAdapter


@pytest.mark.parametrize("adapter", [CwaWeatherAdapter, CwaAlertAdapter, CwaRadarAdapter])
def test_demo_disables_direct_cwa_adapters(settings, adapter):
    settings.WORKSITE_DEMO = True
    upstream = Mock()
    result = adapter("synthetic-only", client=upstream).fetch()
    assert result.runtime_state == "disabled"
    assert not upstream.mock_calls


def test_demo_disables_direct_geology_upstream(settings):
    settings.WORKSITE_DEMO = True
    upstream = Mock()
    with pytest.raises(RuntimeError, match="Demo"):
        GeologyCloudAdapter("drill", client=upstream).fetch({})
    assert not upstream.mock_calls
