"""Phase 9: renders the real report data dict (`app/services/report_builder.py`)
into a real PDF, a real CSV (only when tabular data actually exists), and a
real ZIP bundle. Every renderer here is a pure function of the data dict (and,
for the bundle, real artifact file bytes already on disk) — no value is
invented here; a missing section is rendered as an explicit "not available"
line, never silently skipped without explanation and never a fabricated
number standing in for one that doesn't exist.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

_STYLES = getSampleStyleSheet()
_H1 = _STYLES["Heading1"]
_H2 = _STYLES["Heading2"]
_BODY = _STYLES["BodyText"]
_SMALL = ParagraphStyle("Small", parent=_BODY, fontSize=8, leading=10)
_DISCLAIMER = ParagraphStyle(
    "Disclaimer", parent=_BODY, fontSize=9, leading=12, textColor=colors.HexColor("#92400e")
)


def _na(value) -> str:
    return "Not available" if value is None else str(value)


def _escape(text: str) -> str:
    """reportlab Paragraphs parse their text as mini-markup, so a data value
    containing '<' or '&' (e.g. a calibration error quoting a comparison)
    must be escaped or it breaks rendering."""
    return xml_escape(text)


def _kv_table(rows: list[tuple[str, object]]) -> Table:
    data = [
        [Paragraph(f"<b>{_escape(k)}</b>", _BODY), Paragraph(_escape(_na(v)), _BODY)]
        for k, v in rows
    ]
    table = Table(data, colWidths=[55 * mm, 110 * mm])
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e2e8f0")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f8fafc")),
            ]
        )
    )
    return table


def _fmt(value, spec: str) -> str:
    return "Not available" if value is None else format(value, spec)


def _calibration_quality_flowables(cal: dict) -> list:
    """P1-2: keeps the four kinds of calibration evidence visibly separate —
    all-valid-sample in-sample diagnostics, held-out cross-validation, the
    quality-gate decision, and the exact policy applied. A calibration
    produced before the gate existed has none of these and renders nothing
    here rather than a fabricated section."""
    diagnostics = cal.get("fit_diagnostics")
    cross_validation = cal.get("cross_validation")
    gate = cal.get("quality_gate")
    if not (diagnostics or cross_validation or gate):
        return []

    flowables: list = [Spacer(1, 2 * mm)]
    if diagnostics:
        flowables.append(Paragraph("In-sample diagnostics (all valid samples)", _SMALL))
        flowables.append(
            _kv_table(
                [
                    (
                        "All-sample MAE / RMSE / bias",
                        f"{_fmt(diagnostics.get('all_sample_mae'), '.4f')} / "
                        f"{_fmt(diagnostics.get('all_sample_rmse'), '.4f')} / "
                        f"{_fmt(diagnostics.get('all_sample_bias'), '.4e')}",
                    ),
                    ("In-sample R²", _fmt(diagnostics.get("in_sample_r2"), ".4f")),
                    (
                        "Pearson r / Spearman rho",
                        f"{_fmt(diagnostics.get('pearson_r'), '.4f')} / "
                        f"{_fmt(diagnostics.get('spearman_rho'), '.4f')}",
                    ),
                    (
                        "Valid / effective samples",
                        f"{diagnostics.get('valid_sample_count')} / "
                        f"{_na(diagnostics.get('effective_sample_count'))}",
                    ),
                ]
            )
        )
    if cross_validation:
        rows = [
            ("Method", cross_validation.get("method")),
            ("Feasible", cross_validation.get("feasible")),
            ("Folds", cross_validation.get("fold_count")),
        ]
        if cross_validation.get("blocks_per_side") is not None:
            rows.append(
                (
                    "Spatial blocks (per side / non-empty)",
                    f"{cross_validation.get('blocks_per_side')} / "
                    f"{cross_validation.get('non_empty_block_count')}",
                )
            )
        if cross_validation.get("feasible"):
            rows += [
                (
                    "Held-out MAE / RMSE / bias",
                    f"{_fmt(cross_validation.get('heldout_mae'), '.4f')} / "
                    f"{_fmt(cross_validation.get('heldout_rmse'), '.4f')} / "
                    f"{_fmt(cross_validation.get('heldout_bias'), '.4e')}",
                ),
                ("Held-out skill vs. mean baseline", _fmt(cross_validation.get("skill"), ".4f")),
            ]
        else:
            rows.append(("Why not feasible", cross_validation.get("infeasibility_reason")))
        flowables.append(Paragraph("Held-out cross-validation", _SMALL))
        flowables.append(_kv_table(rows))
    if gate:
        policy = gate.get("policy") or {}
        failed = gate.get("failed_criteria") or []
        rows = [
            ("Decision", "Passed" if gate.get("passed") else "Failed"),
            (
                "Failed criteria",
                (
                    "; ".join(f"{c.get('criterion')}: {c.get('reason')}" for c in failed)
                    if failed
                    else "None"
                ),
            ),
            ("Not evaluated", ", ".join(gate.get("not_evaluated") or []) or "None"),
            (
                "Applied policy",
                f"version {policy.get('version')}, minimum held-out skill "
                f"{policy.get('min_cv_skill')}, expected scale sign "
                f"{policy.get('expected_scale_sign')}, blocks per side "
                f"{policy.get('cv_blocks_per_side')}",
            ),
        ]
        flowables.append(Paragraph("Calibration quality gate", _SMALL))
        flowables.append(_kv_table(rows))
        if policy.get("statement"):
            flowables.append(Paragraph(_escape(policy["statement"]), _DISCLAIMER))
    return flowables


def _calibration_residuals_flowables(residuals: dict | None) -> list:
    """P1-5: residuals at calibration sample locations. Held-out and in-sample
    fit statistics are always labelled separately; per-block (DEM) or
    per-point (GCP) tables only — never a per-pixel map."""
    if not residuals:
        return []
    flowables: list = [Spacer(1, 2 * mm), Paragraph("Calibration residuals", _SMALL)]
    if not residuals.get("available"):
        flowables.append(Paragraph(_escape(_na(residuals.get("note"))), _BODY))
        return flowables

    rows: list[tuple[str, object]] = [
        ("Definition", residuals.get("residual_definition")),
        ("Units", residuals.get("units")),
        (
            "Samples (valid / candidates)",
            f"{residuals.get('sample_count')} / {residuals.get('total_candidate_samples')}",
        ),
        ("Held-out method", residuals.get("cv_method")),
    ]
    for kind in ("heldout", "fit"):
        entry = (residuals.get("kinds") or {}).get(kind) or {}
        stats = entry.get("statistics") or {}
        rows.append(
            (
                f"{entry.get('label', kind)}: MAE / RMSE / bias / min / max",
                f"{_fmt(stats.get('mae'), '.4f')} / {_fmt(stats.get('rmse'), '.4f')} / "
                f"{_fmt(stats.get('bias'), '.4e')} / {_fmt(stats.get('min'), '.4f')} / "
                f"{_fmt(stats.get('max'), '.4f')}",
            )
        )
    flowables.append(_kv_table(rows))

    table_rows: list[list] = []
    if residuals.get("heldout_blocks"):
        table_rows.append(["Block (row, col)", "Held-out n", "RMSE", "Bias", "MAE"])
        for block in residuals["heldout_blocks"]:
            table_rows.append(
                [
                    f"{block['block_id']} ({block['block_row']}, {block['block_col']})",
                    block["heldout_count"],
                    _fmt(block["heldout_rmse"], ".4f"),
                    _fmt(block["heldout_bias"], ".4f"),
                    _fmt(block["heldout_mae"], ".4f"),
                ]
            )
    elif residuals.get("gcp_points"):
        table_rows.append(
            ["GCP", "Row, col", "Reference", "Held-out pred.", "Held-out resid.", "Fit resid."]
        )
        for point in residuals["gcp_points"]:
            table_rows.append(
                [
                    point["gcp_index"],
                    f"{point['row']}, {point['col']}",
                    _fmt(point["reference_elevation"], ".4f"),
                    _fmt(point["predicted_heldout"], ".4f"),
                    _fmt(point["residual_heldout"], ".4f"),
                    _fmt(point["residual_fit"], ".4f")
                    + ("" if point["inlier_in_production_fit"] else " (outlier)"),
                ]
            )
    if table_rows:
        table = Table(table_rows)
        table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e2e8f0")),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                ]
            )
        )
        flowables.append(Spacer(1, 1 * mm))
        flowables.append(table)
    if residuals.get("disclaimer"):
        flowables.append(Paragraph(_escape(residuals["disclaimer"]), _DISCLAIMER))
    return flowables


def _ground_filter_flowables(ground_filter: dict | None) -> list:
    """P1-3 section: the DTM/nDSM ESTIMATES, their method, applied
    parameters, valid-pixel statistics, ground share, and limitations —
    never presented as a measured bare-earth model or measured heights."""
    if ground_filter is None:
        return []
    flowables: list = [
        Paragraph(
            "Bare-Earth Estimate (DTM) and Height Above Ground (nDSM)",
            ParagraphStyle(
                "h3gf",
                parent=_BODY,
                fontSize=10,
                leading=13,
                spaceBefore=4,
                fontName="Helvetica-Bold",
            ),
        )
    ]
    if ground_filter.get("status") != "completed":
        rows = [("Status", ground_filter.get("status"))]
        if ground_filter.get("note"):
            rows.append(("Note", ground_filter["note"]))
        if ground_filter.get("error"):
            rows.append(("Ground filter error", ground_filter["error"]))
        flowables.append(_kv_table(rows))
        flowables.append(Spacer(1, 3 * mm))
        return flowables

    params = ground_filter.get("parameters") or {}
    stats = ground_filter.get("statistics") or {}
    cell = ground_filter.get("cell_size") or {}
    policy = ground_filter.get("policy") or {}
    flowables.append(
        _kv_table(
            [
                ("Status", "completed"),
                ("Method", ground_filter.get("method")),
                (
                    "Parameters",
                    f"max window {params.get('max_window_m')} m, slope {params.get('slope')}, "
                    f"initial threshold {params.get('initial_threshold')}, max threshold "
                    f"{params.get('max_threshold')} (policy {policy.get('version')})",
                ),
                (
                    "Cell size (m)",
                    f"{_fmt(cell.get('x_m'), '.4g')} x {_fmt(cell.get('y_m'), '.4g')}",
                ),
                ("Window levels", len(ground_filter.get("window_levels") or [])),
                (
                    "Valid / ground pixels",
                    f"{stats.get('valid_count')} / {stats.get('ground_count')} "
                    f"(ground share {_fmt(stats.get('ground_fraction'), '.3f')})",
                ),
                (
                    "DTM min / max / mean",
                    f"{_fmt(stats.get('dtm_min'), '.3f')} / {_fmt(stats.get('dtm_max'), '.3f')} / "
                    f"{_fmt(stats.get('dtm_mean'), '.3f')}",
                ),
                (
                    "nDSM min / max / mean / p95",
                    " / ".join(
                        _fmt(stats.get(key), ".3f")
                        for key in ("ndsm_min", "ndsm_max", "ndsm_mean", "ndsm_p95")
                    ),
                ),
            ]
        )
    )
    for text in (
        ground_filter.get("dtm_value_semantics"),
        ground_filter.get("ndsm_value_semantics"),
        ground_filter.get("limitations"),
        policy.get("statement"),
    ):
        if text:
            flowables.append(Paragraph(_escape(text), _DISCLAIMER))
    flowables.append(Spacer(1, 3 * mm))
    return flowables


def render_pdf(data: dict) -> bytes:
    """Builds the real report PDF directly from the report data dict — no
    static template with placeholder values; every row below is a real
    dict lookup, and any missing/unavailable field renders as a plain
    "Not available" line rather than a blank or fabricated one."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        title="TERRAIN-X Analysis Report",
    )
    story = []

    project = data["project"]
    dataset = data["dataset"]
    story.append(Paragraph("TERRAIN-X Analysis Report", _H1))
    story.append(Paragraph(f"Generated: {data['generated_at']}", _SMALL))
    story.append(Spacer(1, 6 * mm))

    story.append(Paragraph("Project & Dataset", _H2))
    story.append(
        _kv_table(
            [
                ("Project", project["name"]),
                ("Project ID", project["id"]),
                ("Dataset filename", dataset["original_filename"]),
                ("Dataset ID", dataset["id"]),
                (
                    "Dimensions",
                    f"{dataset['width']} x {dataset['height']}, {dataset['bands']} band(s)",
                ),
                (
                    "Georeferencing",
                    (
                        f"{dataset['crs']} ({dataset['bounds']})"
                        if dataset["is_georeferenced"]
                        else "Not georeferenced"
                    ),
                ),
            ]
        )
    )
    story.append(Spacer(1, 6 * mm))

    depth = data.get("depth_analysis")
    story.append(Paragraph("Depth Analysis", _H2))
    if depth is None:
        story.append(Paragraph("No depth-analysis job has completed for this dataset.", _BODY))
    else:
        story.append(
            _kv_table(
                [
                    ("Analysis job ID", depth["analysis_job_id"]),
                    ("Status", depth["status"]),
                    ("Completed at", depth["completed_at"]),
                ]
            )
        )
        story.append(Spacer(1, 3 * mm))

        model = depth.get("depth_estimation")
        story.append(
            Paragraph(
                "Relative Depth (model)",
                ParagraphStyle(
                    "h3",
                    parent=_BODY,
                    fontSize=10,
                    leading=13,
                    spaceBefore=4,
                    fontName="Helvetica-Bold",
                ),
            )
        )
        if model is None:
            story.append(Paragraph("Not available.", _BODY))
        else:
            story.append(
                _kv_table(
                    [
                        ("Model", model["model_name"]),
                        ("Revision", model["model_revision"]),
                        ("Device", model["device"]),
                        ("Inference time (s)", model["inference_seconds"]),
                        ("Output size", f"{model['output_width']} x {model['output_height']}"),
                    ]
                )
            )
            story.append(Paragraph(model["value_semantics"], _DISCLAIMER))
        story.append(Spacer(1, 3 * mm))

        cal = depth["calibration"]
        story.append(
            Paragraph(
                "Metric Calibration",
                ParagraphStyle(
                    "h3b",
                    parent=_BODY,
                    fontSize=10,
                    leading=13,
                    spaceBefore=4,
                    fontName="Helvetica-Bold",
                ),
            )
        )
        if cal["status"] == "uncalibrated":
            story.append(Paragraph(cal["note"], _BODY))
        else:
            rows = [
                ("Status", cal["status"]),
                ("Method", cal.get("method")),
                ("Reference type", cal.get("reference_type")),
                ("Scale (a)", cal.get("scale_a")),
                ("Offset (b)", cal.get("offset_b")),
                ("Source CRS", cal.get("source_crs")),
                ("Reference CRS", cal.get("reference_crs")),
                (
                    "Valid / inlier / outlier samples",
                    f"{cal.get('valid_samples')} / {cal.get('inlier_samples')} / "
                    f"{cal.get('outlier_samples')}",
                ),
            ]
            metrics = cal.get("validation_metrics")
            if metrics:
                rows.append(
                    (
                        "In-sample inlier MAE / RMSE / bias",
                        f"{metrics.get('mae'):.4f} / {metrics.get('rmse'):.4f} / "
                        f"{metrics.get('bias'):.4e}",
                    )
                )
            if cal.get("error"):
                rows.append(("Calibration error", cal["error"]))
            story.append(_kv_table(rows))
            story.extend(_calibration_quality_flowables(cal))
            story.extend(_calibration_residuals_flowables(cal.get("residuals")))
            if cal.get("limitations"):
                story.append(Paragraph(_escape(cal["limitations"]), _DISCLAIMER))
        story.append(Spacer(1, 3 * mm))

        story.extend(_ground_filter_flowables(depth.get("ground_filter")))

        sem = depth["semantic_segmentation"]
        story.append(
            Paragraph(
                "Semantic Region Segmentation",
                ParagraphStyle(
                    "h3c",
                    parent=_BODY,
                    fontSize=10,
                    leading=13,
                    spaceBefore=4,
                    fontName="Helvetica-Bold",
                ),
            )
        )
        if sem["status"] == "not_requested":
            story.append(Paragraph("Not requested for this job.", _BODY))
        else:
            story.append(
                _kv_table(
                    [
                        ("Status", sem["status"]),
                        ("Model", sem.get("model_name")),
                        ("Region count", sem.get("region_count")),
                        ("Mean mask stability", sem.get("mask_stability_mean")),
                        ("Error", sem.get("error")),
                    ]
                )
            )
            if sem.get("value_semantics"):
                story.append(Paragraph(sem["value_semantics"], _DISCLAIMER))
    story.append(Spacer(1, 6 * mm))

    disaster = data.get("disaster_screening")
    story.append(Paragraph("Disaster Screening (Terrain-Derived)", _H2))
    if disaster is None:
        story.append(Paragraph("No disaster-screening job has completed for this dataset.", _BODY))
    else:
        story.append(
            _kv_table(
                [
                    ("Analysis job ID", disaster["analysis_job_id"]),
                    (
                        "Source artifact",
                        f"{disaster.get('source_artifact_type')} "
                        f"({disaster.get('source_artifact_id')})",
                    ),
                    ("Analysis CRS", disaster.get("analysis_crs")),
                ]
            )
        )
        stats = disaster.get("terrain_statistics")
        if stats:
            story.append(Spacer(1, 2 * mm))
            story.append(
                _kv_table(
                    [
                        (
                            "Elevation min / max / mean",
                            f"{stats['min_elevation']:.3f} / {stats['max_elevation']:.3f} / "
                            f"{stats['mean_elevation']:.3f}",
                        ),
                        (
                            "Slope min / max / mean (deg)",
                            (
                                f"{stats['min_slope_deg']} / {stats['max_slope_deg']} / "
                                f"{stats['mean_slope_deg']}"
                                if stats.get("mean_slope_deg") is not None
                                else "Not available"
                            ),
                        ),
                        (
                            "Valid / total pixels",
                            f"{stats['valid_pixel_count']} / {stats['total_pixel_count']}",
                        ),
                    ]
                )
            )
        flood = disaster.get("flood")
        if flood:
            story.append(Spacer(1, 2 * mm))
            story.append(
                Paragraph(
                    "Flood Screening",
                    ParagraphStyle(
                        "h3d",
                        parent=_BODY,
                        fontSize=10,
                        leading=13,
                        spaceBefore=4,
                        fontName="Helvetica-Bold",
                    ),
                )
            )
            story.append(
                _kv_table(
                    [
                        ("Water level", flood["water_level"]),
                        (
                            "Potentially inundated",
                            f"{flood['potentially_inundated_pixel_count']} px "
                            f"({flood['potentially_inundated_percentage']:.2f}%)",
                        ),
                        ("Area", f"{flood['potentially_inundated_area']} {flood['area_unit']}"),
                    ]
                )
            )
            story.append(Paragraph(flood["disclaimer"], _DISCLAIMER))
        landslide = disaster.get("landslide")
        if landslide:
            story.append(Spacer(1, 2 * mm))
            story.append(
                Paragraph(
                    "Landslide Susceptibility Screening",
                    ParagraphStyle(
                        "h3e",
                        parent=_BODY,
                        fontSize=10,
                        leading=13,
                        spaceBefore=4,
                        fontName="Helvetica-Bold",
                    ),
                )
            )
            counts = landslide["class_pixel_counts"]
            story.append(
                _kv_table(
                    [
                        ("Thresholds (deg)", landslide["thresholds"]),
                        ("Class pixel counts", counts),
                        (
                            "Max / mean slope (deg)",
                            f"{landslide['max_slope_deg']:.2f} / {landslide['mean_slope_deg']:.2f}",
                        ),
                    ]
                )
            )
            story.append(Paragraph(landslide["disclaimer"], _DISCLAIMER))
    story.append(Spacer(1, 6 * mm))

    story.append(Paragraph("Generated Artifacts", _H2))
    artifacts = data.get("artifacts") or []
    if not artifacts:
        story.append(Paragraph("No artifacts were generated.", _BODY))
    else:
        header = ["Type", "Dimensions", "dtype", "CRS", "Min", "Max"]
        rows = [header]
        for a in artifacts:
            rows.append(
                [
                    a["display_name"],
                    (
                        f"{a['width']}x{a['height']}"
                        if a.get("width") is not None
                        else f"{_na(a.get('sample_count'))} points"
                    ),
                    a.get("dtype") or "-",
                    a.get("crs") or "-",
                    f"{a['min_value']:.3f}" if a.get("min_value") is not None else "-",
                    f"{a['max_value']:.3f}" if a.get("max_value") is not None else "-",
                ]
            )
        table = Table(rows, colWidths=[38 * mm, 28 * mm, 20 * mm, 28 * mm, 20 * mm, 20 * mm])
        table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e2e8f0")),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                ]
            )
        )
        story.append(table)
    story.append(Spacer(1, 6 * mm))

    story.append(Paragraph("Measurements", _H2))
    measurements = data.get("measurements") or []
    if not measurements:
        story.append(Paragraph("No measurements were saved for this analysis.", _BODY))
    else:
        rows = [["Type", "Created", "Result"]]
        for m in measurements:
            rows.append([m["measurement_type"], m["created_at"], str(m["result_data"])[:60]])
        table = Table(rows, colWidths=[30 * mm, 45 * mm, 79 * mm])
        table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e2e8f0")),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTSIZE", (0, 0), (-1, -1), 7),
                ]
            )
        )
        story.append(table)
    story.append(Spacer(1, 6 * mm))

    story.append(Paragraph("Scientific Limitations & Disclaimers", _H2))
    for limitation in data.get("limitations", []):
        story.append(Paragraph(f"• {limitation}", _DISCLAIMER))

    doc.build(story)
    return buf.getvalue()


