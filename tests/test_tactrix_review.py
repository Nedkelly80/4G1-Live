"""Tests for offline Tactrix standalone-card log review."""

import csv
import os
from datetime import datetime
from pathlib import Path
import tempfile

import pytest

import tactrix_review
from tactrix_review import (
    TactrixLogError,
    analyse_tactrix_log,
    find_latest_numbered_log,
    format_review_text,
)


_WEEKEND_LOGCFG = (
    Path(__file__).resolve().parents[1] / "trackside" / "NT43-weekend-logcfg.txt"
)


def test_logcfg_parses_and_validates_the_binding_nt43_weekend_profile():
    """The installed profile must retain only the defined, evidence-safe requests."""
    text = _WEEKEND_LOGCFG.read_text(encoding="utf-8")

    params = tactrix_review.parse_logcfg(text)
    request_map = {param.name: param for param in params}

    expected = {
        "RPM": ("mut2", "0x21", "x,31.25,*", 1),
        "TPS": ("mut2", "0x17", "x,0.39215686,*", 1),
        "ECU_Load": ("mut2", "0x1C", "x,0.625,*", 1),
        "ECU_TimingAdv": ("mut2", "0x06", "x,20,-", 1),
        "InjPulseWidth_ms": ("mut2", "0x29", "x,0.256,*", 1),
        "NarrowbandO2_V": ("mut2", "0x13", "x,0.01952,*", 1),
        "Battery_V": ("mut2", "0x14", "x,0.07333,*", 1),
        "Coolant_C": ("mut2", "0x10", "x,40,-", 1),
        "IAT_C": ("mut2", "0x11", "x,40,-", 1),
        "VSS_ECU_kph": ("mut2", "0x2F", "x,2,*", 1),
        "Coolant_raw07": ("mut2", "0x07", None, 2),
        "IAT_raw3A": ("mut2", "0x3A", None, 2),
        "IDC_pct_approx": ("calc", None, "RPM,InjPulseWidth_ms,*,0.0008333333,*", 1),
    }
    assert set(request_map) == set(expected)
    assert {
        name: (param.kind, param.param_id, param.scaling_rpn, param.priority)
        for name, param in request_map.items()
    } == expected
    assert tactrix_review.validate_weekend_logcfg(text) == []


def test_logcfg_rejects_unapproved_requests_and_mislabelled_raw_temperature():
    """Unsafe legacy channels and raw-temperature maths cannot enter the card profile."""
    text = _WEEKEND_LOGCFG.read_text(encoding="utf-8")
    with_mdp = text + "\n\ntype=mut2\nparamname=MDP\nparamid=0x38\nscalingrpn=x,1.25,*\n"
    raw_with_celsius_scaling = text.replace(
        "paramname=Coolant_raw07\nparamid=0x07\npriority=2",
        "paramname=Coolant_raw07\nparamid=0x07\nscalingrpn=x,40,-\npriority=2",
    )

    assert any("MDP" in error for error in tactrix_review.validate_weekend_logcfg(with_mdp))
    assert any(
        "Coolant_raw07" in error
        for error in tactrix_review.validate_weekend_logcfg(raw_with_celsius_scaling)
    )


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        ("\nconditionrpn=RPM,0,>\naction=start\n", "trigger"),
        ("\n; ROM identity confirmed by logging\n", "ROM/calibration"),
        ("\n; ROM/calibration identity is 23420003\n", "ROM/calibration"),
        ("\n; ROM is 23420003 confirmed\n", "ROM/calibration"),
        (
            "\n; ROM is 23420003 confirmed but calibration is not established\n",
            "ROM/calibration",
        ),
        ("\nfoo=bar\n", "Unapproved active directive"),
        ("\ntype=mut2\nparamname=RPM_copy\nparamid=0x21\n", "Duplicate MUT-II ID"),
        ("\nsetpinvoltage=1,0\n", "Duplicate singleton directive: setpinvoltage"),
        ("\ntype=mut2\n", "Duplicate singleton directive: type=mut2"),
    ],
)
def test_logcfg_rejects_active_triggers_identity_claims_and_duplicates(mutation, expected_error):
    """A logger profile must not gain control logic or provenance claims by accident."""
    text = _WEEKEND_LOGCFG.read_text(encoding="utf-8") + mutation

    assert any(expected_error in error for error in tactrix_review.validate_weekend_logcfg(text))


