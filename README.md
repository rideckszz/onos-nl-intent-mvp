
---

# ONOS NL Intent Mediator (onos-nl-intent-mvp)

This repository contains a proof-of-concept mediator that uses a Large Language Model (LLM) to translate natural-language instructions into ONOS intents. The mediator is evaluated in a controlled SDN environment with ONOS and Mininet using synthetic topologies and an automated measurement workflow.

The main goals are:

* To assess whether an LLM-based planner can correctly map high-level requests to ONOS `HostToHostIntent` operations.
* To measure planning and control-plane latency, verification overhead, and success rates across different topologies and operations.
* To cross-check mediator metrics with ONOS’s `metrics` application (intent event rates and timestamps).

The project is aligned with the experimental methodology described in the associated article / report.

---

## Repository Structure

The key directories and files are:

```text
.
├── experiments/
│   ├── dashboard_server.py    # Live dashboard (Flask) for interactive plots
│   ├── offline_plots.py       # Offline PGF plots and tables from CSV + JSON
│   ├── ...                    # Scaling / experiment runner scripts
│   └── plots_offline/         # (Created) PGF plots and LaTeX-ready figures
├── logs/
│   ├── experiments_scaling.csv  # Main CSV log for scaling experiment
│   └── intents_metrics.json     # ONOS metrics app JSON output
├── src/ or mediator modules     # (Depending on your layout) LLM planner client,
│                                # ONOS REST wrapper, and mediator core
├── requirements.txt             # Python dependencies (Flask, pandas, matplotlib, etc.)
└── README.md                    # This file
```

Names of some modules may differ slightly; the structure above reflects the usage in this project.

---

## Experimental Architecture

The environment is organised as follows:

* **Control plane**: ONOS controller (tested with ONOS 2.5.x, built with Bazel).
* **Data plane**: Mininet running on Linux, with:

  * Linear topologies with 3, 6, and 9 hosts (`linear_3`, `linear_6`, `linear_9`).
  * Two-level tree topologies with fanout 2 and 3.
* **Mediator**:

  * Python component that:

    * Receives natural-language instructions.
    * Forwards each instruction to an LLM-based planner (HTTP(S) endpoint).
    * Receives a JSON “plan” with an `operation` field and parameters.
    * Validates, resolves host IDs, and invokes ONOS REST APIs.
    * Verifies the resulting controller state via `/intents` and other endpoints.
* **Metrics collection**:

  * CSV logs from the mediator (`logs/experiments_scaling.csv`).
  * JSON snapshot from the ONOS `metrics` application (`logs/intents_metrics.json`).

---

## Requirements

* Linux (tested on recent Ubuntu).
* Python 3.10+ (repository currently uses a virtual environment such as `.grifoVenv`).
* ONOS 2.x running and reachable from the host running the mediator.
* Mininet installed and able to connect to a remote controller.
* Optionally, ONOS `metrics` app activated to export intent metrics.

Python dependencies (see `requirements.txt`) include:

* `flask`
* `pandas`
* `matplotlib`
* `scienceplots` (for `plt.style.use(["science", "pgf"])`)
* `requests` / `httpx` (for REST and planner calls)

---

## Setup

```bash
git clone <this-repo-url> onos-nl-intent-mvp
cd onos-nl-intent-mvp

python -m venv .venv
source .venv/bin/activate   # or equivalent on your shell

pip install -r requirements.txt
```

Configure environment variables or a config file (depending on your implementation) for:

* ONOS base URL (e.g. `http://127.0.0.1:8181/onos/v1`)
* ONOS credentials (if required)
* Planner / LLM endpoint (e.g. `https://llm.ic.unicamp.br/...`)

---

## Running ONOS and Mininet

1. **Start ONOS** (built with Bazel or from distribution):

   * Ensure REST API is enabled and reachable.
   * Optionally activate apps:

     ```bash
     onos> app activate drivers openflow
     onos> app activate metrics
     ```

2. **Start Mininet** with a chosen topology (example, using the controller as remote):

   ```bash
   sudo mn --topo linear,3 --controller=remote,ip=<ONOS_IP>,port=6653 --link=tc
   ```

   For trees, use the appropriate Mininet options or helper scripts.

3. Run `pingAll` inside Mininet to warm ARP/MAC tables:

   ```bash
   mininet> pingAll
   ```

---

## Running the Scaling Experiment

The scaling experiment:

* Starts Mininet with one of the predefined topologies.
* Waits for ONOS to discover devices and hosts.
* Executes a warm-up phase creating a fixed number of `HostToHostIntent` instances.
* Runs a fixed number of natural-language instructions for each operation class:

  * `connect_hosts`
  * `list_intents`
  * `delete_intents_between_hosts`
  * `delete_all_intents`
* Logs results to `logs/experiments_scaling.csv`.

A typical invocation (adjust to your actual module name) is:

