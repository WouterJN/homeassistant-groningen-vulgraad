"""Test fixtures for the Groningen vulgraad integration."""

import pytest

from custom_components.groningen_vulgraad.api import Container

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load custom_components/ during tests."""
    yield


def make_container(number, vulgraad=51, has_sensor=True, cluster="1234",
                   fraction="GREY", lat=53.2, lon=6.5):
    """has_sensor None means unknown, as the open data WFS reports it."""
    return Container(
        number=number,
        cluster_id=cluster,
        cluster_name=f"{cluster} - Teststraat",
        vulgraad=vulgraad,
        has_sensor=has_sensor,
        fraction=fraction,
        warn=60,
        alarm=80,
        latitude=lat,
        longitude=lon,
    )


@pytest.fixture
def containers():
    """A small stand-in for the municipality-wide payload."""
    return [
        make_container("111", vulgraad=51),
        make_container("222", vulgraad=85, cluster="5678", fraction="BLUE",
                       lat=53.3, lon=6.6),
        make_container("333", vulgraad=None, has_sensor=False, cluster="9999"),
    ]