def test_discover_tactrix_root_requires_config_and_numbered_log(tmp_path):
    """A random CSV folder must not be selected as the Tactrix card."""
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    (unrelated / "Heat 1.csv").write_text("sample,time\\n1,0\\n", encoding="utf-8")
    card = tmp_path / "tactrix-card"
    card.mkdir()
    (card / "logcfg.txt").write_text("logger config", encoding="utf-8")
    (card / "log0013.csv").write_text("sample,time\\n1,0\\n", encoding="utf-8")

    assert tactrix_review.discover_tactrix_root([unrelated, card]) == card
    with pytest.raises(TactrixLogError, match="Tactrix"):
        tactrix_review.discover_tactrix_root([unrelated])


def test_discover_tactrix_root_rejects_ambiguous_matching_roots(tmp_path):
    """Two qualifying cards must trigger explicit selection, not first-root guessing."""
    cards = []
    for name, index in (("card-a", 13), ("card-b", 14)):
        card = tmp_path / name
        card.mkdir()
        (card / "logcfg.txt").write_text("logger config", encoding="utf-8")
        (card / f"log{index:04d}.csv").write_text(
            "sample,time\n1,0\n", encoding="utf-8"
        )
        cards.append(card)

    with pytest.raises(TactrixLogError, match="Multiple Tactrix cards"):
        tactrix_review.discover_tactrix_root(cards)


def test_archive_session_copy_creates_labelled_copy_without_touching_source(tmp_path):
    """Archiving must copy a numbered log into a dated PC folder, not move it."""
    source = tmp_path / "card" / "log0013.csv"
    source.parent.mkdir()
    original_bytes = b"sample,time\r\n1,0.000\r\n"
    source.write_bytes(original_bytes)
    destination_root = tmp_path / "archive"
    captured_at = datetime(2026, 8, 4, 18, 5)

    archived = tactrix_review.archive_session_copy(
        source, destination_root, "Heat 1", now=captured_at
    )

    assert archived == destination_root / "2026-08-04" / "2026-08-04_1805_Heat-1_log0013.csv"
    assert archived.read_bytes() == original_bytes
    assert source.read_bytes() == original_bytes
    assert source.exists()

    duplicate = tactrix_review.archive_session_copy(
        source, destination_root, "Heat 1", now=captured_at
    )

    assert duplicate.name == "2026-08-04_1805_Heat-1_log0013_2.csv"
    assert duplicate.read_bytes() == original_bytes


def test_archive_session_copy_replaces_unsafe_label_characters_and_rejects_empty_labels(tmp_path):
    """Unsafe labels and blank labels must not create ambiguous archive names."""
    source = tmp_path / "log0009.csv"
    source.write_bytes(b"sample,time\n1,0\n")
    captured_at = datetime(2026, 8, 4, 18, 5)

    archived = tactrix_review.archive_session_copy(
        source, tmp_path / "archive", "Heat: 1 / Final", now=captured_at
    )

    assert archived.name == "2026-08-04_1805_Heat-1-Final_log0009.csv"
    with pytest.raises(TactrixLogError, match="label"):
        tactrix_review.archive_session_copy(source, tmp_path / "archive", "   ", now=captured_at)


def test_find_latest_numbered_log_uses_greatest_numeric_index_not_mtime():
    """Changing a card timestamp must not override its numeric log sequence."""
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        for name, mtime in (
            ("log0009.csv", 9),
            ("log0012.csv", 1_000_000),
            ("log70003.csv", 12),
        ):
            path = root / name
            path.write_text("sample,time\n1,0\n", encoding="utf-8")
            os.utime(path, (mtime, mtime))

        assert find_latest_numbered_log(root).name == "log70003.csv"


