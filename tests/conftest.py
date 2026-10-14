"""
Shared fixtures for Quiet Corners test suite.
"""

import pytest

from src.core.config import DEFAULT_CONFIG, PipelineConfig
from src.core.models import County, Metro


@pytest.fixture
def config() -> PipelineConfig:
    return DEFAULT_CONFIG


@pytest.fixture
def stable_pop_series() -> dict[int, int]:
    """A very stable county: ~10,000 with tiny fluctuations."""
    return {
        2020: 10000,
        2021: 10020,
        2022: 9980,
        2023: 10010,
        2024: 10005,
    }


@pytest.fixture
def growing_pop_series() -> dict[int, int]:
    """A steadily growing county."""
    return {
        2020: 10000,
        2021: 10300,
        2022: 10600,
        2023: 10900,
        2024: 11200,
    }


@pytest.fixture
def declining_pop_series() -> dict[int, int]:
    """A declining county."""
    return {
        2020: 10000,
        2021: 9700,
        2022: 9400,
        2023: 9100,
        2024: 8800,
    }


@pytest.fixture
def volatile_pop_series() -> dict[int, int]:
    """Highly volatile county."""
    return {
        2020: 10000,
        2021: 12000,
        2022: 8000,
        2023: 11500,
        2024: 7500,
    }


@pytest.fixture
def sample_metros() -> list[Metro]:
    """A small set of metros for testing remoteness."""
    return [
        Metro("New York", 40.7128, -74.0060, 8_300_000),
        Metro("Atlanta", 33.7490, -84.3880, 500_000),
        Metro("Asheville", 35.5951, -82.5515, 95_000),
        Metro("Charlotte", 35.2271, -80.8431, 900_000),
        Metro("Knoxville", 35.9606, -83.9207, 190_000),
    ]


@pytest.fixture
def franklin_nc_county() -> County:
    """Franklin, NC (Macon County) — calibration reference."""
    return County(
        fips="37113",
        state="NC",
        name="Macon County",
        population=37_000,
        rucc=7,
        lat=35.18,
        lon=-83.43,
    )


@pytest.fixture
def mock_census_response() -> list[list[str]]:
    """Simulated Census API JSON response for county populations."""
    return [
        [
            "NAME",
            "POP_2024",
            "POP_2023",
            "POP_2022",
            "POP_2021",
            "ESTIMATESBASE2020",
            "state",
            "county",
        ],
        [
            "Macon County, North Carolina",
            "37500",
            "37200",
            "37000",
            "36800",
            "36600",
            "37",
            "113",
        ],
        [
            "Swain County, North Carolina",
            "14800",
            "14750",
            "14700",
            "14650",
            "14600",
            "37",
            "173",
        ],
    ]
