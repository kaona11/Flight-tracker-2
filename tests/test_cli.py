
import pytest

from qsuite.cli import build_parser, parse_interval
from qsuite.config import Config
from qsuite.models import Cabin


@pytest.mark.parametrize("text,seconds", [
    ("30s", 30), ("90m", 5400), ("6h", 21600), ("24h", 86400), ("2d", 172800),
    ("3600", 3600),
])
def test_interval_parsing(text, seconds):
    assert parse_interval(text) == seconds


def test_bad_interval_is_rejected_clearly():
    with pytest.raises(ValueError, match="could not parse interval"):
        parse_interval("soon")


def test_global_flags_work_before_and_after_the_subcommand():
    """`qsuite -v scan` must not be silently downgraded by subparser defaults."""
    from qsuite.cli import _fill_global_defaults
    p = build_parser()
    assert _fill_global_defaults(p.parse_args(["-v", "scan"])).verbose == 1
    assert _fill_global_defaults(p.parse_args(["scan", "-v"])).verbose == 1
    assert _fill_global_defaults(p.parse_args(["scan", "-vv"])).verbose == 2
    assert _fill_global_defaults(p.parse_args(["scan"])).verbose == 0
    assert _fill_global_defaults(p.parse_args(["scan"])).config is None
    assert _fill_global_defaults(p.parse_args(["--config", "x.yml", "scan"])).config == "x.yml"


def test_provider_list_is_split_on_commas():
    from qsuite.cli import apply_overrides
    args = build_parser().parse_args(["scan", "--provider", "ba, alaska ,qantas"])
    cfg = apply_overrides(Config(), args)
    assert cfg.providers == ["ba", "alaska", "qantas"]


def test_route_and_cabin_overrides():
    from qsuite.cli import apply_overrides
    args = build_parser().parse_args(
        ["scan", "--route", "yyz-doh", "--cabin", "first"])
    cfg = apply_overrides(Config(), args)
    assert (cfg.route.origin, cfg.route.destination) == ("YYZ", "DOH")
    assert cfg.cabin is Cabin.FIRST
