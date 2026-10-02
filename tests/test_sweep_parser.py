"""Decoding the machine's binary sweep artifacts into a display view.

Built as real protobuf messages and decoded through IQM's own definitions, so
these exercise the wire format rather than a stand-in for it.
"""

import math

import pytest

pytest.importorskip("iqm.data_definitions", reason="iqm-data-definitions not installed")

from iqm.data_definitions.station_control.v1.run_definition_pb2 import (  # noqa: E402
    RunDefinition,
)
from iqm.data_definitions.station_control.v2.task_service_pb2 import (  # noqa: E402
    SweepResultsResponse,
)

from middleware.vendors.iqm.sweep_parser import (  # noqa: E402
    MAX_TRACE_POINTS,
    parse_artifact,
    parse_run_definition,
    parse_sweep_results,
)


def _results(**arrays):
    """One `Array` per sweep spot, which is how the station sends them.

    Pass a single (shape, values) pair for a one-spot readout, or a list of
    them for several spots.
    """
    msg = SweepResultsResponse(sweep_id="sweep-1")
    for key, spec in arrays.items():
        spots = spec if isinstance(spec, list) else [spec]
        for shape, values in spots:
            a = msg.results[key].arrays.add()
            a.shape.extend(shape)
            if values and isinstance(values[0], complex):
                a.complex128_array.real.extend(v.real for v in values)
                a.complex128_array.imag.extend(v.imag for v in values)
            elif values and isinstance(values[0], int):
                a.int64_array.items.extend(values)
            else:
                a.float64_array.items.extend(values)
    return msg.SerializeToString()


def test_keys_shapes_and_dtypes_survive():
    blob = _results(
        **{
            "QB1__tt": ([4], [0.0, 1.0, -1.0, 0.5]),
            "QB3__integration": ([2], [1 + 2j, 3 + 4j]),
            "QB2__thresholded": ([3], [10, 20, 30]),
        }
    )
    out = parse_sweep_results(blob)

    assert out["sweep_id"] == "sweep-1"
    assert out["results"]["QB1__tt"]["dtype"] == "float64"
    assert out["results"]["QB3__integration"]["dtype"] == "complex128"
    assert out["results"]["QB2__thresholded"]["dtype"] == "int64"
    assert out["results"]["QB1__tt"]["shape"] == [4]


def test_complex_arrives_as_real_imaginary_pairs():
    out = parse_sweep_results(_results(**{"QB3__integration": ([2], [1 + 2j, 3 + 4j])}))
    assert out["results"]["QB3__integration"]["values"] == [[1.0, 2.0], [3.0, 4.0]]


def test_small_trace_is_not_touched():
    vals = [0.1, 0.2, 0.3]
    out = parse_sweep_results(_results(**{"QB1__tt": ([3], vals)}))
    entry = out["results"]["QB1__tt"]
    assert entry["values"] == vals
    assert "reduced" not in entry


def test_long_trace_is_decimated_and_says_so():
    n = MAX_TRACE_POINTS * 4
    vals = [math.sin(i / 3) for i in range(n)]
    out = parse_sweep_results(_results(**{"QB1__tt": ([n], vals)}))
    entry = out["results"]["QB1__tt"]

    assert len(entry["values"]) <= MAX_TRACE_POINTS
    assert entry["reduced"]["method"] == "min/max buckets"
    assert entry["reduced"]["samples"] == f"{n}->{len(entry['values'])}"
    assert entry["shape"] == [n], "the original shape is still reported"


