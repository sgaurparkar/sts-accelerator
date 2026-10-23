import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pytest
from src.config import network_check as nc


def test_bad_mode_rejected():
    with pytest.raises(Exception):
        nc.transfer_mode({"transfer": {"mode": "dirct"}})


def test_default_and_direct_mode():
    assert nc.transfer_mode({"transfer": {"mode": "direct"}}) == "direct"