def test_find_latest_numbered_log_ignores_renamed_csv_files():
    """A renamed heat file must not masquerade as a Tactrix sequence member."""
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        (root / "Heat1.csv").write_text("sample,time\n1,0\n", encoding="utf-8")
        (root / "log0012.CSV").write_text("sample,time\n1,0\n", encoding="utf-8")

        assert find_latest_numbered_log(root).name == "log0012.CSV"


def test_current_profile_analysis_reports_capture_metrics_without_health_verdict():
    """Removing current-profile parsing must break the trackside summary."""
    csv_text = """sample,time,RPM,TPS,ECU_Load,ECU_TimingAdv,InjPulseWidth_ms,NarrowbandO2_V,Battery_V,Coolant_C,IAT_C,VSS_ECU_kph,Coolant_raw07,IAT_raw3A,IDC_pct_approx
1,0.000,900,5,20,8,2.0,0.10,12.7,75,30,0,115,70,1.5
2,0.075,3000,80,72,15,6.0,0.82,14.2,88,34,20,128,74,15.0
3,0.150,6875,100,95,20,8.0,0.91,14.4,95,39,65,135,79,45.8
4,0.225,1500,20,35,10,3.0,0.20,14.1,94,40,10,134,80,3.8
"""
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "log70003.csv"
        path.write_text(csv_text, encoding="utf-8")

        review = analyse_tactrix_log(path)

    assert review.profile_kind == "current"
    assert review.row_count == 4
    assert review.duration_seconds == pytest.approx(0.225)
    assert review.sampling_hz == pytest.approx(13.3333333)
    assert review.high_load_row_count == 2
    assert review.channel_stats["rpm"].maximum == 6875
    assert review.high_load_stats["battery_v"].minimum == 14.2
    assert review.channel_stats["coolant_c"].maximum == 95
    assert review.engine_health_verdict is None


def test_format_review_text_presents_current_capture_and_evidence_limits(tmp_path):
    """The review dialog must show useful facts without implying engine health."""
    path = tmp_path / "log70003.csv"
    path.write_text(
        """sample,time,RPM,TPS,ECU_Load,ECU_TimingAdv,InjPulseWidth_ms,NarrowbandO2_V,Battery_V,Coolant_C,IAT_C,VSS_ECU_kph,Coolant_raw07,IAT_raw3A,IDC_pct_approx
1,0.000,900,5,20,8,2.0,0.10,12.7,75,30,0,115,70,1.5
2,0.075,3000,80,72,15,6.0,0.82,14.2,88,34,20,128,74,15.0
3,0.150,6875,100,95,20,8.0,0.91,14.4,95,39,65,135,79,45.8
""",
        encoding="utf-8",
    )

    text = format_review_text(analyse_tactrix_log(path))

    assert "CAPTURE READY" in text
    assert "Profile: current" in text
    assert f"File: {path.resolve()}" in text
    assert "Rows: 3" in text
    assert "Duration: 0.150 s" in text
    assert "Rate: 13.3 Hz" in text
    assert "Max RPM: 6875" in text
    assert "High-load (RPM >= 3000, TPS >= 80%): 2 rows" in text
    assert "Timing: 15.0 to 20.0 deg" in text
    assert "Battery: 14.2 to 14.4 V" in text
    assert "IDC: 15.0 to 45.8 % approx" in text
    assert "ECU-scaled coolant (unvalidated): 75.0 to 95.0 C" in text
    assert "ECU-scaled IAT (unvalidated): 30.0 to 39.0 C" in text
    assert "ECU VSS (unvalidated): 0.0 to 65.0 km/h" in text
    assert "ECU-scaled coolant/IAT and ECU VSS require car-side validation." in text
    assert "ECU VSS is not confirmed ground speed." in text
    assert "Narrowband O2 is switching evidence, not a wideband AFR measurement." in text
    assert "No knock channel is available in this capture; do not infer knock status." in text
    assert "Raw MUT 0x07/0x3A temperatures are counts, not Celsius." in text
    assert "engine health" not in text.lower()