def test_decimation_keeps_the_extremes():
    """Striding would sample the carrier; the envelope has to survive."""
    n = MAX_TRACE_POINTS * 4
    vals = [math.sin(i / 3) for i in range(n)]
    vals[n // 2] = 9.0  # a spike a stride could easily miss
    out = parse_sweep_results(_results(**{"QB1__tt": ([n], vals)}))

    kept = out["results"]["QB1__tt"]["values"]
    assert max(kept) == pytest.approx(9.0)
    assert min(kept) == pytest.approx(min(vals), abs=0.01)


def test_several_spots_become_a_field_reduced_per_row():
    """A readout with one 1-D array per spot is a 2-D field, spot on the y axis."""
    rows, cols = 8, 4096
    spots = [([cols], [float((r + 1) * math.sin(c / 5)) for c in range(cols)]) for r in range(rows)]
    out = parse_sweep_results(_results(**{"PL-1__tt": spots}))
    entry = out["results"]["PL-1__tt"]

    assert len(entry["field"]) == len(entry["row_indices"])
    assert all(len(r) == len(entry["field"][0]) for r in entry["field"])
    assert entry["shape"] == [rows, cols], "the spot count becomes the first axis"
    assert entry["spots"] == rows
    assert entry["reduced"]["method"] == "max |value| per bucket"
    assert all(v >= 0 for row in entry["field"] for v in row), "a field is an envelope"
    # Row extremes are reported from the full row, not the reduced one.
    assert entry["row_max"][-1] == pytest.approx(rows * 1.0, abs=0.01)


def _run_definition():
    rd = RunDefinition(run_id="run-1", username="Pulla User", experiment_name="Pulla")
    sweep = rd.sweep_definition
    sweep.sweep_id = "sweep-1"
    single = sweep.sweeps.parallel_sweeps.add().single_parameter_sweeps.add()
    single.parameter_name = "drive.constant.amplitude"
    single.parameter.label = "drive amplitude"
    single.parameter.unit = "a.u."
    single.values.float64_array.items.extend([0.1, 0.2, 0.3])

    ch = sweep.playlist.channels["QB1__drive"]
    ch.controller_name = "QB1__drive"
    ch.channel_config.iq_channel.sample_rate = 2e9
    wf = ch.waveform_table.add()
    wf.n_samples = 80
    wf.truncated_gaussian.full_width = 0.75
    idle = ch.instruction_table.add()
    idle.duration_samples = 20
    idle.wait.SetInParent()
    pulse = ch.instruction_table.add()
    pulse.duration_samples = 80
    pulse.iq_pulse.waveform_i_ref = 0
    pulse.iq_pulse.scale_i = 0.25
    acq = ch.acquisition_table.add()
    acq.label = "QB1__tt"
    acq.delay_samples = 40
    acq.timetrace.SetInParent()
    for _ in range(3):
        s = sweep.playlist.schedules.add()
        s.channels["QB1__drive"].instruction_refs.extend([0, 1])
    return rd.SerializeToString()


def test_axes_carry_the_parameter_name_and_unit():
    out = parse_run_definition(_run_definition())
    axis = out["axes"][0][0]
    assert axis["parameter_name"] == "drive.constant.amplitude"
    assert axis["label"] == "drive amplitude"
    assert axis["unit"] == "a.u."
    assert axis["values"] == [0.1, 0.2, 0.3]


def test_playlist_channels_instructions_and_envelope():
    out = parse_run_definition(_run_definition())
    p = out["playlist"]
    ch = p["channels"][0]

    assert ch["name"] == "QB1__drive"
    assert ch["config"] == "iq_channel"
    assert ch["sample_rate"] == 2e9
    assert [i["kind"] for i in ch["instructions"]] == ["wait", "drive"]
    assert ch["instructions"][1]["scale"] == 0.25
    wf = ch["waveforms"][0]
    assert wf["kind"] == "truncated_gaussian"
    # A Gaussian peaks in the middle and falls off at both ends.
    assert wf["env"][len(wf["env"]) // 2] == max(wf["env"])
    assert wf["env"][0] < wf["env"][len(wf["env"]) // 2]


def test_acquisitions_name_the_result_keys():
    out = parse_run_definition(_run_definition())
    assert out["playlist"]["acquisitions"] == [
        {"channel": "QB1__drive", "label": "QB1__tt", "delay_samples": 40, "kind": "timetrace"}
    ]


def test_interning_counts_are_reported():
    p = parse_run_definition(_run_definition())["playlist"]
    assert p["schedules_total"] == 3
    assert p["instruction_table_entries"] == 2
    assert p["instruction_refs"] == 6, "3 schedules x 2 refs, resolving to 2 table entries"


def test_unknown_artifact_type_yields_nothing():
    assert parse_artifact("timeline", b"{}") is None


def test_undecodable_bytes_do_not_raise():
    """The raw artifact is already stored; a bad parse must stay a warning."""
    assert parse_artifact("sweep_results", b"\x00\xff not protobuf") is None


def test_per_shot_outcomes_are_flagged_binary():
    """The shape a mock and a real measurement job both produce.

    1000 float64 zeros and ones per readout: per-shot thresholded outcomes,
    not a waveform. A consumer needs to know that before it draws a line.
    """
    shots = [0.0, 1.0, 1.0, 0.0, 1.0]
    out = parse_sweep_results(_results(**{"QB3__meas_3_1_0": ([len(shots)], shots)}))
    entry = out["results"]["QB3__meas_3_1_0"]

    assert entry["binary"] is True
    assert entry["ones"] == 3
    assert entry["spots"] == 1
    assert entry["dtype"] == "float64"


def test_a_real_trace_is_not_flagged_binary():
    vals = [0.0, 0.31, -0.2, 1.0]
    out = parse_sweep_results(_results(**{"QB1__tt": ([4], vals)}))
    assert "binary" not in out["results"]["QB1__tt"]


def test_v1_shaped_bytes_are_not_silently_misread():
    """The mistake this parser was built with, pinned.

    `Arrays.arrays` and `Array.shape` are both field 1, so v2 bytes parsed as
    v1 do not fail — they yield hundreds of shape entries and no data. Parsing
    v2 bytes with the v2 class must give the real shape instead.
    """
    out = parse_sweep_results(_results(**{"QB3__meas_3_1_0": ([1000], [0.0] * 1000)}))
    entry = out["results"]["QB3__meas_3_1_0"]
    assert entry["shape"] == [1000]
    assert len(entry["shape"]) == 1, "a shape of hundreds of entries is the v1/v2 mix-up"
    assert entry["dtype"] == "float64"


def test_axes_come_from_hard_sweeps_when_there_is_no_sweep_definition():
    """A Pulla measurement job: no sweep definition, axes per readout label."""
    from iqm.data_definitions.station_control.v1.run_definition_pb2 import RunDefinition

    rd = RunDefinition(run_id="run-2", username="Pulla User", experiment_name="Pulla")
    rd.components.extend(["QB1", "QB2"])
    single = rd.hard_sweeps["QB3__meas_3_1_0"].parallel_sweeps.add().single_parameter_sweeps.add()
    single.parameter_name = "repetitions"
    single.values.int64_array.items.extend(range(4))
    rd.default_data_parameters.extend(["QB3__meas_3_1_0"])
    rd.additional_run_properties.update(
        {"readout_label_to_impl": {"QB3__meas_3_1_0": "measure_fidelity.constant"}}
    )

    out = parse_run_definition(rd.SerializeToString())

    assert out["playlist"] is None, "no sweep definition means no schedule"
    assert out["axes"] == [], "the whole-run sweep is empty"
    axis = out["axes_by_key"]["QB3__meas_3_1_0"][0]
    assert axis["parameter_name"] == "repetitions"
    assert axis["n"] == 4
    assert out["readout_label_to_impl"] == {"QB3__meas_3_1_0": "measure_fidelity.constant"}
    assert out["default_data_parameters"] == ["QB3__meas_3_1_0"]


def _sweep_request_bytes():
    """The v1 ``SweepRequest`` the machine packs into the v2 field, on its own."""
    from iqm.data_definitions.station_control.v1.sweep_request_pb2 import SweepRequest

    rd = RunDefinition()
    rd.ParseFromString(_run_definition())
    sr = SweepRequest()
    sr.CopyFrom(rd.sweep_definition)
    return sr


def _run_definition_v2_with_packed_sweep():
    """What the machine actually sends: the schedule inside ``Any`` field 13.

    Both payloads examined — one from the production machine, one from the
    mock — have ``sweep_definition`` (field 12) unset and carry the whole
    ``SweepRequest`` here instead.
    """
    from iqm.data_definitions.station_control.v2.run_definition_pb2 import (
        RunDefinition as RunDefinitionV2,
    )

    v2 = RunDefinitionV2(run_id="run-3", username="Pulla User", experiment_name="Pulla")
    v2.sweep_definition_payload.Pack(_sweep_request_bytes(), type_url_prefix="iqm-data-definitions")
    return v2.SerializeToString()


def test_playlist_is_found_in_the_packed_sweep_definition():
    out = parse_run_definition(_run_definition_v2_with_packed_sweep())

    assert out["sweep_id"] == "sweep-1"
    p = out["playlist"]
    assert p is not None, "the schedule is in sweep_definition_payload, not field 12"
    assert [c["name"] for c in p["channels"]] == ["QB1__drive"]
    assert len(p["channels"][0]["instructions"]) == 2
    assert len(p["schedules"]) == 3


def test_a_v1_parse_of_those_bytes_finds_no_schedule():
    """The regression this pins: v1 has no field 13, so it drops the playlist.

    It does not fail — a newer message read with an older definition goes
    quiet, which is exactly how this reached production unnoticed.
    """
    from iqm.data_definitions.station_control.v1.run_definition_pb2 import (
        RunDefinition as RunDefinitionV1,
    )

    data = _run_definition_v2_with_packed_sweep()
    v1 = RunDefinitionV1.FromString(data)

    assert not len(v1.sweep_definition.playlist.channels)
    assert not v1.sweep_definition.sweep_id


def test_readout_metadata_keeps_its_plain_values_on_a_v2_payload():
    """Why only the sweep definition is read through v2.

    v2 retypes ``additional_run_properties`` to its own ``Struct``, whose
    ``MessageToDict`` yields ``{"stringValue": ...}`` where v1 gives a plain
    string. Everything but the sweep definition therefore stays on the v1
    parse, and the sidecar keeps the shape the viewer reads.
    """
    from iqm.data_definitions.station_control.v2.run_definition_pb2 import (
        RunDefinition as RunDefinitionV2,
    )

    v2 = RunDefinitionV2(run_id="run-4", username="Pulla User", experiment_name="Pulla")
    v2.sweep_definition_payload.Pack(_sweep_request_bytes(), type_url_prefix="iqm-data-definitions")
    props = v2.additional_run_properties.fields
    props["readout_label_to_impl"].struct_value.fields["QB1__M"].string_value = "measure.constant"
    props["qubits"].list_value.values.add().string_value = "QB1"

    out = parse_run_definition(v2.SerializeToString())

    assert out["readout_label_to_impl"] == {"QB1__M": "measure.constant"}
    assert out["qubits"] == ["QB1"]
    assert out["playlist"] is not None


def _readout_channel(probe_kind):
    """A readout channel whose trigger points at ``probe_kind``.

    ``probe_pulse_ref`` indexes the *instruction* table. A real playlist
    usually puts a ``multiplexed_iq_pulse`` there — one probe tone per qubit on
    the line — and each of its entries points at the ``iq_pulse`` that finally
    names a waveform.
    """
    rd = RunDefinition(run_id="run-ro", username="Pulla User", experiment_name="Pulla")
    ch = rd.sweep_definition.playlist.channels["PL-1__readout"]
    ch.controller_name = "PL-1__readout"
    ch.channel_config.ro_channel.sample_rate = 2e9

    wf = ch.waveform_table.add()  # 0
    wf.n_samples = 2400
    wf.constant.SetInParent()

    wait = ch.instruction_table.add()  # 0
    wait.duration_samples = 600480
    wait.wait.SetInParent()

    probe = ch.instruction_table.add()  # 1
    probe.duration_samples = 2400
    probe.iq_pulse.waveform_i_ref = 0
    probe.iq_pulse.scale_i = 0.1

    mux = ch.instruction_table.add()  # 2
    mux.duration_samples = 2432
    for _ in range(3):
        mux.multiplexed_iq_pulse.entries.add().instruction_ref = 1

    trig = ch.instruction_table.add()  # 3
    trig.duration_samples = 3544
    trig.readout_trigger.probe_pulse_ref = {"mux": 2, "direct": 1, "dangling": 9}[probe_kind]
    trig.readout_trigger.acqusitions.extend([0, 1, 2])

    s = rd.sweep_definition.playlist.schedules.add()
    s.channels["PL-1__readout"].instruction_refs.extend([0, 3])
    return rd.SerializeToString()


def _trigger(data):
    ch = parse_run_definition(data)["playlist"]["channels"][0]
    return next(i for i in ch["instructions"] if i["op"] == "readout_trigger"), ch


def test_the_readout_probe_resolves_through_a_multiplexed_pulse():
    trig, ch = _trigger(_readout_channel("mux"))

    assert trig["probe"] == 2, "the instruction the trigger points at is kept as-is"
    assert trig["wf"] == 0, "and the waveform it ends up playing is resolved"
    assert trig["scale"] == 0.1
    assert trig["wf"] < len(ch["waveforms"]), "a waveform index that does not exist draws nothing"


def test_the_readout_probe_resolves_when_it_is_a_plain_pulse():
    trig, _ = _trigger(_readout_channel("direct"))

    assert trig["probe"] == 1
    assert trig["wf"] == 0
    assert trig["scale"] == 0.1


def test_a_probe_reference_that_leads_nowhere_yields_no_waveform():
    trig, _ = _trigger(_readout_channel("dangling"))

    assert trig["probe"] == 9
    assert trig["wf"] is None, "better nothing drawn than somebody else's shape"
