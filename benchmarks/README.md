# PyAnsys DPF smoke benchmark

Create the environment from a Miniforge Prompt:

```cmd
mamba env create -f environment.yml -y
mamba activate cassy-rst-bench
python benchmarks/dpf_smoke.py --repeat 2
```

The benchmark uses PyAnsys DPF's example result, downloaded and cached by DPF. It does not require MAPDL, but it does require network access the first time the fixture is downloaded. The fixture covers stress, structural temperature, material IDs, units, and multiple result sets; it has no named selections.

`--repeat` runs each check using the same connection to an already running DPF server. `run_benchmark(server)` requires an existing server object. These benchmark scripts only connect; they do not start, restart or shut down MAPDL or DPF. (The `cassy-fatigue run` CLI and the opt-in pytest integration can start and shut down their own DPF server when no endpoint is given.) If a service is unavailable, start it with the corresponding command below.

The user can start DPF in a separate activated Miniforge Prompt and leave it open. For the network installation, `pushd` maps the UNC path to a temporary drive letter; CMD cannot launch the DPF batch file from a UNC working directory:

```cmd
pushd "%AWP_ROOT242%"
python -i -c "import os; from ansys.dpf import core as dpf; config = dpf.ServerConfig(protocol='gRPC', legacy=True, grpc_mode='insecure'); server = dpf.start_local_server(ansys_path=os.getcwd(), port=50054, config=config); print(server)"
```

Keep the prompt and its temporary drive mapping open while using DPF. Only run `popd` after the user has finished with the server.

These commands explicitly use plaintext legacy gRPC for the configured local Ansys 2024 R2 installation. `LegacyGrpcServer` alone still defaults to mTLS with current PyDPF, so `grpc_mode='insecure'` is required for this older server. Do not expose plaintext gRPC on an untrusted network. The DPF default endpoint is `127.0.0.1:50054`; use `--dpf-ip`/`--dpf-port` or `DPF_IP`/`DPF_PORT` to select another endpoint. Confirm the actual port printed at startup. Files must be accessible on the DPF server machine.

If startup reports missing certificates, check the port before launching again: the process may already be running. In the existing Python prompt, reconnect without restarting:

```python
config = dpf.ServerConfig(protocol="gRPC", legacy=True, grpc_mode="insecure")
server = dpf.connect_to_server("127.0.0.1", 50054, as_global=False, config=config)
print(server)
```

## Local MAPDL fixture

MAPDL sessions are user-owned, just like DPF. The services have separate endpoints and both must remain available when needed. The MAPDL default endpoint is `127.0.0.1:50052`; supply `--ip` and `--port` for another endpoint.

From a Miniforge Prompt, the user can start an R2 session using the configured network installation:

```cmd
mamba activate cassy-rst-bench
python -i -c "import os; from pathlib import Path; from ansys.mapdl.core import launch_mapdl; mapdl = launch_mapdl(exec_file=str(Path(os.environ['AWP_ROOT242']) / 'ansys/bin/winx64/ANSYS242.exe'), port=50052, nproc=1, additional_switches='-smp', cleanup_on_exit=False)"
```

Keep that prompt open. Generation clears the connected model and changes its working directory/job name. Obtain the user's explicit consent before running:

```cmd
python benchmarks/mapdl_fixture.py --allow-clear
```

The output directory must also be accessible to the MAPDL server (the default assumes a local server). Generation refuses to overwrite an existing result. The session remains open after generation.

The generated result is tracked in Git as `benchmarks/fixtures/two_materials.rst`, so a fresh clone does not need MAPDL. Generation writes to the Git-ignored `benchmarks/generated/two_materials/`; copy the new `.rst` over the tracked fixture only when the fixture definition changes. To verify the tracked fixture (or another result via `--result`) without contacting MAPDL:

```cmd
python benchmarks/mapdl_fixture.py --verify-only
```

The fixture has 18 nodes, four SOLID185 elements, two materials, two states, and six shared nodes in the `INTERFACE` selection. Checks compare all stress components and BFE temperatures against analytical expectations, including material-local nodal averaging at the interface. Other generated solver files (`.db`, `.esav`, `.full`, ...) are ignored by Git; only the `.rst` fixture is tracked.

## Remaining local reader checks

Use the existing editable environment and server:

```cmd
C:\F4EMiniforge3\envs\cassy-rst-bench\python.exe benchmarks\phase0_reader_checks.py benchmarks\fixtures\two_materials.rst --check all
set CASSY_DPF_INTEGRATION=1
C:\F4EMiniforge3\envs\cassy-rst-bench\python.exe -m pytest tests\test_server_policy.py tests\test_phase0_integration.py -q
```

Ordinary pytest runs skip live integration; offline policy tests never connect.
The live test has a 120-second timeout and failures propagate rather than returning
success-shaped JSON. It checks both load-step/substep mappings, every temperature
value and nodal ID, and actual PyVista geometry/connectivity.

Native `mesh.grid` stalls while importing the full VTK module set here. The fixture
check explicitly constructs its four eight-node hexahedra from DPF data instead;
it does not certify general native DPF plotting. Thermal `.rth`, cluster deployment
and production performance remain unverified.

## Two-state fatigue workflow

From the repository root, use the existing environment's editable CASSY installation
and the saved fixture (no `PYTHONPATH` override required):

```cmd
C:\F4EMiniforge3\envs\cassy-rst-bench\python.exe -m cassy_fatigue run benchmarks\two_state.yaml --output benchmarks\generated\fatigue
```

This writes VTU, configuration and provenance files. The fixture's solver
materials and P/Q tags are artificial: its fatigue material mappings are examples,
not a real engineering assessment. Output files are protected; use `--overwrite`
only when you intend to replace them.

The production reader now uses the DPF `mesh_to_pyvista` server operator directly.
That general mesh conversion is verified on the fixture and avoids the stalled
full-VTK import; it is not restricted to the hand-built hexahedral reader check.
See the [package README](../README.md) for configuration and output semantics.

The [fatigue example](two_state.yaml) defines two fatigue events (sums of P/Q loads)
with separate cycle counts. Outputs report `Vj_pulse`, `Vj_secondary` and `U_total`; at
interfaces the per-event fields use the material governing total usage.
To exercise named-selection material assignment with this same fixture, replace
the numeric keys under `materials` with `MATERIAL_1` and `MATERIAL_2`, respectively.
Both are elemental selections that together cover the complete mesh.