def _write_csv_section(writer, title: str, header: list[str], rows: list[list]) -> None:
    writer.writerow([title])
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    writer.writerow([])


_RESIDUAL_CSV_COLUMNS = [
    "sample_index",
    "row",
    "col",
    "source_x",
    "source_y",
    "longitude",
    "latitude",
    "relative_depth",
    "reference_elevation",
    "predicted_heldout",
    "residual_heldout",
    "predicted_fit",
    "residual_fit",
    "inlier_in_production_fit",
    "fold_id",
    "block_id",
    "reference_cell_id",
    "gcp_index",
]


def render_csv(data: dict, calibration_residuals: dict | None = None) -> bytes | None:
    """Builds one real CSV file covering every genuinely-available tabular
    section (measurements, terrain statistics, hazard class counts, P1-5
    calibration residual samples) as blank-line-separated sections. Returns
    None when NONE of those sections have any real data — never an empty
    placeholder file. `calibration_residuals` is the stored residual GeoJSON
    FeatureCollection, rendered as its own clearly-titled section, never
    mixed with measurement rows."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    wrote_any = False

    measurements = data.get("measurements") or []
    if measurements:
        _write_csv_section(
            writer,
            "Measurements",
            [
                "id",
                "measurement_type",
                "analysis_job_id",
                "artifact_id",
                "created_at",
                "result_data",
            ],
            [
                [
                    m["id"],
                    m["measurement_type"],
                    m["analysis_job_id"],
                    m["artifact_id"],
                    m["created_at"],
                    m["result_data"],
                ]
                for m in measurements
            ],
        )
        wrote_any = True

    ground_filter = (data.get("depth_analysis") or {}).get("ground_filter") or {}
    if ground_filter.get("status") == "completed" and ground_filter.get("statistics"):
        _write_csv_section(
            writer,
            "Ground Filter (DTM/nDSM estimate) Statistics",
            ["metric", "value"],
            [[k, v] for k, v in ground_filter["statistics"].items()],
        )
        wrote_any = True

    disaster = data.get("disaster_screening")
    if disaster:
        stats = disaster.get("terrain_statistics")
        if stats:
            _write_csv_section(
                writer,
                "Terrain Statistics",
                ["metric", "value"],
                [[k, v] for k, v in stats.items()],
            )
            wrote_any = True

        flood = disaster.get("flood")
        if flood:
            _write_csv_section(
                writer,
                "Flood Screening Summary",
                ["metric", "value"],
                [
                    ["water_level", flood["water_level"]],
                    [
                        "potentially_inundated_pixel_count",
                        flood["potentially_inundated_pixel_count"],
                    ],
                    ["valid_pixel_count", flood["valid_pixel_count"]],
                    ["potentially_inundated_percentage", flood["potentially_inundated_percentage"]],
                    ["potentially_inundated_area", flood["potentially_inundated_area"]],
                    ["area_unit", flood["area_unit"]],
                ],
            )
            wrote_any = True

        landslide = disaster.get("landslide")
        if landslide:
            _write_csv_section(
                writer,
                "Landslide Susceptibility Classes",
                ["class", "pixel_count", "area", "percentage"],
                [
                    [
                        cls,
                        landslide["class_pixel_counts"].get(cls),
                        landslide["class_areas"].get(cls),
                        landslide["class_percentages"].get(cls),
                    ]
                    for cls in landslide["class_pixel_counts"]
                ],
            )
            wrote_any = True

    features = (calibration_residuals or {}).get("features") or []
    if features:
        residual_rows = []
        for feature in features:
            props = feature["properties"]
            lon, lat = feature["geometry"]["coordinates"]
            position = {"longitude": lon, "latitude": lat}
            residual_rows.append(
                [position[c] if c in position else props.get(c) for c in _RESIDUAL_CSV_COLUMNS]
            )
        _write_csv_section(
            writer,
            "Calibration Residual Samples (not measurements; residual = predicted - reference, "
            "in the calibration reference's units; held-out is the validation view, fit is "
            "in-sample)",
            _RESIDUAL_CSV_COLUMNS,
            residual_rows,
        )
        wrote_any = True

    if not wrote_any:
        return None
    return buf.getvalue().encode("utf-8")


def build_bundle_zip(
    data: dict,
    pdf_bytes: bytes,
    csv_bytes: bytes | None,
    artifact_files: list[tuple[str, Path]],
) -> bytes:
    """Zips the real generated report files together with the real,
    already-stored raster artifact files — reads each artifact straight
    from its existing storage location (`artifact_files`); never
    regenerates or duplicates raster data, only archives real bytes that
    already exist. `artifact_files` is a list of (arcname, absolute_path)
    pairs; a path that no longer exists on disk is skipped with a note in
    the manifest rather than silently producing a shorter, unexplained ZIP.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("report.pdf", pdf_bytes)
        zf.writestr("analysis.json", json.dumps(data, indent=2))
        if csv_bytes is not None:
            zf.writestr("measurements.csv", csv_bytes)

        missing: list[str] = []
        for arcname, path in artifact_files:
            if path.is_file():
                zf.write(path, arcname=f"artifacts/{arcname}")
            else:
                missing.append(arcname)
        if missing:
            zf.writestr(
                "artifacts/MISSING.txt",
                "The following artifact files were not found in storage at bundle-generation "
                "time and could not be included:\n" + "\n".join(missing),
            )
    return buf.getvalue()