def test_unknown_profile_never_reports_capture_ready(tmp_path):
    """An unrecognised header must require review even when its few channels vary."""
    path = tmp_path / "log0020.csv"
    path.write_text(
        """sample,time,RPM,TPS,Battery_V,Coolant_C,IAT_C
1,0.000,900,5,12.5,75,30
2,0.075,1800,20,13.0,80,31
3,0.150,3000,80,13.5,85,32
""",
        encoding="utf-8",
    )

    review = analyse_tactrix_log(path)
    text = format_review_text(review)

    assert review.profile_kind == "unknown"
    assert review.capture_status == "warning"
    assert "Unknown profile" in review.evidence_warnings
    assert text.startswith("NEEDS CHECK")
    assert "CAPTURE READY" not in text


def test_current_profile_readiness_checks_every_canonical_channel(tmp_path):
    """Missing, zero, constant, and malformed current channels must block readiness."""
    path = tmp_path / "log0021.csv"
    path.write_text(
        """sample,time,RPM,TPS,ECU_Load,ECU_TimingAdv,InjPulseWidth_ms,NarrowbandO2_V,Battery_V,Coolant_C,IAT_C,VSS_ECU_kph,IAT_raw3A,IDC_pct_approx
1,0.000,900,5,20,10,2.0,0,12.5,75,30,n/a,70,1.5
2,0.075,3000,80,80,10,6.0,0,13.0,80,31,n/a,,15.0
3,0.150,6000,100,95,10,8.0,0,13.5,85,32,n/a,74,40.0
""",
        encoding="utf-8",
    )

    review = analyse_tactrix_log(path)

    assert review.profile_kind == "current"
    assert review.capture_status == "warning"
    assert "Missing coolant_raw07 channel" in review.evidence_warnings
    assert "All-zero narrowband_o2_v channel" in review.evidence_warnings
    assert "Constant ecu_timing_adv channel" in review.evidence_warnings
    assert "Non-numeric vss_ecu_kph channel" in review.evidence_warnings
    assert "Incomplete/non-numeric samples for iat_raw3a channel" in review.evidence_warnings


def test_legacy_profile_warns_about_rejected_fields_and_raw_temperatures():
    """Removing legacy evidence limits must make historic Round 6 logs look safe."""
    csv_text = """sample,time,RPM,TPS,ECULoad,TimingAdv,InjPulseWidth,Airflow,O2Sensor,Speed,Knock,CoolantTemp,AirTemp,Battery,MDP,KnockSum,IDC
1,0.000,1000,5,20,10,2.0,200,0.1,0,0,47,71,14.0,0,0,1.7
2,0.075,3500,90,80,18,6.0,250,0.8,30,0,50,74,14.2,0,0,17.5
"""
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "log0012.csv"
        path.write_text(csv_text, encoding="utf-8")
        review = analyse_tactrix_log(path)

    assert review.profile_kind == "legacy"
    warnings = "\n".join(review.evidence_warnings)
    assert "Airflow" in warnings
    assert "MDP" in warnings
    assert "Knock" in warnings
    assert "raw values, not Celsius" in warnings
    assert "healthy" not in warnings.lower()
    assert review.engine_health_verdict is None
    assert "coolant_c" not in review.channel_stats
    assert "iat_c" not in review.channel_stats
    assert review.channel_stats["coolant_raw07"].maximum == 50
    assert review.channel_stats["iat_raw3a"].maximum == 74
    assert "Missing coolant_c channel" not in review.evidence_warnings
    assert "Missing iat_c channel" not in review.evidence_warnings


