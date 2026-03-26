"""Shared fixtures for the ASTRAL test suite."""

import pytest


@pytest.fixture
def detect_msg() -> dict:
    return {
        "type": "DETECT",
        "subject": "KESTREL-2",
        "object": "H2O_ICE",
        "lat": -43.7,
        "lon": 130.2,
        "depth_m": 18.0,
        "conf": 0.94,
    }


@pytest.fixture
def simple_msg() -> dict:
    return {
        "type": "DETECT",
        "subject": "KESTREL-1",
        "object": "BASALT",
        "lat": 10.0,
        "lon": 20.0,
        "depth_m": 0.0,
        "conf": 0.8,
    }
