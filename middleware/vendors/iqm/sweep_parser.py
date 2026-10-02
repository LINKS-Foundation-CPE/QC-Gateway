"""Decode the machine's binary sweep artifacts into a JSON view.

Why a server-side sidecar rather than parsing in the browser: the wire format
is protobuf owned by the vendor, and IQM ships the parser
(``iqm-data-definitions``). Decoding here keeps that the only parser anyone
has to maintain, and it puts the reduction in one place instead of in every
browser — a results blob is megabytes of samples, and a page needs a few
thousand points.

Two artifacts are decoded:

``sweep_results``  ``station_control.v2.SweepResultsResponse`` — a map from
                   readout label to ``Arrays``, which holds one ``Array`` per
                   sweep spot.
``payload``        ``RunDefinition`` — the swept axes, the readout metadata,
                   and the pulse schedule when the job carries one.

What a real payload contains varies, so nothing here requires any of it:

* ``hard_sweeps`` maps a readout label to its own sweep axes, and is what a
  station-side sweep populates. For a plain measurement job the axis is
  ``repetitions``, so a per-shot array is indexed by shot number.
* ``sweep_definition`` carries the whole-run Cartesian sweep and the playlist,
  and a Pulla measurement job may not set it at all — in which case there is
  no schedule to draw and ``readout_label_to_impl`` in
  ``additional_run_properties`` is what says how each readout was acquired.

The raw bytes are still stored alongside, untouched; this is a derived view,
never a replacement.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from google.protobuf.json_format import MessageToDict

logger = logging.getLogger(__name__)

# Points kept per trace after decimation. A viewport is ~1000 px wide, and
# min/max bucketing gives two points per bucket, so this is roughly one bucket
# per pixel — past that the extra samples cannot be seen.
MAX_TRACE_POINTS = 2048
# A 2-D trace field is budgeted in cells, not in points: it is drawn as a
# heatmap, so the columns per row have to stay wide enough to see structure —
# splitting one trace budget across every row leaves tens of columns.
MAX_ROWS = 128
MAX_FIELD_COLS = 256
MAX_SCHEDULES = 64
# Points per reconstructed waveform envelope.
ENVELOPE_POINTS = 48


def _sequence_values(seq) -> tuple[list[Any], str]:
    """Read a ``Sequence``/``Array`` payload without caring which arm is set."""
    kind = seq.WhichOneof("kind") if hasattr(seq, "WhichOneof") else None
    if kind is None:
        for name in (
            "float64_array",
            "int64_array",
            "complex128_array",
            "bool_array",
            "string_array",
        ):
            arm = getattr(seq, name, None)
            if arm is None:
                continue
            if name == "complex128_array":
                if len(arm.real):
                    kind = name
                    break
            elif len(arm.items):
                kind = name
                break
    if kind is None:
        return [], "empty"
    arm = getattr(seq, kind)
    if kind == "complex128_array":
        return [[r, i] for r, i in zip(arm.real, arm.imag, strict=False)], "complex128"
    dtype = {
        "float64_array": "float64",
        "int64_array": "int64",
        "bool_array": "bool",
        "string_array": "string",
    }[kind]
    return list(arm.items), dtype


def _absmax_decimate(values: list[float], target: int) -> tuple[list[float], int]:
    """Reduce a trace to ~``target`` points, keeping each bucket's peak size.

    For a field drawn as a heatmap, not a line: ``_minmax_decimate`` emits the
    bucket's low *and* high, so neighbouring cells alternate between them and
    the field reads as speckle. One envelope value per bucket is what a colour
    scale can actually show.
    """
    n = len(values)
    if n <= target:
        return [abs(v) for v in values], 1
    width = n / target
    out: list[float] = []
    for b in range(target):
        lo, hi = int(b * width), min(n, int((b + 1) * width))
        if hi <= lo:
            continue
        out.append(max(abs(v) for v in values[lo:hi]))
    return out, int(width)


def _minmax_decimate(values: list[float], target: int) -> tuple[list[float], int]:
    """Reduce a trace to ~``target`` points, keeping each bucket's extremes.

    Plain striding aliases: a trace oscillating at the carrier frequency turns
    into whatever the stride happens to land on, which looks like noise and
    hides the envelope. Taking the min and max of each bucket preserves the
    visible extent of the signal, which is what the eye reads off a trace.
    """
    n = len(values)
    if n <= target:
        return values, 1
    buckets = max(1, target // 2)
    width = n / buckets
    out: list[float] = []
    for b in range(buckets):
        lo, hi = int(b * width), min(n, int((b + 1) * width))
        if hi <= lo:
            continue
        chunk = values[lo:hi]
        a, z = min(chunk), max(chunk)
        # Emitted in sample order so the polyline still reads left to right.
        out.extend([a, z] if chunk.index(a) <= chunk.index(z) else [z, a])
    return out, int(width)


def parse_sweep_results(data: bytes) -> dict[str, Any] | None:
    """Decode ``SweepResultsResponse`` into per-key arrays, reduced for display.

    The v2 message is the one the station sends: a readout label maps to
    ``Arrays``, which holds one ``Array`` **per sweep spot**. The v1 message
    maps the label to a single ``Array``, and because ``Arrays.arrays`` and
    ``Array.shape`` are both field 1, parsing v2 bytes with the v1 class does
    not fail — it reads the repeated sub-messages as a few hundred shape
    entries and finds no data. Anything that looks like that is this mistake.
    """
    from iqm.data_definitions.station_control.v2.task_service_pb2 import SweepResultsResponse

    msg = SweepResultsResponse.FromString(data)
    out: dict[str, Any] = {"sweep_id": msg.sweep_id, "results": {}}
    for key in msg.results:
        spots = list(msg.results[key].arrays)
        if not spots:
            out["results"][key] = {"shape": [], "dtype": "empty", "n": 0, "spots": 0}
            continue

        per_spot = [_sequence_values(a) for a in spots]
        dtype = next((d for _, d in per_spot if d != "empty"), "empty")
        spot_shape = list(spots[0].shape)

        if len(spots) > 1:
            # One 1-D array per spot is the same thing as a 2-D field, and the
            # sweep axis is the spot index.
            shape = [len(spots), *spot_shape]
            values = [v for vals, _ in per_spot for v in vals]
        else:
            shape = spot_shape
            values = per_spot[0][0]
        entry: dict[str, Any] = {
            "shape": shape,
            "dtype": dtype,
            "n": len(values),
            "spots": len(spots),
        }
        # Per-shot thresholded outcomes arrive as float64 zeros and ones. They
        # are not a waveform, and drawing them as one is meaningless: what a
        # reader wants is the proportion and its spread.
        if dtype in ("float64", "int64") and values and set(values) <= {0, 1}:
            entry["binary"] = True
            entry["ones"] = int(sum(values))

        if dtype == "float64" and len(shape) == 2 and shape[0] and shape[1]:
            rows, cols = shape
            row_stride = max(1, math.ceil(rows / MAX_ROWS))
            kept_rows = list(range(0, rows, row_stride))
            per_row = MAX_FIELD_COLS
            field, mins, maxs = [], [], []
            col_stride = 1
            for r in kept_rows:
                seg = values[r * cols : (r + 1) * cols]
                red, col_stride = _absmax_decimate(seg, per_row)
                field.append([round(v, 6) for v in red])
                mins.append(round(min(seg), 6))
                maxs.append(round(max(seg), 6))
            entry.update(
                field=field,
                row_indices=kept_rows,
                row_min=mins,
                row_max=maxs,
                reduced={
                    "rows": f"{rows}->{len(kept_rows)}",
                    "samples": f"{cols}->{len(field[0]) if field else 0}",
                    "method": "max |value| per bucket",
                    "col_stride": col_stride,
                },
            )
        elif dtype == "float64" and len(shape) <= 1:
            red, stride = _minmax_decimate(values, MAX_TRACE_POINTS)
            entry["values"] = [round(v, 6) for v in red]
            if stride > 1:
                entry["reduced"] = {
                    "samples": f"{len(values)}->{len(red)}",
                    "method": "min/max buckets",
                    "stride": stride,
                }
        elif dtype == "complex128":
            entry["values"] = [[round(r, 6), round(i, 6)] for r, i in values]
        else:
            entry["values"] = values[:MAX_TRACE_POINTS]
            if len(values) > MAX_TRACE_POINTS:
                entry["reduced"] = {
                    "samples": f"{len(values)}->{MAX_TRACE_POINTS}",
                    "method": "truncated",
                }
        out["results"][key] = entry
    return out


def _envelope(wf) -> tuple[str, list[float]]:
    """Reconstruct a normalised envelope from a waveform's analytic shape.

    Waveforms in a playlist are almost always a shape plus parameters rather
    than samples, which is why a schedule for a whole sweep is kilobytes. The
    reconstruction is for display only — the station is the authority on what
    it actually plays.
    """
    kind = wf.WhichOneof("waveform_description")
    n = ENVELOPE_POINTS
    xs = [(-0.5 + i / (n - 1)) for i in range(n)]

    if kind == "samples":
        vals = list(wf.samples.samples)
        if not vals:
            return kind or "unknown", [0.0] * n
        red, _ = _minmax_decimate(vals, n)
        peak = max(abs(v) for v in red) or 1.0
        return kind, [round(v / peak, 4) for v in red]
    if kind == "constant":
        return kind, [1.0] * n
    if kind in ("truncated_gaussian", "gaussian"):
        shape = getattr(wf, kind)
        full_width = getattr(shape, "full_width", 0.0) or 1.0
        sigma = getattr(shape, "sigma", 0.0) or full_width / 5.0
        return kind, [round(math.exp(-(x * x) / (2 * sigma * sigma)), 4) for x in xs]
    if kind in (
        "cosine_rise_fall",
        "truncated_gaussian_smoothed_square",
        "gaussian_smoothed_square",
        "modulated_cosine_rise_fall",
    ):
        rise = getattr(getattr(wf, kind), "rise_time", 0.0) or 0.25
        ys = []
        for x in xs:
            a = abs(x)
            if a <= 0.5 - rise:
                ys.append(1.0)
            else:
                ys.append(round(0.5 * (1 + math.cos(math.pi * (a - (0.5 - rise)) / rise)), 4))
        return kind, ys
    # Slepian and the derivative shapes have no short closed form worth
    # duplicating here; a flat block is honest about "a pulse of this length"
    # and the kind is reported so a viewer can say it did not reconstruct it.
    return kind or "unknown", [1.0] * n


_OP_KIND = {
    "iq_pulse": "drive",
    "real_pulse": "flux",
    "readout_trigger": "readout",
    "multiplexed_iq_pulse": "drive",
    "multiplexed_real_pulse": "flux",
    "virtual_rz": "virtual_rz",
    "wait": "wait",
    "conditional_instruction": "conditional",
}


def _probe_waveform(table: Any, idx: int, depth: int = 0) -> tuple[int | None, float]:
    """Follow a ``readout_trigger``'s ``probe_pulse_ref`` to a waveform.

    The reference is an index into the channel's *instruction* table, not its
    waveform table — the readout pulse is a separate instruction the trigger
    points at, usually a ``multiplexed_iq_pulse`` gathering one probe tone per
    qubit on the line, each of whose entries points at an ``iq_pulse``, which
    is what finally names a waveform.

    Returns ``(waveform_ref, scale)``, or ``(None, 1.0)`` when the chain leads
    somewhere with no waveform. ``depth`` stops a malformed playlist that
    references itself.
    """
    if depth > 4 or idx < 0 or idx >= len(table):
        return None, 1.0
    ins = table[idx]
    op = ins.WhichOneof("operation")
    if op == "iq_pulse":
        return ins.iq_pulse.waveform_i_ref, round(ins.iq_pulse.scale_i, 6)
    if op == "real_pulse":
        return ins.real_pulse.waveform_ref, round(ins.real_pulse.scale, 6)
    if op in ("multiplexed_iq_pulse", "multiplexed_real_pulse"):
        # Every entry is one tone of the same probe; they share a shape, so the
        # first one is what to draw.
        entries = getattr(ins, op).entries
        if len(entries):
            return _probe_waveform(table, entries[0].instruction_ref, depth + 1)
    return None, 1.0


def _sweep_definition(msg: Any, data: bytes) -> Any:
    """The ``SweepRequest`` a run definition carries, from either place it lives.

    v1 holds it inline in ``sweep_definition``. v2 added
    ``sweep_definition_payload``, a ``google.protobuf.Any`` wrapping the same
    v1 ``SweepRequest``, and that is what the machine actually sends: both
    payloads examined — one from the production machine, one from the mock —
    carry the playlist there and leave field 12 unset.

    Field 13 does not exist in v1, so a v1 parse drops it silently along with
    the entire pulse schedule. That is the same failure the sweep *results*
    had, and for the same reason: a newer message read with an older
    definition does not fail, it just goes quiet.

    Only the sweep definition is taken from the v2 view. Everything else keeps
    coming from the v1 parse, because v2 retypes ``additional_run_properties``
    from ``google.protobuf.Struct`` to its own ``Struct``, whose
    ``MessageToDict`` yields the wrapped form — ``{"stringValue": ...}`` where
    v1 gives a plain string. Reading it through v2 would change the shape of
    every readout-metadata field in the sidecar.
    """
    from iqm.data_definitions.station_control.v1.sweep_request_pb2 import SweepRequest
    from iqm.data_definitions.station_control.v2.run_definition_pb2 import (
        RunDefinition as RunDefinitionV2,
    )

    sweep = msg.sweep_definition
    if len(sweep.playlist.channels):
        return sweep

    try:
        v2 = RunDefinitionV2.FromString(data)
        if not v2.HasField("sweep_definition_payload"):
            return sweep
        packed = SweepRequest()
        if not v2.sweep_definition_payload.Unpack(packed):
            logger.warning(
                "iqm: sweep_definition_payload holds %s, not a SweepRequest",
                v2.sweep_definition_payload.type_url,
            )
            return sweep
        return packed
    except Exception as e:
        # The v1 view is already complete apart from the schedule; losing the
        # schedule must not cost the axes and the readout metadata too.
        logger.warning("iqm: could not read sweep_definition_payload: %s", e)
        return sweep


def parse_run_definition(data: bytes) -> dict[str, Any] | None:
    """Decode ``RunDefinition``: the swept axes and the pulse schedule."""
    from iqm.data_definitions.station_control.v1.run_definition_pb2 import RunDefinition

    msg = RunDefinition.FromString(data)
    sweep = _sweep_definition(msg, data)
    out: dict[str, Any] = {
        "run_id": msg.run_id,
        "username": msg.username,
        "experiment_name": msg.experiment_name,
        "experiment_label": msg.experiment_label,
        "sweep_id": sweep.sweep_id,
        "components": list(msg.components),
        "return_parameters": list(sweep.return_parameters),
        "axes": [],
        "axes_by_key": {},
        "playlist": None,
    }

    def _axis_group(cartesian):
        group = []
        for parallel in cartesian.parallel_sweeps:
            for single in parallel.single_parameter_sweeps:
                values, dtype = _sequence_values(single.values)
                prm = single.parameter
                group.append(
                    {
                        "parameter_name": single.parameter_name,
                        "label": prm.label or single.parameter_name,
                        "unit": prm.unit,
                        "dtype": dtype,
                        "n": len(values),
                        "values": values[: MAX_ROWS * 4],
                    }
                )
        return group

    # Swept axes, with the parameter's own name and unit rather than an index.
    #
    # Two places carry them and a job may use either. `sweep_definition.sweeps`
    # is the whole-run Cartesian sweep; `hard_sweeps` is per readout label,
    # which is what a station-side sweep uses — for a plain measurement job it
    # is the repetition index, so the axis of a per-shot array is the shot
    # number. Keyed axes are the better answer where they exist, because they
    # say which readout each axis belongs to instead of leaving it to be
    # guessed from a matching length.
    group = _axis_group(sweep.sweeps)
    if group:
        out["axes"].append(group)
    out["axes_by_key"] = {k: _axis_group(msg.hard_sweeps[k]) for k in msg.hard_sweeps}

    # Readout metadata, which is where the dispatch hints live when the payload
    # carries no playlist: `readout_label_to_impl` names each readout's
    # implementation and the *_data_parameters lists say how it was acquired.
    # Its own try/except: MessageToDict raises on a Struct holding an unset
    # Value, and losing the readout metadata must not also lose the axes.
    props: dict[str, Any] = {}
    if len(msg.additional_run_properties.fields):
        try:
            props = MessageToDict(msg.additional_run_properties)
        except Exception as e:
            logger.warning("iqm: could not read additional_run_properties: %s", e)
    for name in (
        "readout_label_to_impl",
        "integration_data_parameters",
        "time_trace_data_parameters",
        "target_data_parameters",
        "qubits",
        "couplers",
        "probe_lines",
        "computational_resonators",
    ):
        if name in props:
            out[name] = props[name]
    out["default_data_parameters"] = list(msg.default_data_parameters)

    out["playlist"] = _parse_playlist(sweep.playlist)
    return out


def _parse_playlist(pl) -> dict[str, Any] | None:
    """Channels, interned instruction/waveform tables, schedules, acquisitions.

    ``None`` when the payload carries no schedule, which a measurement job
    submitted without a sweep definition does not.
    """
    if not len(pl.channels):
        # A payload without a sweep definition carries no schedule at all; the
        # axes and readout metadata above are still worth having.
        return None

    channels: list[dict[str, Any]] = []
    acquisitions: list[dict[str, Any]] = []
    for name in pl.channels:
        ch = pl.channels[name]
        cfg = ch.channel_config.WhichOneof("extended") or "unknown"
        waveforms = []
        for wf in ch.waveform_table:
            kind, env = _envelope(wf)
            waveforms.append({"n_samples": wf.n_samples, "kind": kind, "env": env})
        instructions = []
        for ins in ch.instruction_table:
            op = ins.WhichOneof("operation")
            e: dict[str, Any] = {
                "dur": ins.duration_samples,
                "op": op,
                "kind": _OP_KIND.get(op or "", op or "unknown"),
            }
            if op == "iq_pulse":
                e.update(
                    wf=ins.iq_pulse.waveform_i_ref,
                    scale=round(ins.iq_pulse.scale_i, 6),
                    phase=round(ins.iq_pulse.phase, 6),
                )
            elif op == "real_pulse":
                e.update(wf=ins.real_pulse.waveform_ref, scale=round(ins.real_pulse.scale, 6))
            elif op == "readout_trigger":
                # `probe_pulse_ref` indexes the instruction table, so it is kept
                # as `probe` and the waveform it ends up playing is resolved
                # here. Storing it as `wf` read it as a waveform index: on a
                # channel with fewer waveforms than instructions the readout
                # pulse then drew nothing, and on a channel with more it drew
                # somebody else's shape.
                probe = ins.readout_trigger.probe_pulse_ref
                wf_ref, wf_scale = _probe_waveform(ch.instruction_table, probe)
                e.update(
                    probe=probe,
                    wf=wf_ref,
                    scale=wf_scale,
                    acqs=list(ins.readout_trigger.acqusitions),
                )
            elif op == "virtual_rz":
                e["phase"] = round(ins.virtual_rz.phase_increment, 6)
            instructions.append(e)
        channels.append(
            {
                "name": ch.controller_name or name,
                "config": cfg,
                "sample_rate": getattr(getattr(ch.channel_config, cfg, None), "sample_rate", 0.0)
                if cfg != "unknown"
                else 0.0,
                "waveforms": waveforms,
                "instructions": instructions,
            }
        )
        for acq in ch.acquisition_table:
            acquisitions.append(
                {
                    "channel": ch.controller_name or name,
                    "label": acq.label,
                    "delay_samples": acq.delay_samples,
                    "kind": acq.WhichOneof("acquisition_type") or "unknown",
                }
            )

    total = len(pl.schedules)
    stride = max(1, math.ceil(total / MAX_SCHEDULES))
    kept = list(range(0, total, stride))
    schedules = [
        {n: list(pl.schedules[k].channels[n].instruction_refs) for n in pl.schedules[k].channels}
        for k in kept
    ]

    refs = sum(len(v) for s in schedules for v in s.values())
    return {
        "channels": channels,
        "acquisitions": acquisitions,
        "schedules": schedules,
        "schedule_indices": kept,
        "schedules_total": total,
        # What the interning bought: a viewer can say why the schedule is small.
        "instruction_table_entries": sum(len(c["instructions"]) for c in channels),
        "waveform_table_entries": sum(len(c["waveforms"]) for c in channels),
        "instruction_refs": refs,
    }


def parse_artifact(atype: str, data: bytes) -> dict[str, Any] | None:
    """Dispatch on artifact type. ``None`` means "nothing to add for this one"."""
    try:
        if atype == "sweep_results":
            return parse_sweep_results(data)
        if atype == "payload":
            return parse_run_definition(data)
    except Exception as e:
        # A derived view is best-effort: the raw artifact is already stored, so
        # a parse failure must not cost the job its results.
        logger.warning("iqm: could not parse %s artifact (%d bytes): %s", atype, len(data), e)
    return None