@pytest.mark.parametrize(
    ("csv_text", "expected_message"),
    [
        ("", "header"),
        ("sample,time,RPM\n", "no data rows"),
        ("sample,time,RPM\n1,1.0,1000\n2,1.0,1200\n", "time"),
    ],
    ids=("empty", "header_only", "non_increasing_time"),
)
def test_malformed_capture_never_returns_ready_status(csv_text, expected_message):
    """Dropping malformed-capture validation must not yield a ready review."""
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "log0009.csv"
        path.write_text(csv_text, encoding="utf-8")
        with pytest.raises(TactrixLogError, match=expected_message):
            analyse_tactrix_log(path)


def test_invalid_utf8_is_wrapped_as_tactrix_log_error(tmp_path):
    """A decode failure must stay inside the analyzer's GUI-safe error boundary."""
    path = tmp_path / "log0022.csv"
    path.write_bytes(b"sample,time,RPM\n1,0.000,\xff\n")

    with pytest.raises(TactrixLogError, match="parse Tactrix CSV"):
        analyse_tactrix_log(path)


def test_csv_iteration_error_is_wrapped_as_tactrix_log_error(tmp_path):
    """A CSV parser failure must be reported through the analyzer's public error type."""
    path = tmp_path / "log0023.csv"
    path.write_text(
        "sample,time,RPM\n1,0.000," + ("9" * 128) + "\n", encoding="utf-8"
    )
    original_limit = csv.field_size_limit()
    csv.field_size_limit(32)
    try:
        with pytest.raises(TactrixLogError, match="parse Tactrix CSV"):
            analyse_tactrix_log(path)
    finally:
        csv.field_size_limit(original_limit)


def test_available_all_zero_channel_is_reported_as_capture_quality_issue():
    """A stuck optional channel must not silently disappear from the evidence."""
    csv_text = """sample,time,RPM,TPS,NarrowbandO2_V
1,0.000,1000,5,0
2,0.075,2000,20,0
"""
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "log0013.csv"
        path.write_text(csv_text, encoding="utf-8")
        review = analyse_tactrix_log(path)

    assert "All-zero narrowband_o2_v channel" in review.evidence_warnings
    assert review.capture_status == "warning"


def test_partial_non_numeric_core_channel_marks_capture_warning():
    """Dropping one battery sample must not leave the capture ready."""
    csv_text = """sample,time,RPM,TPS,Battery_V,Coolant_C,IAT_C
1,0.000,1000,5,12.5,75,30
2,0.075,2000,20,n/a,80,31
3,0.150,3000,80,13.5,85,32
"""
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "log0014.csv"
        path.write_text(csv_text, encoding="utf-8")
        review = analyse_tactrix_log(path)

    assert review.capture_status == "warning"
    assert "Incomplete/non-numeric samples for battery_v channel" in review.evidence_warnings


def test_calculated_idc_is_included_in_high_load_statistics():
    """Removing fallback IDC from high-load rows must break its load summary."""
    csv_text = """sample,time,RPM,TPS,InjPulseWidth_ms,Battery_V,Coolant_C,IAT_C
1,0.000,1000,5,2.0,12.5,75,30
2,0.075,3000,80,6.0,13.0,80,31
3,0.150,6000,100,8.0,13.5,85,32
"""
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "log0015.csv"
        path.write_text(csv_text, encoding="utf-8")
        review = analyse_tactrix_log(path)

    assert review.high_load_stats["idc_pct_approx"].minimum == pytest.approx(15.0)
    assert review.high_load_stats["idc_pct_approx"].maximum == pytest.approx(40.0)


def test_whitespace_padded_headers_are_normalized_for_rows_and_profile():
    """Header whitespace must not make otherwise valid current CSV data unreadable."""
    csv_text = (
        " sample , time , RPM , TPS , ECU_Load , InjPulseWidth_ms , Battery_V , Coolant_C , IAT_C "
        "\n1,0.000,1000,5,20,2.0,12.5,75,30"
        "\n2,0.075,3000,80,80,6.0,13.0,80,31\n"
    )
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "log0016.csv"
        path.write_text(csv_text, encoding="utf-8")
        review = analyse_tactrix_log(path)

    assert review.profile_kind == "current"
    assert review.row_count == 2
    assert review.channel_stats["rpm"].maximum == 3000