```bash
python -m experiments.<your_scaling_module> \
    --outcsv logs/experiments_scaling.csv
```

The resulting CSV should have columns such as:

```text
topology_label,hosts_count,run_id,prompt,operation,ok,error,total_ms,llm_ms,onos_ms,verify_ms
```

Semantics:

* `topology_label`: e.g. `linear_3`, `linear_6`, `linear_9`, `tree_fanout2`, `tree_fanout3`.
* `hosts_count`: number of hosts in that topology.
* `run_id`: trial index or repetition index.
* `prompt`: actual natural-language instruction sent.
* `operation`: operation returned by the planner (`connect_hosts`, `list_intents`, etc.).
* `ok`: boolean (or 0/1) indicating whether the controller state matches the intended change.
* `error`: textual error description (planner or REST error).
* `total_ms`: end-to-end latency of the instruction.
* `llm_ms`: planning time (LLM / planner).
* `onos_ms`: controller time (REST calls to ONOS).
* `verify_ms`: verification time (extra queries to check final state).

---

## Collecting ONOS Metrics (metrics app)

To correlate mediator metrics with internal ONOS measurements, the `metrics` app is used.

1. Activate the app:

   ```bash
   onos> app activate metrics
   ```

2. After running your experiment, query intent event metrics in JSON:

   ```bash
   onos> intents-events-metrics --json
   ```

3. Copy the JSON output to `logs/intents_metrics.json`. For example:

   ```json
   {
     "intentSubmittedTimestamp": { "value": 1765581614030 },
     "intentSubmittedRate": { "count": 75, "mean_rate": 0.0068516720, ... },
     "intentInstalledTimestamp": { "value": 1765581614042 },
     "intentInstalledRate": { "count": 75, "mean_rate": 0.0068516719, ... },
     ...
   }
   ```

This JSON is consumed by the offline plotting script to produce tables/figures that relate mediator-level metrics to ONOS-internal event rates.

---

## Offline Plots and Tables (PGF + science style)

Once `logs/experiments_scaling.csv` and `logs/intents_metrics.json` are available, you can generate all plots and summary tables offline:

```bash
python -m experiments.offline_plots \
    --csv logs/experiments_scaling.csv \
    --metrics-json logs/intents_metrics.json \
    --outdir experiments/plots_offline
```

The script:

* Uses `plt.style.use(["science", "pgf"])` to generate figures suitable for LaTeX integration.
* Produces `.pgf` figures and possibly `.tex` tables in `experiments/plots_offline`.

Typical outputs (names may vary slightly):

* `success_rate_per_topology.pgf`
* `success_rate_per_operation.pgf`
* `latency_components_per_operation.pgf`
* `latency_distribution_by_operation.pgf`
* `scaling_total_latency_vs_hosts.pgf`
* `scaling_llm_vs_onos_vs_verify.pgf`
* `error_types_distribution.tex` (if error counts are meaningful)
* `onos_intent_metrics_summary.tex` (tables summarising mean rates, counts, and timestamps)

These plots implement the metrics described in the methodology section:

* **Success rate per operation and topology** (correctness).
* **Planning, controller, verification, and total latency** (performance).
* **Scaling behaviour vs. number of hosts/topology type**.
* **Cross-check with ONOS `metrics` app** (intent submitted/installed/withdrawn rates).

You can include a PGF figure in LaTeX as:

```latex
\begin{figure}[ht]
    \centering
    \input{experiments/plots_offline/success_rate_per_topology.pgf}
    \caption{Success rate per topology.}
    \label{fig:success-topology}
\end{figure}
```

---

## Live Dashboard (Optional)

For interactive inspection during an experiment, a small Flask dashboard is available:

```bash
python -m experiments.dashboard_server
```

This:

* Starts a web server on port 5001 (by default).
* Reads the CSV log file periodically.
* Renders static images from matplotlib (PNG) in an HTML dashboard.

Access it in a browser:

```text
http://127.0.0.1:5001
```

This mode is useful for monitoring the experiment while it is running, but the final figures in the paper or report should be generated offline using `offline_plots.py` and PGF.

---

## Reproducibility

* Experiments use a fixed random seed for host selection to ensure reproducible runs.
* All code changes and configurations should be tracked via Git.
* The LaTeX article/report can directly import figures and tables from `experiments/plots_offline`.

For full reproducibility, document:

* ONOS version and build method.
* Mininet version and exact topology commands.
* Planner/LLM endpoint and model version.
* Experiment parameters (number of instructions per operation, warm-up size, etc.).

---

## Citation

If you use this repository as part of an academic work, please reference your article or thesis associated with this project and, if appropriate, this repository as:

> D. G. Andrighetti, “Use of Large Language Models for Intent-Based Configuration in Software-Defined Networks,” Institute of Computing, UNICAMP, 2025.


